"""Held 1:3 and 1:5 variants of the user's framework on 2019 (2019 was used once before, for the $10-target version)."""
import json

import pandas as pd

from common import ROOT, run_grid
from rr_framework import COSTS, RULES

if __name__ == "__main__":
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    v = {f"1:{rr}, held until target or stop-out": {**base, "tp_usd": 10.0 * rr, "flat_hour": 24, "__rules__": RULES}
         for rr in (3, 5)}
    df = run_grid("reaction_zone.py", v, 2019, costs=COSTS)
    df.to_csv(ROOT / "results" / "rr_framework" / "y2019.csv", index=False)
    print(df[["variant", "trades", "net", "Q1", "Q2", "Q3", "Q4", "win_rate", "max_dd", "avg_hold_h"]].to_string(index=False))
