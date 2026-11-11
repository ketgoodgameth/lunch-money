"""Incremental indicators checked against independent batch implementations."""
import numpy as np
import pandas as pd
import pytest

from xautest.indicators import MACD, RSI, SuperTrend
from xautest.models import Bar


def random_bars(n=400, seed=5):
    rng = np.random.default_rng(seed)
    close = 3000 + np.cumsum(rng.normal(0, 2, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.exponential(1, n)
    low = np.minimum(open_, close) - rng.exponential(1, n)
    return [Bar(i * 60_000, o, h, l, c) for i, (o, h, l, c) in enumerate(zip(open_, high, low, close))]


def test_macd_matches_pandas_ewm():
    bars = random_bars()
    s = pd.Series([b.close for b in bars])
    macd = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    m = MACD()
    out = [(m.update(x), m.macd, m.signal) for x in s]
    assert np.allclose([o[1] for o in out], macd) and np.allclose([o[2] for o in out], sig)
    assert np.allclose([o[0] for o in out], macd - sig)


def test_rsi_matches_wilder_reference():
    closes = [b.close for b in random_bars()]
    n = 14
    d = np.diff(closes)
    g, l = np.maximum(d, 0), np.maximum(-d, 0)
    ag, al = g[:n].mean(), l[:n].mean()
    ref = [100 - 100 / (1 + ag / al)]
    for i in range(n, len(d)):
        ag, al = (ag * (n - 1) + g[i]) / n, (al * (n - 1) + l[i]) / n
        ref.append(100 - 100 / (1 + ag / al))
    r = RSI(n)
    vals = [r.update(x) for x in closes]
    assert vals[:n] == [None] * n
    assert np.allclose(vals[n:], ref)


def supertrend_reference(bars, period=10, mult=3.0):
    """Transcription of the TradingView Pine definition (ta.supertrend), Wilder ATR seeded with first TR."""
    h = np.array([b.high for b in bars]); l = np.array([b.low for b in bars]); c = np.array([b.close for b in bars])
    tr = np.r_[h[0] - l[0], np.maximum.reduce([h[1:] - l[1:], abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])])]
    atr = np.empty(len(tr)); atr[0] = tr[0]
    for i in range(1, len(tr)):
        atr[i] = atr[i - 1] + (tr[i] - atr[i - 1]) / period
    up, dn, dirs, vals = None, None, [], []
    d = 1
    for i in range(len(bars)):
        hl2 = (h[i] + l[i]) / 2
        bu, bl = hl2 + mult * atr[i], hl2 - mult * atr[i]
        if up is None:
            up, dn = bu, bl
        else:
            up = bu if (bu < up or c[i - 1] > up) else up
            dn = bl if (bl > dn or c[i - 1] < dn) else dn
        if d == 1 and c[i] < dn:
            d = -1
        elif d == -1 and c[i] > up:
            d = 1
        dirs.append(d); vals.append(dn if d == 1 else up)
    return dirs, vals


def test_supertrend_matches_reference_and_flips():
    bars = random_bars(600, seed=11)
    st = SuperTrend(10, 3.0)
    got = [(st.update(b), st.direction) for b in bars]
    dirs, vals = supertrend_reference(bars)
    assert [g[1] for g in got] == dirs
    assert np.allclose([g[0] for g in got], vals)
    assert 1 in dirs and -1 in dirs  # the random walk produced both regimes


def test_supertrend_turns_red_on_a_crash():
    st = SuperTrend(10, 3.0)
    for i in range(30):  # steady uptrend
        p = 3000 + i
        st.update(Bar(i, p - 0.5, p + 0.5, p - 0.7, p + 0.4))
    assert st.direction == 1
    st.update(Bar(31, 3029, 3029, 3000, 3001))  # big red bar through the lower band
    assert st.direction == -1 and st.flipped
