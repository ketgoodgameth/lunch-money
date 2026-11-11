"""One-time test of the versions locked in results/locked_for_2021.json on 2021 data."""
import json

import pandas as pd

from common import COSTS, ROOT, run_grid

if __name__ == "__main__":
    lock = json.loads((ROOT / "results" / "locked_for_2021.json").read_text())
    costs = {"zero cost": COSTS["zero cost"], "$0.20": {"fixed_spread": 0.20, "slippage": 0.0},
             "$0.20 + $0.10 slippage": {"fixed_spread": 0.20, "slippage": 0.10}}
    frames = [run_grid(t["file"], {name: t["params"]}, 2021, costs=costs) for name, t in lock["tests"].items()]
    df = pd.concat(frames)
    df.to_csv(ROOT / "results" / "test_2021.csv", index=False)
    pd.set_option("display.width", 250)
    print(df[["variant", "costs", "trades", "net", "Q1", "Q2", "Q3", "Q4", "q_positive", "win_rate", "avg_R",
              "tstat", "max_dd", "long_net", "short_net"]].to_string(index=False))
    std = df[df.costs == "$0.20"].set_index("variant").net
    for name in lock["tests"]:
        print(f"{name}: {'PASS' if std[name] > 0 else 'FAIL'} ({std[name]:+.2f} at $0.20)")
