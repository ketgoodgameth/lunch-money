import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xautest import BrokerConfig, SimBroker
from xautest.models import BUY, SELL, Bar, Tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from reaction_zone import FVGTracker, ReactionZone, parse_sessions  # noqa: E402

DAY0 = 20_400 * 86_400_000
H, M = 3_600_000, 60_000


def test_fvg_detection_and_fill():
    f = FVGTracker()
    f.update(Bar(0, 100, 101, 99, 100.5))
    f.update(Bar(1, 100.5, 104, 100.4, 103.8))
    f.update(Bar(2, 103.8, 106, 102.0, 105))        # bar1 high 101 < bar3 low 102 -> bullish gap 101-102
    (z,) = f.zones
    assert (z.side, z.lo, z.hi) == (1, 101, 102)
    f.update(Bar(3, 105, 105.5, 100.9, 101.5))      # trades through the gap -> filled
    assert not f.zones
    g = FVGTracker()
    for b in (Bar(0, 100, 101, 99, 99.5), Bar(1, 99.5, 99.6, 96, 96.2), Bar(2, 96.2, 97, 95, 95.5)):
        g.update(b)                                  # bar1 low 99 > bar3 high 97 -> bearish gap 97-99
    assert [(z.side, z.lo, z.hi) for z in g.zones] == [(-1, 97, 99)]


def test_sessions():
    assert parse_sessions("07:00-10:00, 12:30-16:00") == [(7 * H, 10 * H), (12 * H + 30 * M, 16 * H)]


class Stub(SimpleNamespace):
    def update(self, *_):
        return getattr(self, "ret", None)


def make(bias=-1, rsi=75.0, **params):
    s = ReactionZone(**params)
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.broker.on_tick(Tick(DAY0 + 13 * H, 3003.0, 3003.2))
    s.on_start()
    s.bias = lambda: bias
    s.m15_rsi = Stub(ret=rsi)
    for _ in range(s.p.swing_lookback):
        s.hist15.append((3005.0, 2995.0, 60.0))
    return s


def run_to_entry(s, side=SELL):
    T = DAY0 + 13 * H
    s.m5_st = Stub(direction=side, ready=True)
    s.m5_macd = Stub(ret=-0.4 if side == SELL else 0.4, prev_hist=0.1 if side == SELL else -0.1, ready=True)
    s.on_m5(Bar(T + 15 * M, 0, 0, 0, 0))
    s.m1_macd = Stub(ret=-0.2 if side == SELL else 0.2, prev_hist=0.05 if side == SELL else -0.05, ready=True)
    px = 3002.0 if side == SELL else 2998.0
    t = Tick(T + 21 * M, px, px + 0.2)
    s.broker.on_tick(t)
    s.on_m1(Bar(T + 20 * M, px + 0.5, px + 0.6, px - 0.1, px), t)
    return t


def test_sell_via_sweep_no_stop_fixed_tp():
    s = make()
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))   # sweep 3005, close back below, wick 5/7
    assert s.setup.side == SELL and s.setup.level == 3005.0 and s.stats["via_sweep"] == 1
    run_to_entry(s)
    (p,) = s.positions
    assert p.side == SELL and p.sl is None and p.tp == pytest.approx(3002.0 - 10.0)


def test_sell_via_bearish_fvg_and_wick_stop():
    s = make(sl_mode="wick", rsi=72.0)
    s.fvg_h1.zones.append(__import__("reaction_zone").Zone(-1, 3003.5, 3004.5))
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3004.0, 3001.5, 3002.5))   # no sweep (3004 < 3005) but enters the gap
    assert s.setup is not None and s.stats["via_fvg"] == 1 and s.setup.level == 3003.5
    run_to_entry(s)
    (p,) = s.positions
    assert p.sl == pytest.approx(3004.0 + 0.2 + 0.5)


def test_no_entry_outside_sessions_or_against_bias():
    s = make(sessions="07:00-10:00")
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    run_to_entry(s)
    assert not s.positions and s.stats["outside_session"] == 1
    s = make(bias=1)
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    assert s.setup is None


def test_buy_mirror():
    s = make(bias=1, rsi=25.0)
    s.on_m15(Bar(DAY0 + 13 * H, 2998.0, 2999.0, 2992.0, 2997.0))   # sweep of 2995 lows, close back above
    assert s.setup.side == BUY
    run_to_entry(s, side=BUY)
    (p,) = s.positions
    assert p.side == BUY and p.tp == pytest.approx(2998.2 + 10.0)


def test_exec_on_5m_confirmation():
    s = make(exec_mode="m5")
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    s.m5_st = Stub(direction=SELL, ready=True)
    s.m5_macd = Stub(ret=-0.4, prev_hist=0.1, ready=True)
    t = Tick(DAY0 + 13 * H + 20 * M, 3002.0, 3002.2)
    s.broker.on_tick(t)
    s.on_m5(Bar(DAY0 + 13 * H + 15 * M, 3003.0, 3003.5, 3001.8, 3002.0), t)
    (p,) = s.positions
    assert p.side == SELL and p.tp == pytest.approx(3002.0 - 10.0)


def test_volatility_scaled_size_keeps_dollar_risk_and_rr():
    s = make(size_mode="atr", account_risk=10.0, stop_atr_frac=0.25, rr=5.0)
    s.datr = Stub(ready=True, value=13.0)          # quiet market: daily range $13
    assert s.scaled_size() == (0.03, pytest.approx(10.0 / 3))
    s.datr = Stub(ready=True, value=45.0)          # busy market: daily range $45 -> minimum 0.01 lots
    assert s.scaled_size() == (0.01, pytest.approx(10.0))
    s.datr = Stub(ready=True, value=13.0)
    s.risk = __import__("xautest.risk", fromlist=["DailyRiskRules"]).DailyRiskRules(use_strategy_lots=True, max_trades=2)
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    run_to_entry(s)
    (p,) = s.positions
    assert p.lots == 0.03 and p.sl is None and p.tp == pytest.approx(3002.0 - 5 * 10.0 / 3)


def test_take_profit_scales_with_daily_range():
    s = make(tp_atr=0.25)
    s.datr = Stub(ready=True, value=20.0)          # daily range $20 -> target $5
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    run_to_entry(s)
    (p,) = s.positions
    assert p.lots == 0.01 and p.tp == pytest.approx(3002.0 - 5.0)


def test_break_even_stop_moves_once_trade_is_in_profit():
    s = make(be_trigger=5.0, be_offset=1.0)
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    run_to_entry(s)
    (p,) = s.positions
    assert p.sl is None
    s._break_even(Tick(DAY0 + 14 * H, 2998.0, 2998.2))       # +$3.8: not yet
    assert p.sl is None
    s._break_even(Tick(DAY0 + 14 * H + M, 2996.8, 2997.0))   # +$5.0 on the ask: stop to entry - $1
    assert p.sl == pytest.approx(p.entry_price - 1.0)
    s._break_even(Tick(DAY0 + 14 * H + 2 * M, 2990.0, 2990.2))
    assert p.sl == pytest.approx(p.entry_price - 1.0)         # never trails further


def confidence_setup(s, macd_hist=-1.0, st_dir=-1, day_pnl=0.0, bar_range=2.0, atr=2.0):
    s.h1_macd = Stub(ready=True, hist=macd_hist)
    s.m15_st = Stub(ready=True, direction=st_dir)
    s.m15_atr = Stub(ready=True, value=atr)
    s.last15 = Bar(DAY0, 3000.0, 3000.0 + bar_range, 3000.0, 3000.0)
    s.pnl_by_day[DAY0 // 86_400_000] = day_pnl


def test_confidence_counts_agreeing_market_checks():
    s = make()
    confidence_setup(s)
    assert s.confidence(SELL, DAY0 + 13 * H) == 1.0
    confidence_setup(s, macd_hist=0.5, day_pnl=-20.0)           # 1H momentum against, a losing day so far
    assert s.confidence(SELL, DAY0 + 13 * H) == 0.5
    confidence_setup(s, bar_range=6.0)                          # 15m candle 3x ATR: news spike
    assert s.confidence(SELL, DAY0 + 13 * H) == 0.75
    assert s.confidence(BUY, DAY0 + 13 * H) == 0.25


def test_each_extra_trade_needs_more_confidence():
    s = make(conf_base=0.5, conf_step=0.25)
    confidence_setup(s, macd_hist=0.5)                          # score 0.75
    ts, day = DAY0 + 13 * H, DAY0 // 86_400_000
    assert s.confident_enough(SELL, ts)                         # 1st trade needs 0.50
    s.fills_by_day[day] = 1
    assert s.confident_enough(SELL, ts)                         # 2nd needs 0.75
    s.fills_by_day[day] = 2
    assert not s.confident_enough(SELL, ts)                     # 3rd needs 1.00
    assert make().confident_enough(BUY, ts)                     # off by default


def test_retest_entry_rests_a_limit_at_the_level_and_cancels_past_the_wick():
    s = make(exec_mode="m1_retest")
    s.on_m15(Bar(DAY0 + 13 * H, 3002.0, 3008.0, 3001.0, 3003.0))
    level, wick = s.setup.level, s.setup.wick
    s.m5_st = Stub(direction=SELL, ready=True)
    s.m5_macd = Stub(ret=-0.4, prev_hist=0.1, ready=True)
    t = Tick(DAY0 + 13 * H + 20 * M, 3002.0, 3002.2)
    s.broker.on_tick(t)
    s.on_m5(Bar(DAY0 + 13 * H + 15 * M, 3003.0, 3003.5, 3001.8, 3002.0), t)
    (o,) = s.orders
    assert not s.positions and o.price == pytest.approx(level) and o.tp == pytest.approx(level - 10.0)
    s.on_tick(Tick(DAY0 + 13 * H + 22 * M, wick + 0.1, wick + 0.3))   # price breaks the wick: idea is dead
    assert not s.orders
