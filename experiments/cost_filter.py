"""Cost-based volatility filter: skip trades whose stop is too tight for costs to stay <= 3% of risk.

Thresholds come from costs, not from fitting: Standard $0.20/oz -> $6.67; Raw A $0.12/oz -> $4.00.
Applied unchanged to two existing strategies, on every year:
  A  1m liquidity sweep (v2) + 1H trend + trail 0.5R
  B  trend pullback, 5m candles, lookback 8, stop >= 2xATR (selected on 2025)

    .venv/bin/python experiments/cost_filter.py
"""
import sys

import pandas as pd

from common import ROOT, run_grid, show

TRAIL = {"exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 0.5}
FILTERS = {"no filter": 0.0, "stops >= $6.67 (Standard 3%)": 0.20 / 0.03, "stops >= $4.00 (Raw A 3%)": 0.12 / 0.03}
STRATEGIES = {
    "A 1m sweep + 1H trend": ("liquidity_sweep_exits.py", {"tf": "1min", "trend": "h1_supertrend", **TRAIL}),
    "B trend pullback 5m": ("trend_pullback.py", {"tf": "5min", "lookback": 8, "min_stop_atr": 2.0,
                                                  "buffer_atr": 0.2, "max_stop": 10.0,
                                                  "trail_start_r": 1.0, "trail_dist_r": 0.5}),
}
YEARS = [2022, 2023, 2024, 2025]
OUT = ROOT / "results" / "cost_filter"


def main(years=YEARS):
    frames = []
    for sname, (sfile, params) in STRATEGIES.items():
        variants = {f"{sname} | {fname}": {**params, "__rules__": {"min_stop": ms}} for fname, ms in FILTERS.items()}
        for y in years:
            frames.append(run_grid(sfile, variants, y))
            pd.concat(frames).to_csv(OUT / "all.csv", index=False)
    df = pd.concat(frames)
    show(df)
    pd.set_option("display.width", 250)
    for c in ("Standard $0.20", "Raw A $0.05+$3.5/side", "zero cost"):
        print(f"\n== {c}: net by year ==")
        print(df[df.costs == c].pivot(index="variant", columns="year", values="net").to_string())
    print(f"\nresults: {OUT / 'all.csv'}")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    main([int(y) for y in sys.argv[1:]] or YEARS)
