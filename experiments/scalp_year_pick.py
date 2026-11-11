"""Which year is the most stable one to build a scalper on? Measured before any scalping test is run.

    .venv/bin/python scalp_year_pick.py

Per year, from the Dukascopy ticks: daily range in % of price (median and its coefficient of variation across
days: lower = steadier), the spread, the trend share (|year's move| / sum of |daily moves|: 0 = all back-and-forth,
1 = one straight line), and 1-minute noise. Rule fixed in advance: the year with the lowest day-to-day variation
of the daily range (CV), among years with a trend share below 0.25, is the build year.
"""
import numpy as np
import pandas as pd

from common import YEAR_FILES, cache_for, year_file

rows = []
for y in sorted(YEAR_FILES):
    try:
        c = cache_for(year_file(y))
    except FileNotFoundError:
        continue
    ts = np.load(c / "ts.npy", mmap_mode="r"); bid = np.load(c / "bid.npy", mmap_mode="r"); ask = np.load(c / "ask.npy", mmap_mode="r")
    s = pd.Series(np.asarray(bid), index=pd.to_datetime(np.asarray(ts), unit="ms", utc=True))
    d = s.resample("1D").ohlc().dropna()
    d = d[d.index.dayofweek < 5]
    rng = (d.high - d.low) / d.close * 100
    m1 = s.resample("1min").last().dropna()
    r1 = m1.pct_change().abs().dropna() * 100
    move = d.close.diff().abs().sum()
    sp = np.asarray(ask[::50]) - np.asarray(bid[::50])
    rows.append({"year": y, "days": len(d), "range_pct": rng.median(), "range_cv": rng.std() / rng.mean(),
                 "range_usd": (d.high - d.low).median(), "trend_share": abs(d.close.iloc[-1] - d.open.iloc[0]) / move,
                 "m1_move_pct": r1.median(), "spread_med": np.median(sp), "spread_p90": np.quantile(sp, 0.9),
                 "ticks_per_day": len(ts) / len(d)})
df = pd.DataFrame(rows).set_index("year").round(4)
print(df.to_string())
ok = df[df.trend_share < 0.25]
print("\nbuild year:", ok.range_cv.idxmin() if len(ok) else df.range_cv.idxmin())
