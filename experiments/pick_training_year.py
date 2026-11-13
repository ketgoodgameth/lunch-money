"""Pick the training year for a new strategy: a stable year, chosen at random, with newer years left to test on.

    .venv/bin/python pick_training_year.py

Rule fixed before the table was computed (2026-11-13):
  stable    the year's net change is at most 10% of the price AND the day-to-day variation of the daily range
            (its coefficient of variation) is at or below the median of all years
  eligible  stable and at least 4 newer years exist (so 2022 or earlier)
  pick      one eligible year at random, numpy default_rng(20261113)
The newer years are the test; the older years are a second, weaker check.
"""
import json

import numpy as np
import pandas as pd

from common import ROOT, YEAR_FILES, cache_for, year_file

SEED = 20261113

rows = []
for y in sorted(YEAR_FILES):
    c = cache_for(year_file(y))
    ts = np.load(c / "ts.npy", mmap_mode="r"); bid = np.load(c / "bid.npy", mmap_mode="r")
    s = pd.Series(np.asarray(bid), index=pd.to_datetime(np.asarray(ts), unit="ms", utc=True))
    d = s.resample("1D").ohlc().dropna()
    d = d[d.index.dayofweek < 5]
    rng = (d.high - d.low) / d.close * 100
    rows.append({"year": y, "open": d.open.iloc[0], "close": d.close.iloc[-1],
                 "change_pct": (d.close.iloc[-1] / d.open.iloc[0] - 1) * 100,
                 "range_pct": rng.median(), "range_usd": (d.high - d.low).median(), "range_cv": rng.std() / rng.mean(),
                 "trend_share": abs(d.close.iloc[-1] - d.open.iloc[0]) / d.close.diff().abs().sum()})
df = pd.DataFrame(rows).set_index("year")
cv_med = df.range_cv.median()
df["stable"] = (df.change_pct.abs() <= 10) & (df.range_cv <= cv_med)
df["eligible"] = df.stable & (df.index <= max(df.index) - 4)
pd.set_option("display.width", 200)
print(df.round(3).to_string())
elig = sorted(df[df.eligible].index)
pick = int(np.random.default_rng(SEED).choice(elig))
print(f"\nmedian range CV {cv_med:.3f}; eligible: {elig}; picked at random (seed {SEED}): {pick}")
print(f"test years (newer): {[int(y) for y in df.index if y > pick]}; older years (second check): {[int(y) for y in df.index if y < pick]}")
(ROOT / "results" / "training_year.json").write_text(json.dumps(
    {"rule": "net change <= 10% and range CV <= median; at least 4 newer years; random pick", "seed": SEED,
     "eligible": [int(y) for y in elig], "picked": pick}, indent=2))
