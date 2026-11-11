"""Stress the New York breakout: slippage on every fill and Dukascopy's real (wider, spiky) spread."""
import json
import sys

import pandas as pd

from common import ROOT, run_grid

COSTS = {
    "$0.20 spread, no slippage": {"fixed_spread": 0.20, "slippage": 0.0},
    "$0.20 spread + $0.10 slippage": {"fixed_spread": 0.20, "slippage": 0.10},
    "$0.20 spread + $0.30 slippage": {"fixed_spread": 0.20, "slippage": 0.30},
    "Dukascopy real spread (~$0.68)": {"slippage": 0.0},
}

if __name__ == "__main__":
    sel = json.loads((ROOT / "results" / "flat_year" / "selected.json").read_text())
    df = pd.concat([run_grid(sel["file"], {sel["name"]: sel["params"]}, y, costs=COSTS) for y in (2022, 2023, 2024, 2025)])
    df.to_csv(ROOT / "results" / "flat_year" / "stress.csv", index=False)
    pd.set_option("display.width", 220)
    t = df.pivot(index="costs", columns="year", values="net")[[2022, 2023, 2024, 2025]].loc[list(COSTS)]
    print(t.assign(total=t.sum(axis=1)).round(2).to_string())
