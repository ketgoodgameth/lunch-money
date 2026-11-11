"""Small incremental indicators: update with one value at a time, O(1) per update."""
from __future__ import annotations

from collections import deque


class EMA:
    def __init__(self, period: int):
        self.period, self.k = period, 2.0 / (period + 1)
        self.value: float | None = None
        self.count = 0

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.value + self.k * (x - self.value)
        self.count += 1
        return self.value

    @property
    def ready(self) -> bool:
        return self.count >= self.period


class SMA:
    def __init__(self, period: int):
        self.period = period
        self.window: deque[float] = deque(maxlen=period)
        self.total = 0.0

    def update(self, x: float) -> float:
        if len(self.window) == self.period:
            self.total -= self.window[0]
        self.window.append(x)
        self.total += x
        return self.value

    @property
    def value(self) -> float | None:
        return self.total / len(self.window) if self.window else None

    @property
    def ready(self) -> bool:
        return len(self.window) == self.period


class ATR:
    """Wilder's average true range from bars."""

    def __init__(self, period: int = 14):
        self.period = period
        self.value: float | None = None
        self.prev_close: float | None = None
        self.count = 0

    def update(self, bar) -> float | None:
        tr = bar.high - bar.low if self.prev_close is None else max(
            bar.high - bar.low, abs(bar.high - self.prev_close), abs(bar.low - self.prev_close))
        self.prev_close = bar.close
        self.count += 1
        if self.value is None:
            self.value = tr
        else:
            self.value += (tr - self.value) / self.period
        return self.value

    @property
    def ready(self) -> bool:
        return self.count >= self.period


class RSI:
    """Wilder's RSI (the standard one in MT5/TradingView): SMA seed, then Wilder smoothing."""

    def __init__(self, period: int = 14):
        self.period = period
        self.prev: float | None = None
        self.avg_gain = self.avg_loss = 0.0
        self.count = 0  # number of price changes seen
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.prev is None:
            self.prev = x
            return None
        ch = x - self.prev
        self.prev = x
        gain, loss = max(ch, 0.0), max(-ch, 0.0)
        self.count += 1
        n = self.period
        if self.count <= n:
            self.avg_gain += gain / n
            self.avg_loss += loss / n
            if self.count < n:
                return None
        else:
            self.avg_gain = (self.avg_gain * (n - 1) + gain) / n
            self.avg_loss = (self.avg_loss * (n - 1) + loss) / n
        if self.avg_loss == 0:
            self.value = 100.0 if self.avg_gain > 0 else 50.0
        else:
            self.value = 100.0 - 100.0 / (1.0 + self.avg_gain / self.avg_loss)
        return self.value

    @property
    def ready(self) -> bool:
        return self.count >= self.period


class MACD:
    """Standard MACD: EMA(fast) - EMA(slow), signal = EMA(signal) of that, histogram = macd - signal."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast, self.slow, self.sig = EMA(fast), EMA(slow), EMA(signal)
        self.macd = self.signal = self.hist = None
        self.prev_hist: float | None = None

    def update(self, x: float) -> float:
        self.macd = self.fast.update(x) - self.slow.update(x)
        self.signal = self.sig.update(self.macd)
        self.prev_hist, self.hist = self.hist, self.macd - self.signal
        return self.hist

    @property
    def ready(self) -> bool:
        # slow EMA needs `slow` values, then the signal EMA needs `signal` MACD values on top
        return self.slow.count >= self.slow.period + self.sig.period - 1


class SuperTrend:
    """SuperTrend on bars (TradingView/MT5 definition, Wilder ATR). direction: +1 up (green), -1 down (red)."""

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self.atr, self.mult = ATR(period), multiplier
        self.upper = self.lower = None
        self.direction = 1
        self.value: float | None = None
        self.prev_close: float | None = None
        self.flipped = False  # direction changed on the latest bar

    def update(self, bar) -> float | None:
        atr = self.atr.update(bar)
        hl2 = (bar.high + bar.low) / 2
        bu, bl = hl2 + self.mult * atr, hl2 - self.mult * atr
        pc = self.prev_close
        if self.upper is None:
            self.upper, self.lower = bu, bl
        else:
            self.upper = bu if (bu < self.upper or pc > self.upper) else self.upper
            self.lower = bl if (bl > self.lower or pc < self.lower) else self.lower
        before = self.direction
        if self.direction == 1 and bar.close < self.lower:
            self.direction = -1
        elif self.direction == -1 and bar.close > self.upper:
            self.direction = 1
        self.flipped = self.direction != before
        self.prev_close = bar.close
        self.value = self.lower if self.direction == 1 else self.upper
        return self.value

    @property
    def ready(self) -> bool:
        return self.atr.ready
