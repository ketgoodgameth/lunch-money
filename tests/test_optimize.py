from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from xautest.cli import main, parse_grid
from xautest.feeds.synthetic import generate
from xautest.models import EquityPoint, RunResult, Trade
from xautest.optimize import DAY_MS, grid, metrics

ROOT = Path(__file__).resolve().parents[1]


def test_grid_and_parse():
    spec = parse_grid(["a=1,2", "b=0.5,x"])
    assert spec == {"a": [1, 2], "b": [0.5, "x"]}
    assert len(grid(spec)) == 4


def test_metrics_count_days_without_trades():
    d0 = 20_000
    t = Trade(1, 1, 0.1, d0 * DAY_MS + 1, 3000, d0 * DAY_MS + 2, 3001, 10.0, 0.0, 0.0, "tp")
    res = RunResult(trades=[t], equity=[EquityPoint(0, 10_000, 10_000), EquityPoint(1, 10_010, 10_010)],
                    initial_balance=10_000)
    m = metrics(res, market_days=np.array([d0, d0 + 1, d0 + 2, d0 + 3]))
    assert m["days"] == 4 and m["avg_day"] == pytest.approx(2.5) and m["net"] == 10.0


def test_optimize_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)  # strategy paths are relative to the project
    df = generate(datetime(2025, 3, 3, tzinfo=timezone.utc), datetime(2025, 3, 15, tzinfo=timezone.utc), seed=4)
    data = tmp_path / "t.csv"
    df.to_csv(data, index=False)
    out = tmp_path / "opt"
    main(["optimize", "--strategy", "strategies/liquidity_sweep_v2.py", "--source", "csv", "--data", str(data),
          "--grid", "be_trigger_points=1.5,1000", "--grid", "atr_buffer_mult=1.0,2.0",
          "--train-end", "2025-03-10", "--risk-per-trade", "10", "--leverage", "unlimited", "--zero-spread",
          "--commission", "0", "--min-trades", "0", "--workers", "2", "--out", str(out)])
    res = pd.read_csv(out / "all_combinations.csv")
    assert len(res) == 4
    assert {"train_net", "test_net", "train_sharpe", "test_trades"} <= set(res.columns)
    text = capsys.readouterr().out
    assert "rank correlation" in text and "baseline" in text
