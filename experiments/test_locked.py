"""Run the versions locked in results/locked_for_<year>.json once on that year.

    .venv/bin/python test_locked.py 2020
"""
import json
import sys

import numpy as np
import pandas as pd

from common import COSTS, ROOT, run_grid, year_file

if __name__ == "__main__":
    year = int(sys.argv[1])
    f = year_file(year)
    raw = pd.read_csv(f, usecols=["timestamp", "bidPrice"], dtype={"timestamp": np.int64, "bidPrice": np.float64})
    t = pd.to_datetime(raw.timestamp, unit="ms", utc=True)
    d = pd.Series(1, index=t).resample("D").count(); wd = d[d.index.day_of_week < 5]
    print(f"{year} data: {len(raw):,} ticks, {t.iloc[0]} -> {t.iloc[-1]}, out-of-order {int((raw.timestamp.diff() < 0).sum())}, "
          f"gold {raw.bidPrice.min():,.0f}-{raw.bidPrice.max():,.0f} ({raw.bidPrice.iloc[-1] / raw.bidPrice.iloc[0] - 1:+.1%}), "
          f"weekdays without ticks: {[x.strftime('%m-%d') for x in wd[wd == 0].index]}")
    lock = json.loads((ROOT / "results" / f"locked_for_{year}.json").read_text())
    extra = lock.get("broker", {})
    costs = {"zero cost": {**COSTS["zero cost"], **extra}, "$0.20": {"fixed_spread": 0.20, "slippage": 0.0, **extra},
             "$0.20 + $0.10 slippage": {"fixed_spread": 0.20, "slippage": 0.10, **extra}}
    df = pd.concat([run_grid(tt["file"], {name: tt["params"]}, year, costs=costs) for name, tt in lock["tests"].items()])
    df.to_csv(ROOT / "results" / f"test_{year}.csv", index=False)
    pd.set_option("display.width", 250)
    print(df[["variant", "costs", "trades", "net", "Q1", "Q2", "Q3", "Q4", "q_positive", "win_rate", "avg_R",
              "tstat", "max_dd", "long_net", "short_net"]].to_string(index=False))
    std = df[df.costs == "$0.20"].set_index("variant").net
    for name in lock["tests"]:
        print(f"{name}: {'PASS' if std[name] > 0 else 'FAIL'} ({std[name]:+.2f} at $0.20)")
