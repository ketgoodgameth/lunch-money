"""How much do tick-level signals predict, before any trading costs? (Step 1 of the HFT experiment.)

    .venv/bin/python hft_signals.py [year]        # default 2021, the build year

For every tick in 01:00-20:00 UTC it measures a signal from the ticks just before it and the mid-price move just
after it (entered 100 ms later, like the simulated broker). Signals:
  burst   mid price moved at least X $/oz over the last W seconds
  run     K mid-price changes in a row in the same direction
Events are thinned so two events are at least 60 s apart. "follow" is the average move in the signal's
direction over the next H seconds, in $/oz; negative means price came back (a fade would have earned it).
A round trip costs the spread: $0.20/oz on Exness Standard, so an edge has to beat that to be tradeable.
"""
import sys

import numpy as np
import pandas as pd

from common import cache_for, year_file
from xautest.feeds.arrays import load_cache

H = 3_600_000
LATENCY = 100
HORIZONS = {"1s": 1_000, "5s": 5_000, "15s": 15_000, "60s": 60_000, "300s": 300_000}
BURSTS = {1_000: (0.2, 0.4, 0.8), 5_000: (0.4, 0.8, 1.5), 30_000: (1.0, 2.0, 4.0)}
RUNS = (5, 8, 12)
GAP = 60_000
SESSIONS = {"all 01-20": (1, 20), "active 07-16": (7, 16), "quiet 01-06": (1, 6)}


def thin(idx: np.ndarray, ts: np.ndarray) -> np.ndarray:
    keep, last = [], -GAP
    for i, t in zip(idx.tolist(), ts[idx].tolist()):
        if t - last >= GAP:
            keep.append(i); last = t
    return np.asarray(keep, dtype=np.int64)


def study(year: int) -> pd.DataFrame:
    a = load_cache(cache_for(year_file(year)))
    ts = np.asarray(a["ts"])
    mid = (np.asarray(a["bid"]) + np.asarray(a["ask"])) * 0.5
    n = len(ts)
    hour = (ts % 86_400_000) // H
    i_in = np.minimum(np.searchsorted(ts, ts + LATENCY, "left"), n - 1)
    fwd = {}
    for name, h in HORIZONS.items():
        i_out = np.minimum(np.searchsorted(ts, ts + LATENCY + h, "left"), n - 1)
        f = mid[i_out] - mid[i_in]
        f[(ts[i_out] - ts) > h + 60_000] = np.nan          # a gap in the data (weekend, break)
        fwd[name] = f
    signals = {}
    for w, thrs in BURSTS.items():
        past = mid - mid[np.maximum(np.searchsorted(ts, ts - w, "right") - 1, 0)]
        for x in thrs:
            signals[f"burst ${x:g} in {w // 1000}s"] = np.where(np.abs(past) >= x, np.sign(past), 0.0)
    d = np.sign(np.diff(mid, prepend=mid[0]))
    moved = d != 0
    dm = d[moved]
    start = np.r_[True, dm[1:] != dm[:-1]]
    pos = np.arange(len(dm))
    run_len = pos - np.maximum.accumulate(np.where(start, pos, 0)) + 1
    run = np.zeros(n); run[moved] = run_len * dm
    for k in RUNS:
        signals[f"run of {k} ticks"] = np.where(moved & (np.abs(run) == k), np.sign(run), 0.0)
    rows = []
    for sname, (h0, h1) in SESSIONS.items():
        ok = (hour >= h0) & (hour < h1)
        for name, sig in signals.items():
            ev = thin(np.flatnonzero((sig != 0) & ok), ts)
            row = {"session": sname, "signal": name, "events": len(ev), "per_day": round(len(ev) / 258, 1)}
            for hn, f in fwd.items():
                x = f[ev] * sig[ev]
                x = x[~np.isnan(x)]
                row[f"follow {hn}"] = round(float(x.mean()), 3) if len(x) else np.nan
                if hn in ("5s", "60s"):
                    row[f"t {hn}"] = round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 1) if len(x) > 2 else np.nan
            rows.append(row)
    sp = np.asarray(a["ask"]) - np.asarray(a["bid"])
    inh = (hour >= 1) & (hour < 20)
    gaps = np.diff(ts[inh])
    print(f"{year}: {n:,} ticks; 01-20 UTC: {inh.sum():,} ticks, median gap {np.median(gaps):.0f} ms, "
          f"data spread median ${np.median(sp[inh]):.3f} (mean ${sp[inh].mean():.3f}); "
          f"typical |mid move| over 1s ${np.nanmean(np.abs(fwd['1s'][inh])):.3f}, 5s ${np.nanmean(np.abs(fwd['5s'][inh])):.3f}, "
          f"60s ${np.nanmean(np.abs(fwd['60s'][inh])):.3f}, 300s ${np.nanmean(np.abs(fwd['300s'][inh])):.3f}")
    return pd.DataFrame(rows)


if __name__ == "__main__":
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    print(study(year).to_string(index=False))
