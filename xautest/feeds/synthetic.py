"""SYNTHETIC ticks for smoke tests and demos only. Not market data; never judge a strategy on it."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..models import dt_to_ms


def generate(start: datetime, end: datetime, price: float = 2650.0, seed: int = 7,
             vol_annual: float = 0.16) -> pd.DataFrame:
    """Random-walk bid/ask ticks with gold-like hours (closed Fri 22:00 - Sun 23:00 UTC and a daily
    1-hour break), uneven tick arrival and a spread that widens in quiet hours."""
    rng = np.random.default_rng(seed)
    t, end_ms = dt_to_ms(start), dt_to_ms(end)
    ts, bids, asks = [], [], []
    sigma_per_s = vol_annual / np.sqrt(252 * 23 * 3600)
    while t < end_ms:
        dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        closed = (dt.weekday() == 4 and dt.hour >= 22) or dt.weekday() == 5 or \
                 (dt.weekday() == 6 and dt.hour < 23) or dt.hour == 22
        if closed:
            t = dt_to_ms((dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
            continue
        busy = 13 <= dt.hour < 17
        rate = 4.0 if busy else 1.2  # ticks per second
        gap = rng.exponential(1 / rate)
        price *= np.exp(sigma_per_s * np.sqrt(gap) * rng.standard_normal())
        spread = round(max(0.08, rng.normal(0.18 if busy else 0.32, 0.04)), 3)
        t += max(1, int(gap * 1000))
        ts.append(t)
        bid = round(price - spread / 2, 3)
        bids.append(bid)
        asks.append(round(bid + spread, 3))
    return pd.DataFrame({"ts_ms": ts, "bid": bids, "ask": asks})
