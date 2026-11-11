"""The user's risk framework on the reaction-zone entries: the account IS the stop loss.

$10 account per trade (stop-out at $0 with negative balance protection = -$10 max), 0.01 lots, no stop loss,
take profit at 1:3 / 1:5 / 1:8 ($30 / $50 / $80 per ounce = +$30 / +$50 / +$80).
Each closed by 20:00 UTC, or held until the target or the stop-out ("hold").
Entries: the locked reaction-zone candidate, unchanged. Years 2020-2026 at $0.20.
"""
import json

import numpy as np
import pandas as pd

from common import ROOT, run_grid

YEARS = (2020, 2021, 2022, 2023, 2024, 2025, 2026)
COSTS = {"$0.20": {"fixed_spread": 0.20, "slippage": 0.0, "daily_account": 10.0}}
RULES = {"fixed_lots": 0.01, "max_daily_loss": 10.0, "max_trades": 2, "stop_after_wins": 1}

if __name__ == "__main__":
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    variants = {}
    for rr in (3, 5, 8):
        for hold, flat in (("closed by 20:00 UTC", 20), ("held until target or stop-out", 24)):
            variants[f"1:{rr}, {hold}"] = {**base, "tp_usd": 10.0 * rr, "flat_hour": flat, "__rules__": RULES}
    out = ROOT / "results" / "rr_framework"
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for y in YEARS:
        frames.append(run_grid("reaction_zone.py", variants, y, costs=COSTS))
        pd.concat(frames).to_csv(out / "all.csv", index=False)
    df = pd.concat(frames)
    pd.set_option("display.width", 250)
    t = df.pivot(index="variant", columns="year", values="net")[list(YEARS)].loc[list(variants)]
    n = df.groupby("variant").trades.sum()
    w = df.assign(wins=df.trades * df.win_rate / 100).groupby("variant").wins.sum()
    print("== net at $0.20 by year ($10 account per trade, 0.01 lots, no stop loss) ==")
    print(t.assign(total=t.sum(axis=1), years_positive=(t > 0).sum(axis=1), trades=n,
                   win_rate=(100 * w / n).round(1)).round(0).to_string())
    print("\nbreak-even win rate: 1:3 25%, 1:5 17%, 1:8 11%")
