"""Re-run the best candidates at a given spread on a given year, by quarter.

    .venv/bin/python experiments/best_at_spread.py DATA.csv 0.20 2025 [name filter]

The optional name filter runs only candidates whose name contains it (keeps a holdout year clean).
"""
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xautest import optimize as opt                      # noqa: E402
from xautest.feeds import CSVTickFeed                    # noqa: E402
from xautest.feeds.arrays import build_cache, cache_dir_for  # noqa: E402
from xautest.models import dt_to_ms                      # noqa: E402

TRAIL = {"exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 0.5}
CANDIDATES = {
    "1m sweep + 1H trend (R1)": {"tf": "1min", "trend": "h1_supertrend", **TRAIL},
    "5m sweep + trail 0.5R (R1)": {"tf": "5min", **TRAIL},
}
RISK = {"risk_per_trade": 10.0, "max_daily_loss": 20.0, "max_trades": 2, "day_start_hour": 0.0,
        "stop_after_wins": 1, "stop_after_losses": 0}
BROKER = {"commission_per_lot": 0.0, "slippage": 0.0, "latency_ms": 100, "leverage": float("inf"),
          "daily_account": 20.0}


def main(data: str, spread: float, year: int, only: str = "", workers: int = 8):
    key = {"source": "csv", "data": data, "files": [data], "fmt": "generic", "tz": None, "start": "None",
           "end": "None", "symbol": "XAUUSD", "scale": None}
    cache = cache_dir_for(ROOT / ".cache" / "ticks", key)
    if not (cache / "ok").exists():
        build_cache(CSVTickFeed(data), cache)
    q = [datetime(year, m, 1, tzinfo=timezone.utc) for m in (1, 4, 7, 10)] + [datetime(year + 1, 1, 1, tzinfo=timezone.utc)]
    quarters = [(dt_to_ms(a), dt_to_ms(b)) for a, b in zip(q, q[1:])]
    spreads = {"zero spread": {"zero_spread": True}, f"spread ${spread:.2f}": {"fixed_spread": spread}}
    jobs, labels = [], []
    for cname, params in CANDIDATES.items():
        if only and only not in cname:
            continue
        for sname, sover in spreads.items():
            for qi, w in enumerate(quarters, 1):
                jobs.append((params, w, sover)); labels.append((cname, sname, qi))
    res = opt.run_jobs(jobs, cache, str(ROOT / "strategies" / "liquidity_sweep_exits.py"), {}, BROKER, RISK, workers)
    agg = {}
    for (c, s, qi), m in zip(labels, res):
        a = agg.setdefault((c, s), {"daily": {}, "pnl": [], "q": {}})
        a["daily"].update(m["_daily"]); a["pnl"].extend(m["_pnl"]); a["q"][f"Q{qi}"] = m["net"]
    rows = []
    for (c, s), a in agg.items():
        d = pd.Series(a["daily"]); p = np.array(a["pnl"]); sd = d.std(ddof=1); eq = d.sort_index().cumsum()
        rows.append({"candidate": c, "spread": s, "trades": len(p), "net": round(p.sum(), 2),
                     **{k: round(v, 2) for k, v in a["q"].items()},
                     "win_rate": round((p > 0).mean() * 100, 1), "avg_R": round(p.mean() / 10, 3),
                     "tstat": round(d.mean() / (sd / math.sqrt(len(d))), 2) if sd > 0 else float("nan"),
                     "max_dd": round(float((eq - eq.cummax()).min()), 2)})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(f"{year}:"); print(df.to_string(index=False))
    out = ROOT / "results" / f"best_{year}_spread_{spread:.2f}.csv"
    df.to_csv(out, index=False)
    print(f"saved {out}")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
