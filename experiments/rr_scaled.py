"""The user's 1:3 / 1:5 held framework with volatility-scaled position size, 2019-2026 at $0.20 ($10 account).
Same versions as locked in results/locked_for_2018.json; fixed 0.01-lot versions shown for comparison."""
import json

import pandas as pd

from common import ROOT, run_grid
from rr_framework import RULES as FIXED_RULES

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
COSTS = {"$0.20": {"fixed_spread": 0.20, "slippage": 0.0, "daily_account": 10.0}}

if __name__ == "__main__":
    lock = json.loads((ROOT / "results" / "locked_for_2018.json").read_text())
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    variants = {name: t["params"] for name, t in lock["tests"].items()}
    for rr in (3, 5):
        variants[f"1:{rr} held, fixed 0.01 lots"] = {**base, "tp_usd": 10.0 * rr, "flat_hour": 24, "__rules__": FIXED_RULES}
    out = ROOT / "results" / "rr_scaled"
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for y in YEARS:
        frames.append(run_grid("reaction_zone.py", variants, y, costs=COSTS))
        pd.concat(frames).to_csv(out / "all.csv", index=False)
    df = pd.concat(frames)
    pd.set_option("display.width", 250)
    t = df.pivot(index="variant", columns="year", values="net")[list(YEARS)].loc[list(variants)]
    n = df.groupby("variant").trades.sum()
    w = df.assign(w=df.trades * df.win_rate / 100).groupby("variant").w.sum()
    print(t.assign(total=t.sum(axis=1), years_positive=(t > 0).sum(axis=1), trades=n, win_rate=(100 * w / n).round(1)).round(0).to_string())
    print("\nworst drawdown by variant:", df.groupby("variant").max_dd.min().round(0).to_dict())
