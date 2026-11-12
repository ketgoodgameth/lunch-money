import sys
from pathlib import Path

import pytest

from xautest import BrokerConfig, SimBroker
from xautest.models import BUY, SELL, Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from hft import HFT  # noqa: E402

DAY0 = 20_400 * 86_400_000
H = 3_600_000
T0 = DAY0 + 8 * H


def make(**params):
    s = HFT(**{"window_ms": 1_000, "trigger": 0.4, "tp": 0.5, "sl": 0.5, "hold_ms": 10_000, "cooldown_ms": 2_000,
               **params})
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0, latency_ms=0))
    s.broker.on_tick(Tick(T0, 2000.0, 2000.2))
    s.on_start()
    return s


def feed(s, quotes):
    """quotes: (ms after T0, mid). Spread $0.20."""
    for ms, mid in quotes:
        t = Tick(T0 + ms, mid - 0.1, mid + 0.1)
        s.broker.on_tick(t)
        for ev in s.broker.events:
            if ev[0] == "fill":
                s.on_fill(ev[1], ev[2])
            elif ev[0] == "close":
                s.on_trade(ev[1])
        s.broker.events.clear()
        s.on_tick(t)


def test_momentum_buys_a_burst_up_and_fade_sells_it():
    burst = [(0, 2000.1), (300, 2000.2), (600, 2000.6)]            # +$0.5 inside one second
    m = make(mode="momentum"); feed(m, burst)
    (p,) = m.positions
    assert p.side == BUY and p.tp == pytest.approx(p.entry_price + 0.5) and p.sl == pytest.approx(p.entry_price - 0.5)
    f = make(mode="fade"); feed(f, burst)
    assert f.positions[0].side == SELL


def test_a_slow_move_is_not_a_burst():
    s = make(mode="momentum")
    feed(s, [(i * 900, 2000.1 + 0.2 * i) for i in range(6)])       # +$1 but only $0.2 per 0.9 s
    assert not s.positions and s.stats["signals"] == 0


def test_time_exit_then_cooldown():
    s = make(mode="momentum", tp=50.0, sl=50.0)
    feed(s, [(0, 2000.1), (500, 2000.6)])
    assert s.positions
    feed(s, [(5_000, 2000.6), (10_600, 2000.6)])                    # held 10 s -> closed
    assert not s.positions and s.stats["time_exits"] == 1
    feed(s, [(11_000, 2000.6), (11_400, 2001.2)])                   # a new burst inside the 2 s cooldown
    assert not s.positions
    feed(s, [(14_000, 2001.2), (14_400, 2001.8)])                   # after the cooldown
    assert len(s.positions) == 1


def test_fade_limit_needs_the_burst_to_go_further():
    s = make(mode="fade_limit", limit_off=0.2, limit_ttl_ms=3_000)
    feed(s, [(0, 2000.1), (500, 2000.6)])                           # burst up: sell limit at bid 2000.5 + 0.2
    (o,) = s.orders
    assert o.side == SELL and o.price == pytest.approx(2000.7) and not s.positions
    feed(s, [(900, 2000.85)])                                       # bid 2000.75 >= 2000.7 -> filled
    (p,) = s.positions
    assert p.side == SELL and s.stats["limit_fills"] == 1
    t = make(mode="fade_limit", limit_off=0.2, limit_ttl_ms=3_000)
    feed(t, [(0, 2000.1), (500, 2000.6), (2_000, 2000.6), (4_000, 2000.6)])
    assert not t.orders and not t.positions                         # never reached, expired


def test_wide_spread_and_closed_hours_are_skipped():
    s = make(mode="momentum", max_spread=0.1)
    feed(s, [(0, 2000.1), (500, 2000.6)])
    assert not s.positions and s.stats["skipped_spread"] == 1
    n = make(mode="momentum", sessions="12:00-13:00")
    feed(n, [(0, 2000.1), (500, 2000.6)])
    assert not n.positions and n.stats["signals"] == 0
