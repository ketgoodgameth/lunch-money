import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xautest import BrokerConfig, Engine, SimBroker
from xautest.models import OrderType, Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from opening_range import OpeningRange  # noqa: E402
from trend_pullback import TrendPullback  # noqa: E402

DAY0 = 20_400 * 86_400_000          # a UTC midnight
H, M = 3_600_000, 60_000


def run_day(prices, direction=1, **params):
    s = OpeningRange(**params)
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.broker.on_tick(Tick(prices[0][0], prices[0][1], prices[0][1] + 0.2))
    s.on_start()
    s.st = SimpleNamespace(ready=True, direction=direction, update=lambda b: None)
    for ts, bid in prices:
        t = Tick(ts, bid, bid + 0.2)
        s.broker.on_tick(t)
        while s.broker.events:
            ev = s.broker.events.pop(0)
            if ev[0] == "fill":
                s.on_fill(ev[1], ev[2])
            elif ev[0] == "close":
                s.on_trade(ev[1])
        s.on_tick(t)
    return s


RANGE = [(DAY0 + 7 * H, 3000.0), (DAY0 + 7 * H + 20 * M, 3004.0), (DAY0 + 7 * H + 40 * M, 2996.0)]


def test_buy_stop_above_range_in_uptrend():
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3001.0)], direction=1)
    (o,) = s.orders
    assert o.type is OrderType.STOP and o.side == 1
    assert o.price == pytest.approx(3004.1) and o.sl == pytest.approx(2995.9)


def test_sell_stop_below_range_in_downtrend_and_fill():
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3001.0), (DAY0 + 9 * H, 2995.5)], direction=-1)
    (p,) = s.positions
    assert p.side == -1 and p.sl == pytest.approx(3004.1)


def test_skip_when_range_too_wide_or_already_broken():
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3001.0)], direction=1, max_stop=5.0)
    assert not s.orders and s.stats["skipped_wide"] == 1
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3006.0)], direction=1)
    assert not s.orders and s.stats["skipped_already_broken"] == 1


def test_flat_at_end_of_day():
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3001.0), (DAY0 + 9 * H, 3005.0), (DAY0 + 20 * H + M, 3006.0)])
    assert not s.positions and s.broker.trades[0].reason == "eod"


def test_trend_pullback_entry_hours():
    s = TrendPullback(entry_hours="7-18")
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.broker.on_tick(Tick(DAY0 + 3 * H, 3000.0, 3000.2))
    s.on_start()
    s.st = SimpleNamespace(ready=True, direction=1, update=lambda b: None)
    s.atr = SimpleNamespace(ready=True, update=lambda b: 2.0)
    for _ in range(s.p.lookback):
        s.hist.append((3005.0, 2995.0))
    from xautest.models import Bar
    s.on_setup_bar(Bar(DAY0 + 3 * H, 2996.0, 2997.5, 2994.0, 2997.0), Tick(DAY0 + 3 * H + 1000, 2997.0, 2997.2))
    assert not s.positions and s.stats["skipped_hours"] == 1


def test_trend_pullback_without_trend_takes_both_directions():
    from xautest.models import Bar
    for bar, side in ((Bar(DAY0 + 10 * H, 2996.0, 2997.5, 2994.0, 2997.0), 1),
                      (Bar(DAY0 + 10 * H, 3004.0, 3006.0, 3002.5, 3003.0), -1)):
        s = TrendPullback(trend="none")
        s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
        t = Tick(DAY0 + 10 * H + 1000, bar.close, bar.close + 0.2)
        s.broker.on_tick(t)
        s.on_start()
        s.st = SimpleNamespace(ready=False, direction=0, update=lambda b: None)   # trend not even ready
        s.atr = SimpleNamespace(ready=True, update=lambda b: 2.0)
        for _ in range(s.p.lookback):
            s.hist.append((3005.0, 2995.0))
        s.on_setup_bar(bar, t)
        assert s.positions and s.positions[0].side == side


def test_against_trend_flips_the_side():
    s = OpeningRange(direction="against")
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.broker.on_tick(Tick(RANGE[0][0], 3000.0, 3000.2))
    s.on_start()
    s.st = SimpleNamespace(ready=True, direction=1, update=lambda b: None)
    for ts, bid in RANGE + [(DAY0 + 8 * H + M, 3001.0)]:
        t = Tick(ts, bid, bid + 0.2)
        s.broker.on_tick(t)
        s.on_tick(t)
    (o,) = s.orders
    assert o.side == -1 and o.price == pytest.approx(2995.9)


def test_new_york_time_anchor_follows_daylight_saving():
    from datetime import datetime, timezone
    s = OpeningRange(range_start="09:30", range_tz="America/New_York")
    s.on_start()
    summer = int(datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp() * 1000) // 86_400_000
    winter = int(datetime(2025, 1, 15, tzinfo=timezone.utc).timestamp() * 1000) // 86_400_000
    s._set_range(summer)
    assert s.r0 == (13 * 60 + 30) * M          # 09:30 EDT = 13:30 UTC
    s._set_range(winter)
    assert s.r0 == (14 * 60 + 30) * M          # 09:30 EST = 14:30 UTC


def test_half_range_stop():
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3001.0)], direction=1, stop_frac=0.5)
    (o,) = s.orders
    assert o.price == pytest.approx(3004.1) and o.sl == pytest.approx(3004.1 - 0.5 * 8.2)
    s = run_day(RANGE + [(DAY0 + 8 * H + M, 3001.0)], direction=-1, stop_frac=0.5)
    (o,) = s.orders
    assert o.price == pytest.approx(2995.9) and o.sl == pytest.approx(2995.9 + 0.5 * 8.2)
