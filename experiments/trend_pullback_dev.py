"""Develop the trend-pullback strategy on 2025, then check the pre-selected variant on other years.

    .venv/bin/python experiments/trend_pullback_dev.py dev          # 8 variants on 2025, picks one
    .venv/bin/python experiments/trend_pullback_dev.py check 2022   # the picked variant on another year

Selection rule (fixed before running): best net at Standard $0.20 on 2025 among variants with >= 150 trades.
"""
import json
import sys
from pathlib import Path

from common import ROOT, run_grid, show

BASE = {"trail_start_r": 1.0, "trail_dist_r": 0.5, "buffer_atr": 0.2, "max_stop": 10.0}
VARIANTS = {f"{tf} lb{lb} floor{fl:g}xATR": {**BASE, "tf": tf, "lookback": lb, "min_stop_atr": fl}
            for tf in ("5min", "15min") for lb in (8, 16) for fl in (1.0, 2.0)}
OUT = ROOT / "results" / "trend_pullback"


def dev():
    df = run_grid("trend_pullback.py", VARIANTS, 2025)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "dev_2025.csv", index=False)
    show(df)
    std = df[(df.costs == "Standard $0.20") & (df.trades >= 150)].sort_values("net", ascending=False)
    if std.empty:
        print("\nno variant reached 150 trades; nothing selected")
        return
    pick = std.iloc[0].variant
    (OUT / "selected.json").write_text(json.dumps({"variant": pick, "params": VARIANTS[pick]}, indent=2))
    print(f"\nselected by the pre-declared rule: {pick}")


def check(year: int):
    sel = json.loads((OUT / "selected.json").read_text())
    df = run_grid("trend_pullback.py", {sel["variant"]: sel["params"]}, year)
    df.to_csv(OUT / f"check_{year}.csv", index=False)
    print(df.to_string(index=False))


if __name__ == "__main__":
    dev() if sys.argv[1] == "dev" else check(int(sys.argv[2]))
