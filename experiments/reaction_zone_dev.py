"""The user's reaction-zone method, made mechanical. Built on 2022 (flat), checked on 2023-2026.

    .venv/bin/python reaction_zone_dev.py dev | check

Selection rule (fixed before running): best net at $0.20 on 2022 with >= 100 trades, longs and shorts both
profitable before costs.
"""
import json
import sys

import pandas as pd

from common import ROOT
from flat_year import show
from intraday_20 import run

RULES = {"__rules__": {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 2, "stop_after_wins": 1}}
BASE = {**RULES, "tp_usd": 10.0, "sl_mode": "none", "zones": "sweep+fvg", "sessions": "12:30-16:00", "flat_hour": 20}
F = "reaction_zone.py"
IDEAS = {
    "1 sweeps only, TP $10, no SL, NY": (F, {**BASE, "zones": "sweep"}),
    "2 sweeps+FVG, TP $10, no SL, NY": (F, BASE),
    "3 sweeps+FVG, TP $10, no SL, London+NY": (F, {**BASE, "sessions": "07:00-10:00,12:30-16:00"}),
    "4 sweeps+FVG, TP $10, SL beyond wick, NY": (F, {**BASE, "sl_mode": "wick"}),
    "5 sweeps+FVG, TP $30, no SL, NY": (F, {**BASE, "tp_usd": 30.0}),
}
OUT = ROOT / "results" / "reaction_zone"


def dev():
    df = run(IDEAS, 2022)
    df.to_csv(OUT / "dev_2022.csv", index=False)
    show(df)
    zero = df[df.costs == "zero cost"].set_index("variant")
    std = df[df.costs == "Standard $0.20"].set_index("variant")
    ok = std[(std.trades >= 100) & (zero.long_net > 0) & (zero.short_net > 0)].sort_values("net", ascending=False)
    print("\npassing the screen:", list(ok.index) or "none")
    if not ok.empty:
        pick = ok.index[0]
        (OUT / "selected.json").write_text(json.dumps({"name": pick, "file": F, "params": IDEAS[pick][1]}, indent=2))
        print("selected:", pick)


def check():
    sel = json.loads((OUT / "selected.json").read_text())
    df = pd.concat([run({sel["name"]: (F, sel["params"])}, y) for y in (2023, 2024, 2025, 2026)])
    df.to_csv(OUT / "check.csv", index=False)
    show(df)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"dev": dev, "check": check}[sys.argv[1]]()
