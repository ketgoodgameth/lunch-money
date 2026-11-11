"""Stage-by-stage tests for strategies/liquidity_sweep_mtf.py with stubbed indicators."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from xautest import BrokerConfig, Engine, SimBroker
from xautest.feeds import CSVTickFeed
from xautest.feeds.synthetic import generate
from xautest.models import BUY, SELL, Bar, Tick, dt_to_ms

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from liquidity_sweep_mtf import LiquiditySweepMTF  # noqa: E402

MIN = 60_000
T0 = dt_to_ms(datetime(2025, 10, 6, 10, 0, tzinfo=timezone.utc))  # 15m-aligned


class Stub(SimpleNamespace):
    def update(self, *_):
        return getattr(self, "ret", None)


def make(bias=-1, rsi=75.0, **params):
    s = LiquiditySweepMTF(**params)
    s.broker = SimBroker(BrokerConfig(commission_per_lot=0))
    s.broker.on_tick(Tick(T0, 3003.0, 3003.2))
    s.on_start()
    s.bias = lambda: bias
    s.m15_rsi = Stub(ret=rsi)
    for _ in range(s.p.m15_lookback):            # previous 20 bars: range 2995-3005, RSI 60
        s.m15_hist.append((3005.0, 2995.0, 60.0))
    return s


def set_m5(s, direction, hist, prev):
    s.m5_st = Stub(direction=direction, ready=True)
    s.m5_macd = Stub(ret=hist, prev_hist=prev, ready=True)


def tick(s, ts, bid, ask=None):
    t = Tick(ts, bid, bid + 0.2 if ask is None else ask)
    s.broker.on_tick(t)
    return t


SWEEP_HIGH = Bar(T0, 3002.0, 3008.0, 3001.0, 3003.0)   # pierces 3005, closes 3003, upper wick 5/7 = 71%


def test_full_short_path_enters_with_stop_beyond_wick_and_fixed_tp():
    s = make()
    s.on_m15(SWEEP_HIGH)
    st = s.setup
    assert (st.side, st.level, st.wick, st.ready_ts) == (SELL, 3005.0, 3008.0, T0 + 15 * MIN)
    set_m5(s, direction=-1, hist=-0.4, prev=0.1)            # MACD crossed below signal, ST red
    s.on_m5(Bar(T0 + 15 * MIN, 3003, 3004, 3002, 3002.5))
    assert st.confirmed_ts == T0 + 20 * MIN
    tick(s, T0 + 21 * MIN, 3002.0, 3002.2)
    s.on_m1(Bar(T0 + 20 * MIN, 3003.0, 3003.2, 3001.8, 3002.0))  # bearish 1m close below 3005
    (pos,) = s.positions
    assert pos.side == SELL and pos.entry_price == 3002.0
    assert pos.sl == pytest.approx(3008.0 + 0.2 + 0.5) and pos.tp == pytest.approx(3002.0 - 10.0)
    assert s.setup is None and s.stats["triggered"] == 1


def test_bias_must_agree():
    s = make(bias=1)
    s.on_m15(SWEEP_HIGH)
    assert s.setup is None and s.stats["rsi_ok"] == 1 and s.stats["bias_aligned"] == 0


def test_rsi_or_divergence_required():
    s = make(rsi=65.0)                     # not overbought (<70) and above the swing's RSI (60): no divergence
    s.on_m15(SWEEP_HIGH)
    assert s.setup is None
    s = make(rsi=55.0)                     # lower RSI on a higher high = bearish divergence
    s.on_m15(SWEEP_HIGH)
    assert s.setup is not None
    s = make(rsi=55.0, use_divergence=False)
    s.on_m15(SWEEP_HIGH)
    assert s.setup is None


def test_weak_wick_or_close_beyond_level_is_not_a_sweep():
    s = make()
    s.on_m15(Bar(T0, 3001.0, 3008.0, 3000.0, 3006.0))   # closed above 3005: breakout, not sweep
    assert s.setup is None and s.stats["sweeps"] == 0
    s = make()                                           # fresh: that close made 3008 the new swing high
    s.on_m15(Bar(T0, 3000.0, 3006.0, 2999.0, 3004.8))   # wick 1.2/7 = 17%
    assert s.setup is None and s.stats["sweeps"] == 1 and s.stats["rejections"] == 0


def test_no_confirmation_from_bars_inside_the_sweep_and_no_entry_without_it():
    s = make()
    s.on_m15(SWEEP_HIGH)
    set_m5(s, direction=-1, hist=-0.4, prev=0.1)
    s.on_m5(Bar(T0 + 10 * MIN, 3003, 3004, 3002, 3002.5))   # last 5m of the sweep candle itself
    assert s.setup.confirmed_ts is None
    s.on_m1(Bar(T0 + 16 * MIN, 3003.0, 3003.2, 3001.8, 3002.0))
    assert not s.positions


def test_5m_needs_both_macd_and_supertrend():
    s = make()
    s.on_m15(SWEEP_HIGH)
    set_m5(s, direction=1, hist=-0.4, prev=0.1)      # ST still green
    s.on_m5(Bar(T0 + 15 * MIN, 3003, 3004, 3002, 3002.5))
    set_m5(s, direction=-1, hist=-0.2, prev=-0.4)    # ST red but histogram rising (weakening)
    s.on_m5(Bar(T0 + 20 * MIN, 3003, 3004, 3002, 3002.5))
    assert s.setup.confirmed_ts is None


def test_trading_through_the_wick_invalidates_and_setups_expire():
    s = make()
    s.on_m15(SWEEP_HIGH)
    s.on_tick(tick(s, T0 + 16 * MIN, 3008.1))
    assert s.setup is None and s.stats["invalidated"] == 1
    s = make(setup_valid_min=30)
    s.on_m15(SWEEP_HIGH)
    s.on_tick(tick(s, T0 + 45 * MIN, 3003.0))
    assert s.setup is None and s.stats["expired"] == 1


def test_long_mirror():
    s = make(bias=1, rsi=25.0)
    s.on_m15(Bar(T0, 2998.0, 2999.0, 2992.0, 2997.0))   # pierces 2995, closes 2997, lower wick 5/7
    assert s.setup.side == BUY and s.setup.wick == 2992.0
    set_m5(s, direction=1, hist=0.4, prev=-0.1)
    s.on_m5(Bar(T0 + 15 * MIN, 2997, 2998, 2996, 2997.5))
    tick(s, T0 + 21 * MIN, 2998.0, 2998.2)
    s.on_m1(Bar(T0 + 20 * MIN, 2997.0, 2998.2, 2996.9, 2998.0))
    (pos,) = s.positions
    assert pos.side == BUY and pos.sl == pytest.approx(2991.5) and pos.tp == pytest.approx(3008.2)


def test_risk_sizing():
    s = make(risk_pct=1.0)                 # 1% of 10k = $100 over a 6.9 stop -> 0.14 lots
    s.on_m15(SWEEP_HIGH)
    set_m5(s, direction=-1, hist=-0.4, prev=0.1)
    s.on_m5(Bar(T0 + 15 * MIN, 3003, 3004, 3002, 3002.5))
    tick(s, T0 + 21 * MIN, 3002.0, 3002.2)
    s.on_m1(Bar(T0 + 20 * MIN, 3003.0, 3003.2, 3001.8, 3002.0))
    assert s.positions[0].lots == pytest.approx(0.14)


def test_runs_end_to_end_on_ticks(tmp_path):
    df = generate(datetime(2025, 3, 3, tzinfo=timezone.utc), datetime(2025, 3, 15, tzinfo=timezone.utc), seed=9)
    p = tmp_path / "t.csv"
    df.to_csv(p, index=False)
    s = LiquiditySweepMTF()
    result = Engine(s, SimBroker(BrokerConfig(latency_ms=100)), CSVTickFeed(p)).run()
    st = s.stats
    assert st["h1_bars"] > 150
    assert st["sweeps"] >= st["rejections"] >= st["rsi_ok"] >= st["bias_aligned"] >= st["triggered"]
    assert st["triggered"] == len(result.trades)
