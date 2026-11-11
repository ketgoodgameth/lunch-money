import sys
from pathlib import Path

import pytest

from xautest import BrokerConfig, SimBroker
from xautest.models import BUY, SELL, Bar, Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from scalper import Scalper  # noqa: E402

DAY0 = 20_400 * 86_400_000
H, M = 3_600_000, 60_000


def make(**params):
    s = Scalper(**{"sessions": "00:00-23:59", "flat_hour": 24, "atr_len": 3, "ema": 3, "slow": 5, **params})
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0, latency_ms=0))
    s.broker.on_tick(Tick(DAY0 + 8 * H, 2000.0, 2000.2))
    s.on_start()
    return s


def feed(s, closes, rng=1.0, start=8 * H):
    """Feed 1-minute bars with the given closes (high/low +-rng/2); returns the last tick."""
    t = None
    for i, c in enumerate(closes):
        ts = DAY0 + start + i * M
        t = Tick(ts + M, c, c + 0.2)
        s.broker.on_tick(t)
        s.on_signal_bar(Bar(ts, c, c + rng / 2, c - rng / 2, c), t)
    return t


def test_fade_sells_a_stretch_above_the_average():
    s = make(mode="fade", k=0.8, tp_atr=1.0, sl_atr=1.5)
    feed(s, [2000.0] * 8)
    assert not s.positions
    feed(s, [2004.0], start=8 * H + 8 * M)            # close $2 above the EMA, ATR ~$2.2 -> 0.9 ATR stretch
    (p,) = s.positions
    assert p.side == SELL
    assert p.tp < p.entry_price < p.sl
    assert p.entry_price - p.tp == pytest.approx(s.atr.value * 1.0, rel=1e-6)


def test_breakout_buys_a_new_high_in_an_uptrend():
    s = make(mode="breakout", lookback=5, tp_atr=1.0, sl_atr=1.0)
    feed(s, [2000.0 + 0.2 * i for i in range(10)] + [2004.0])
    (p,) = s.positions
    assert p.side == BUY and p.sl < p.entry_price < p.tp


def test_pullback_needs_trend_and_rsi_turn():
    s = make(mode="pullback", rsi_len=3, rsi_level=30.0, ema=5, slow=20)
    feed(s, [2000.0 + i for i in range(25)] + [2023.0, 2021.0, 2019.5])            # uptrend, then a dip
    assert not s.positions
    feed(s, [2020.5], start=8 * H + 28 * M)                                          # RSI turns back up
    (p,) = s.positions
    assert p.side == BUY


def test_spread_too_big_for_the_target_is_skipped():
    s = make(mode="fade", k=0.8, tp_atr=0.1, max_cost=0.25)  # target ~$0.15 vs $0.20 spread
    feed(s, [2000.0] * 8 + [2004.0])
    assert not s.positions and s.stats["skipped_cost"] == 1


def test_time_exit_after_max_hold_bars():
    s = make(mode="fade", k=0.8, tp_atr=50.0, sl_atr=50.0, max_hold=3)
    feed(s, [2000.0] * 8 + [2004.0])
    assert s.positions
    feed(s, [2004.0] * 3, start=8 * H + 9 * M)
    assert s.stats["time_exits"] == 1
