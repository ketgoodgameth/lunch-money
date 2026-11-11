"""Exit methods in strategies/liquidity_sweep_exits.py, driven tick by tick (entries forced)."""
import sys
from pathlib import Path

import pytest

from xautest import BrokerConfig, SimBroker
from xautest.models import Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from liquidity_sweep_exits import LiquiditySweepExits  # noqa: E402

T0 = 1_760_000_000_000


def make(**params):
    s = LiquiditySweepExits(**params)
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.on_start()
    return s


def step(s, i, bid, ask=None):
    t = Tick(T0 + i * 1000, bid, bid + 0.2 if ask is None else ask)
    s.broker.on_tick(t)
    while s.broker.events:                # minimal engine: route fills/closes to the strategy
        ev = s.broker.events.pop(0)
        if ev[0] == "fill":
            s.on_fill(ev[1], ev[2])
        elif ev[0] == "close":
            s.on_trade(ev[1])
    s.on_tick(t)


def test_rr_take_profit_is_multiple_of_stop_distance_from_fill():
    s = make(exit="rr", rr=10.0)
    step(s, 0, 3000.0)
    s.buy(0.1, sl=2998.2, tp=3010.2)      # v2's own $10 target is replaced
    (p,) = s.positions
    assert p.entry_price == 3000.2
    assert p.tp == pytest.approx(3000.2 + 10 * 2.0)


def test_rr_short_and_no_break_even():
    s = make(exit="rr", rr=2.0)
    step(s, 0, 3000.0)
    s.sell(0.1, sl=3001.2, tp=2990.0)     # R = 1.2 from the bid
    (p,) = s.positions
    assert p.tp == pytest.approx(3000.0 - 2.4)
    step(s, 1, 2997.4)                    # +2.6 in favour: v2 would move to break-even, rr must not
    assert p.sl == 3001.2


def test_trailing_lock_moves_stop_only_forward():
    s = make(exit="trail", trail_start_r=1.0, trail_dist_r=1.0)
    step(s, 0, 3000.0)
    s.buy(0.1, sl=2998.2, tp=3010.2)      # entry 3000.2, R = 2.0, take profit removed
    (p,) = s.positions
    assert p.tp is None
    step(s, 1, 3001.9)                    # +1.7 < 1R: no change
    assert p.sl == 2998.2
    step(s, 2, 3002.2)                    # +2.0 = 1R: stop to break-even
    assert p.sl == pytest.approx(3000.2)
    step(s, 3, 3005.2)                    # +5R... best 3005.2: stop 3003.2
    assert p.sl == pytest.approx(3003.2)
    step(s, 4, 3004.0)                    # pullback: stop stays
    assert p.sl == pytest.approx(3003.2)
    step(s, 5, 3003.1)                    # hits the locked stop
    (t,) = s.broker.trades
    assert t.reason == "sl" and t.exit_price == 3003.1 and t.net_pnl > 0


def test_fixed_keeps_v2_break_even():
    s = make(exit="fixed")
    step(s, 0, 3000.0)
    s.sell(0.1, sl=3003.0, tp=2990.0)
    s.entry_price, s.trade_direction = 3000.0, "SELL"   # what v2's on_bar records
    (p,) = s.positions
    assert p.tp == 2990.0
    step(s, 1, 2997.3, 2997.5)            # ask 2.5 below entry: v2 moves stop to entry
    assert p.sl == 3000.0


H = 3_600_000
DAY0 = (T0 // 86_400_000) * 86_400_000          # midnight UTC of T0's day


def test_hours_filter_blocks_outside_session():
    s = make(hours="7-20")
    s.broker.on_tick(Tick(DAY0 + 3 * H, 3000.0, 3000.2))      # 03:00 UTC
    assert s.buy(0.1, sl=2998.0) is None and s.blocked["hours"] == 1
    s.broker.on_tick(Tick(DAY0 + 10 * H, 3000.0, 3000.2))     # 10:00 UTC
    assert s.buy(0.1, sl=2998.0) is not None


def test_trend_filter_follows_h1_supertrend():
    s = make(trend="h1_supertrend")
    s.h1_st.atr.count = 99                                     # pretend warmed up
    s.h1_st.direction = -1
    s.broker.on_tick(Tick(DAY0 + 10 * H, 3000.0, 3000.2))
    assert s.buy(0.1, sl=2998.0) is None and s.blocked["trend"] == 1
    assert s.sell(0.1, sl=3002.0) is not None


def test_reopen_filter_uses_the_gap_in_quotes():
    s = make(skip_reopen_min=10)
    for ts in (DAY0 + 20 * H, DAY0 + 22 * H):                 # 2-hour gap = reopen at 22:00
        t = Tick(ts, 3000.0, 3000.2)
        s.broker.on_tick(t)
        s.on_tick(t)
    assert s.buy(0.1, sl=2998.0) is None and s.blocked["reopen"] == 1
    t = Tick(DAY0 + 22 * H + 11 * 60_000, 3000.0, 3000.2)
    s.broker.on_tick(t)
    s.on_tick(t)
    assert s.buy(0.1, sl=2998.0) is not None


def test_timeframe_param_sets_bar_size():
    from xautest import Engine
    s = LiquiditySweepExits(tf="15min")
    e = Engine(s, SimBroker(), [])
    assert e.bars.ms == 15 * 60_000


def test_min_stop_widens_tight_stops_and_keeps_wide_ones():
    s = make(exit="trail", min_stop=5.0)
    step(s, 0, 3000.0)
    s.buy(0.1, sl=2998.2)                     # 2.0 from the ask -> widened to 5.0
    assert s.positions[0].sl == pytest.approx(3000.2 - 5.0)
    s2 = make(exit="trail", min_stop=5.0)
    step(s2, 0, 3000.0)
    s2.sell(0.1, sl=3007.0)                   # already 7.0 away -> unchanged
    assert s2.positions[0].sl == 3007.0


def test_limit_entry_is_one_spread_better_and_expires():
    from xautest.models import OrderStatus, OrderType as OT
    s = make(exit="trail", entry="limit", limit_expiry_s=60)
    step(s, 0, 3000.0, 3000.2)
    o = s.buy(0.1, sl=2998.0)
    assert o.type is OT.LIMIT and o.price == 3000.0 and not s.positions
    step(s, 1, 2999.9, 3000.0)                # ask touches the limit -> filled at 3000.0
    assert s.positions and s.positions[0].entry_price == 3000.0
    s2 = make(exit="trail", entry="limit", limit_expiry_s=60)
    step(s2, 0, 3000.0, 3000.2)
    o2 = s2.sell(0.1, sl=3002.0)
    assert o2.price == 3000.2
    step(s2, 61, 2999.0, 2999.2)               # price ran away; order expires
    assert o2.status is OrderStatus.EXPIRED and not s2.positions
