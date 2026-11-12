import sys
from pathlib import Path

from xautest import BrokerConfig, SimBroker
from xautest.models import BUY, SELL, Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from bias_scalp import BiasScalp  # noqa: E402

DAY0 = 20_400 * 86_400_000
H, M, S = 3_600_000, 60_000, 1_000


def make(**params):
    s = BiasScalp(**{"signals": [(DAY0 + 9 * H, DAY0 + 10 * H, SELL)], "cooldown_s": 0, **params})
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0, latency_ms=0))
    s.on_start()
    return s


def tick(s, ts, bid):
    t = Tick(ts, bid, bid + 0.2)
    s.broker.on_tick(t)
    s.on_tick(t)
    return t


def test_trades_only_the_strategy_side_inside_its_window():
    s = make(entry="market")
    tick(s, DAY0 + 8 * H, 2000.0)
    assert not s.positions                                  # before the strategy entered
    tick(s, DAY0 + 9 * H + S, 2000.0)
    (p,) = s.positions
    assert p.side == SELL and p.tp < p.entry_price < p.sl


def test_flip_trades_the_other_side():
    s = make(entry="market", flip=True)
    tick(s, DAY0 + 9 * H + S, 2000.0)
    assert s.positions[0].side == BUY


def test_trade_window_ends_when_the_strategy_trade_closes():
    s = make(entry="market", window="trade")
    tick(s, DAY0 + 10 * H + S, 2000.0)
    assert not s.positions
    s = make(entry="market", window="day")
    tick(s, DAY0 + 10 * H + S, 2000.0)
    assert s.positions


def test_max_hold_closes_the_scalp():
    s = make(entry="market", tp_usd=50, sl_usd=50, max_hold_min=2)
    tick(s, DAY0 + 9 * H + S, 2000.0)
    assert s.positions
    tick(s, DAY0 + 9 * H + 3 * M, 2000.0)
    assert s.stats["time_exits"] == 1


def test_pullback_waits_for_a_candle_against_the_side():
    s = make(entry="pullback")
    tick(s, DAY0 + 9 * H + S, 2000.0)
    tick(s, DAY0 + 9 * H + 50 * S, 1999.0)                 # 1m candle falling: not a pullback for a sell
    tick(s, DAY0 + 9 * H + M + S, 1999.0)
    assert not s.positions
    tick(s, DAY0 + 9 * H + M + 50 * S, 2001.0)             # this candle rises: a pullback in a sell window
    tick(s, DAY0 + 9 * H + 2 * M + S, 2001.0)
    assert s.positions and s.positions[0].side == SELL
