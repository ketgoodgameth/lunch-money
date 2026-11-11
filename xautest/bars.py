"""Builds time bars from ticks (bid prices).

A bar is emitted when the first tick of the NEXT bar arrives, which is exactly when a live
system would know it is complete. Backtest and live therefore see bars at the same moment.
"""
from __future__ import annotations

import re

from .models import Bar, Tick

_UNITS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "d": 86400}


def parse_timeframe(tf: str | int) -> int:
    """'5min' / '5m' / '1h' / '30s' / 300 -> milliseconds."""
    if isinstance(tf, int):
        return tf * 1000
    m = re.fullmatch(r"\s*(\d+)\s*([a-zA-Z]+)\s*", tf)
    if not m or m.group(2).lower() not in _UNITS:
        raise ValueError(f"bad timeframe {tf!r}; use e.g. 30s, 1min, 5m, 1h, 1d")
    return int(m.group(1)) * _UNITS[m.group(2).lower()] * 1000


class BarBuilder:
    def __init__(self, timeframe: str | int):
        self.ms = parse_timeframe(timeframe)
        self.current: Bar | None = None

    def update(self, tick: Tick) -> Bar | None:
        start = tick.ts - tick.ts % self.ms
        cur = self.current
        if cur is not None and start == cur.ts:
            p = tick.bid
            if p > cur.high:
                cur.high = p
            elif p < cur.low:
                cur.low = p
            cur.close = p
            cur.ticks += 1
            cur.spread = tick.ask - tick.bid
            return None
        self.current = Bar(start, tick.bid, tick.bid, tick.bid, tick.bid, 1, tick.ask - tick.bid)
        return cur
