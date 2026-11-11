"""The one-time 2026 test of the version locked in results/flat_year/locked_for_2026.json."""
import json

import pandas as pd

from common import COSTS, ROOT, run_grid

if __name__ == "__main__":
    lock = json.loads((ROOT / "results" / "flat_year" / "locked_for_2026.json").read_text())
    f, p = lock["primary"]["file"], lock["primary"]["params"]
    costs = {"zero cost": COSTS["zero cost"], "$0.20 spread": {"fixed_spread": 0.20, "slippage": 0.0},
             "$0.20 spread + $0.10 slippage": {"fixed_spread": 0.20, "slippage": 0.10}}
    variants = {"PRIMARY (locked)": p,
                "secondary: New York 08:30 local": {**p, **lock["secondary_for_information"]["changes"]}}
    df = run_grid(f, variants, 2026, costs=costs)
    df.to_csv(ROOT / "results" / "flat_year" / "test_2026.csv", index=False)
    pd.set_option("display.width", 250)
    print(df[["variant", "costs", "trades", "net", "Q1", "Q2", "Q3", "Q4", "win_rate", "avg_R", "tstat", "max_dd",
              "long_net", "short_net"]].to_string(index=False))
