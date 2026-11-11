import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xautest import BrokerConfig, SimBroker
from xautest.models import BUY, SELL, Bar, Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from trend_pullback import TrendPullback  # noqa: E402

T0 = 1_760_000_000_000


def make(direction=1, atr=2.0, **params):
    s = TrendPullback(**params)
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.broker.on_tick(Tick(T0, 3000.0, 3000.2))
    s.on_start()
    s.st = SimpleNamespace(ready=True, direction=direction, update=lambda b: None)
    s.atr = SimpleNamespace(ready=True, update=lambda b: atr)
    for _ in range(s.p.lookback):
        s.hist.append((3005.0, 2995.0))
    return s


def test_long_sweep_in_uptrend_enters_with_atr_floor_stop():
    s = make(direction=1, atr=2.0)
    t = Tick(T0 + 1000, 2997.0, 2997.2)
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 2996.0, 2997.5, 2994.0, 2997.0), t)   # low 2994 < 2995, close back above
    (p,) = s.positions
    # structure stop 2994 - 0.4 = 2993.6 (3.6 away) vs floor 2997.2 - 2.0 = 2995.2 -> the wider one
    assert p.side == BUY and p.sl == pytest.approx(2993.6)


def test_floor_widens_a_tight_structure_stop():
    s = make(direction=1, atr=5.0)
    t = Tick(T0 + 1000, 2997.0, 2997.2)
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 2996.0, 2997.5, 2994.0, 2997.0), t)
    assert s.positions[0].sl == pytest.approx(2997.2 - 5.0)


def test_no_entry_against_trend_or_without_rejection():
    s = make(direction=-1)
    t = Tick(T0 + 1000, 2997.0, 2997.2)
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 2996.0, 2997.5, 2994.0, 2997.0), t)   # long setup in a downtrend
    assert not s.positions
    s = make(direction=1)
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 2996.0, 2996.5, 2990.0, 2994.0), t)   # closed below the old low: breakdown
    assert not s.positions


def test_short_mirror_and_wide_stop_skip():
    s = make(direction=-1, atr=2.0)
    t = Tick(T0 + 1000, 3003.0, 3003.2)
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 3004.0, 3006.0, 3002.5, 3003.0), t)   # high 3006 > 3005, close back below
    (p,) = s.positions
    assert p.side == SELL and p.sl == pytest.approx(3006.0 + 0.4)
    s = make(direction=-1, atr=12.0)                              # floor 12 > max_stop 10
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 3004.0, 3006.0, 3002.5, 3003.0), t)
    assert not s.positions and s.stats["skipped_wide"] == 1


def test_trailing_locks_profit():
    s = make(direction=1, atr=2.0)
    t = Tick(T0 + 1000, 2997.0, 2997.2)
    s.broker.on_tick(t)
    s.on_setup_bar(Bar(T0, 2996.0, 2997.5, 2994.0, 2997.0), t)
    (p,) = s.positions
    s.on_fill(None, p)                                   # R = 3.6
    for i, bid in enumerate([2999.0, 3001.0, 3003.0], 2):
        tk = Tick(T0 + i * 1000, bid, bid + 0.2)
        s.broker.on_tick(tk)
        s.manage(tk)
    assert p.sl == pytest.approx(3003.0 - 1.8)           # best 3003, trailing 0.5R behind
