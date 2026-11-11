"""Robustness map for the reaction-zone candidate (wick >= 30%) across 2021-2026 at $0.20.

    .venv/bin/python rz_grid.py run | analyse
Settings: wick 25/30/35/40% x take profit $7.5/$10/$12.5/$15 x swing lookback 12/20/32.
"""
import json
import sys

import pandas as pd

from common import ROOT, run_grid

WICKS, TPS, LBS = [0.25, 0.30, 0.35, 0.40], [7.5, 10.0, 12.5, 15.0], [12, 20, 32]
YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
COSTS = {"$0.20": {"fixed_spread": 0.20, "slippage": 0.0}}
OUT = ROOT / "results" / "rz_grid"


def run():
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    variants = {f"wick {w:.0%} | TP ${tp:g} | lb {lb}": {**base, "min_wick": w, "tp_usd": tp, "swing_lookback": lb}
                for w in WICKS for tp in TPS for lb in LBS}
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for y in YEARS:
        frames.append(run_grid("reaction_zone.py", variants, y, costs=COSTS))
        pd.concat(frames).to_csv(OUT / "grid.csv", index=False)


def analyse():
    df = pd.read_csv(OUT / "grid.csv")
    t = df.pivot(index="variant", columns="year", values="net")
    t["total"] = t.sum(axis=1); t["years_positive"] = (t[list(YEARS)] > 0).sum(axis=1)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 100)
    print(t.sort_values("total", ascending=False).round(0).to_string())
    print(f"\npositive 6-year total: {(t.total > 0).sum()} of {len(t)}; positive in >= 5 of 6 years: "
          f"{(t.years_positive >= 5).sum()}; median 6-year total ${t.total.median():.0f}")
    for col, vals in (("wick", WICKS), ("TP", TPS), ("lb", LBS)):
        key = t.index.to_series().str.extract({"wick": r"wick (\d+)%", "TP": r"TP \$([\d.]+)", "lb": r"lb (\d+)"}[col])[0]
        print(f"average 6-year total by {col}: " + ", ".join(f"{k}: ${v:.0f}" for k, v in t.total.groupby(key).mean().items()))


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
