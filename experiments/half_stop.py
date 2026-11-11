"""New York breakout with the stop part-way into the range (stop_frac), 2022-2026.

Primary (fixed before running): stop_frac 0.5. Neighbours 0.4 and 0.6 check for a plateau; 1.0 is the
original. Note: 2026 was already used once for the original version of this strategy.
"""
import json

import pandas as pd

from common import ROOT, run_grid

YEARS = (2022, 2023, 2024, 2025, 2026)
COSTS = {"$0.20": {"fixed_spread": 0.20, "slippage": 0.0},
         "$0.20 + $0.10 slippage": {"fixed_spread": 0.20, "slippage": 0.10}}

if __name__ == "__main__":
    lock = json.loads((ROOT / "results" / "flat_year" / "locked_for_2026.json").read_text())
    f, p = lock["primary"]["file"], lock["primary"]["params"]
    variants = {f"stop at {fr:g} of range{' (PRIMARY)' if fr == 0.5 else ' (original)' if fr == 1.0 else ''}":
                {**p, "stop_frac": fr} for fr in (0.4, 0.5, 0.6, 1.0)}
    df = pd.concat([run_grid(f, variants, y, costs=COSTS) for y in YEARS])
    out = ROOT / "results" / "half_stop"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "all.csv", index=False)
    pd.set_option("display.width", 250)
    for c in COSTS:
        d = df[df.costs == c]
        t = d.pivot(index="variant", columns="year", values="net")[list(YEARS)].loc[list(variants)]
        n = d.pivot(index="variant", columns="year", values="trades")[list(YEARS)].loc[list(variants)]
        print(f"\n== net, {c} ==")
        print(t.assign(total=t.sum(axis=1), years_positive=(t > 0).sum(axis=1)).round(2).to_string())
        print(f"-- trades, {c}"); print(n.assign(total=n.sum(axis=1)).to_string())
    d = df[df.costs == "$0.20"]
    print("\n== per trade (R), longs/shorts at $0.20 ==")
    print(d[["year", "variant", "avg_R", "win_rate", "tstat", "max_dd", "long_net", "short_net"]]
          .sort_values(["variant", "year"]).to_string(index=False))
