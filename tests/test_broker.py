from datetime import datetime, timezone

import pytest

from xautest.broker import BrokerConfig, SimBroker, next_rollover_ms
from xautest.models import BUY, SELL, OrderStatus, OrderType, Tick, dt_to_ms

T0 = dt_to_ms(datetime(2025, 3, 3, 12, 0, tzinfo=timezone.utc))  # Monday


def tk(ms_offset, bid, ask=None):
    return Tick(T0 + ms_offset, bid, bid + 0.20 if ask is None else ask)


def broker(**kw):
    kw.setdefault("commission_per_lot", 0.0)
    return SimBroker(BrokerConfig(**kw))


def test_market_orders_fill_at_bid_ask_with_slippage_and_commission():
    b = broker(commission_per_lot=3.5, slippage=0.05)
    b.on_tick(tk(0, 2650.00, 2650.30))
    b.submit(BUY, 1.0)
    b.submit(SELL, 0.5)
    longp, shortp = b.positions
    assert longp.entry_price == pytest.approx(2650.35)
    assert shortp.entry_price == pytest.approx(2649.95)
    assert b.balance == pytest.approx(10_000 - 3.5 - 1.75)


def test_long_closes_on_bid_short_on_ask_and_pnl_uses_contract_size():
    b = broker()
    b.on_tick(tk(0, 2650.0, 2650.2))
    b.submit(BUY, 1.0)
    b.submit(SELL, 1.0)
    b.on_tick(tk(1000, 2652.0, 2652.2))
    b.close_all()
    long_t, short_t = sorted(b.trades, key=lambda t: -t.side)
    assert long_t.exit_price == 2652.0 and long_t.gross_pnl == pytest.approx((2652.0 - 2650.2) * 100)
    assert short_t.exit_price == 2652.2 and short_t.gross_pnl == pytest.approx((2650.0 - 2652.2) * 100)


def test_long_stop_loss_triggers_on_bid_not_ask():
    b = broker()
    b.on_tick(tk(0, 2650.0, 2650.2))
    b.submit(BUY, 0.1, sl=2649.0)
    b.on_tick(tk(1, 2649.05, 2648.95 + 0.30))  # ask moves, bid still above SL
    assert b.positions
    b.on_tick(tk(2, 2648.50, 2648.70))  # gap through the stop: fills at the bid, worse than SL
    (t,) = b.trades
    assert t.reason == "sl" and t.exit_price == 2648.50


def test_short_take_profit_triggers_on_ask():
    b = broker()
    b.on_tick(tk(0, 2650.0, 2650.2))
    b.submit(SELL, 0.1, tp=2648.0)
    b.on_tick(tk(1, 2647.9, 2648.1))  # bid below TP but ask is not
    assert b.positions
    b.on_tick(tk(2, 2647.7, 2647.95))
    (t,) = b.trades
    assert t.reason == "tp" and t.exit_price == 2648.0      # exactly at the TP, as MT5 fills it


def test_take_profit_tick_fill_option_keeps_gap_improvement():
    b = broker(tp_fill="tick")
    b.on_tick(tk(0, 2650.0, 2650.2))
    b.submit(SELL, 0.1, tp=2648.0)
    b.on_tick(tk(1, 2647.7, 2647.95))
    (t,) = b.trades
    assert t.exit_price == 2647.95


def test_latency_delays_fill_to_first_tick_after_delay():
    b = broker(latency_ms=150)
    b.on_tick(tk(0, 2650.0))
    o = b.submit(BUY, 0.1)
    b.on_tick(tk(100, 2651.0))
    assert o.status is OrderStatus.PENDING
    b.on_tick(tk(160, 2652.0, 2652.2))
    assert o.status is OrderStatus.FILLED and o.fill_price == 2652.2 and o.fill_ts == T0 + 160


def test_limit_and_stop_rules():
    b = broker()
    b.on_tick(tk(0, 2650.0, 2650.2))
    lim = b.submit(BUY, 0.1, OrderType.LIMIT, price=2649.0)
    stp = b.submit(SELL, 0.1, OrderType.STOP, price=2648.0)
    b.on_tick(tk(1, 2649.0, 2649.2))  # ask 2649.2 > limit: no fill
    assert lim.status is OrderStatus.PENDING
    b.on_tick(tk(2, 2648.6, 2648.8))  # ask <= limit: fill at ask (better than limit)
    assert lim.fill_price == 2648.8
    b.on_tick(tk(3, 2647.5, 2647.7))  # bid gapped below stop: fill at bid
    assert stp.fill_price == 2647.5


def test_marketable_limit_fills_immediately_and_orders_expire():
    b = broker()
    b.on_tick(tk(0, 2650.0, 2650.2))
    o = b.submit(BUY, 0.1, OrderType.LIMIT, price=2651.0)
    assert o.status is OrderStatus.FILLED and o.fill_price == 2650.2
    e = b.submit(SELL, 0.1, OrderType.LIMIT, price=2700.0, expiry_ms=T0 + 500)
    b.on_tick(tk(600, 2650.0))
    assert e.status is OrderStatus.EXPIRED


def test_margin_rejection_and_stop_out():
    b = broker(initial_balance=1000.0, leverage=100.0, stop_out_level=0.5)
    b.on_tick(tk(0, 2650.0, 2650.2))
    o = b.submit(BUY, 1.0)  # needs ~2650 margin
    assert o.status is OrderStatus.REJECTED and b.rejections == 1
    b.submit(BUY, 0.3)      # ~795 margin
    b.on_tick(tk(1, 2630.0, 2630.2))  # equity 1000 - 606 = 394 < 0.5 * 789
    assert not b.positions and b.trades[0].reason == "stop_out"


def test_swap_charged_at_new_york_rollover_with_triple_wednesday():
    b = broker(swap_long=-10.0)
    tue = dt_to_ms(datetime(2025, 3, 4, 20, 0, tzinfo=timezone.utc))
    b.on_tick(Tick(tue, 2650.0, 2650.2))
    b.submit(BUY, 1.0)
    # 17:00 New York on Tue 4 Mar 2025 is 22:00 UTC (EST, UTC-5)
    b.on_tick(Tick(tue + 2 * 3600_000 - 1, 2650.0, 2650.2))
    assert b.positions[0].swap == 0
    b.on_tick(Tick(tue + 2 * 3600_000 + 1, 2650.0, 2650.2))
    assert b.positions[0].swap == pytest.approx(-10.0)
    b.on_tick(Tick(tue + 26 * 3600_000 + 1, 2650.0, 2650.2))  # Wednesday rollover
    assert b.positions[0].swap == pytest.approx(-40.0)


def test_rollover_is_dst_aware():
    summer = dt_to_ms(datetime(2025, 7, 1, 12, 0, tzinfo=timezone.utc))
    assert next_rollover_ms(summer) == dt_to_ms(datetime(2025, 7, 1, 21, 0, tzinfo=timezone.utc))
    winter = dt_to_ms(datetime(2025, 1, 6, 12, 0, tzinfo=timezone.utc))
    assert next_rollover_ms(winter) == dt_to_ms(datetime(2025, 1, 6, 22, 0, tzinfo=timezone.utc))


def test_weekend_gap_applies_each_missed_weekday_rollover_once():
    b = broker(swap_long=-1.0)
    fri = dt_to_ms(datetime(2025, 3, 7, 20, 0, tzinfo=timezone.utc))
    b.on_tick(Tick(fri, 2650.0, 2650.2))
    b.submit(BUY, 1.0)
    mon = dt_to_ms(datetime(2025, 3, 10, 1, 0, tzinfo=timezone.utc))
    b.on_tick(Tick(mon, 2650.0, 2650.2))
    assert b.positions[0].swap == pytest.approx(-1.0)  # Friday only; Sat/Sun have no rollover


def test_extra_spread_and_flatten():
    b = broker(extra_spread=0.10)
    t = b.adjust(tk(0, 2650.0, 2650.2))
    assert (t.bid, t.ask) == (pytest.approx(2649.95), pytest.approx(2650.25))
    b.on_tick(t)
    b.submit(BUY, 0.1)
    b.submit(SELL, 0.1, OrderType.LIMIT, price=2700)
    b.flatten()
    assert not b.positions and not b.orders and b.trades[0].reason == "end"


def test_mae_mfe_tracked():
    b = broker()
    b.on_tick(tk(0, 2650.0, 2650.2))
    b.submit(BUY, 0.1)
    for i, bid in enumerate([2649.0, 2653.0, 2651.0], 1):
        b.on_tick(tk(i, bid))
    b.close_all()
    t = b.trades[0]
    assert t.mae == pytest.approx(2649.0 - 2650.2) and t.mfe == pytest.approx(2653.0 - 2650.2)


def test_unlimited_leverage_uses_no_margin_and_stops_out_at_zero_equity():
    b = broker(initial_balance=1000.0, leverage=float("inf"))
    b.on_tick(tk(0, 2650.0, 2650.2))
    o = b.submit(BUY, 5.0)                       # $1.3M of gold on a $1k account
    assert o.status is OrderStatus.FILLED and b.used_margin() == 0
    b.on_tick(tk(1, 2648.3, 2648.5))             # -$950: still open
    assert b.positions
    b.on_tick(tk(2, 2648.0, 2648.2))             # -$1,100: equity below zero -> stop out
    assert not b.positions and b.trades[0].reason == "stop_out"


def test_parse_leverage():
    import argparse
    from xautest.cli import parse_leverage
    assert parse_leverage("1:500") == 500 and parse_leverage("200") == 200
    assert parse_leverage("1:unlimited") == float("inf") == parse_leverage("unlimited")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_leverage("lots")


def test_fixed_spread_requotes_around_mid():
    b = broker(fixed_spread=0.17)
    t = b.adjust(tk(0, 2650.0, 2650.68))
    assert t.bid == pytest.approx(2650.255) and t.ask == pytest.approx(2650.425)


def test_daily_account_caps_loss_with_negative_balance_protection():
    b = broker(daily_account=20.0, leverage=float("inf"), initial_balance=1000.0)
    b.on_tick(tk(0, 3000.0, 3000.0))
    b.submit(BUY, 0.05, sl=2998.0)               # planned loss $10
    b.on_tick(tk(1, 2999.0, 2999.0))             # -$5: still open
    assert b.positions
    b.on_tick(tk(2, 2994.0, 2994.0))             # gaps through the stop AND the $20 account: -$30
    (t,) = b.trades
    assert t.gross_pnl == pytest.approx(-30.0) and t.nbp_credit == pytest.approx(10.0)
    assert t.net_pnl == pytest.approx(-20.0) and b.balance == pytest.approx(980.0)
    o = b.submit(BUY, 0.01)                       # account is at $0 for the rest of the day
    assert o.status is OrderStatus.REJECTED
    b.on_tick(Tick(T0 + 86_400_000, 2994.0, 2994.0))   # next UTC day: fresh $20
    assert b.submit(BUY, 0.01).status is OrderStatus.FILLED


def test_daily_account_stops_out_at_zero_before_the_stop_loss():
    b = broker(daily_account=20.0, leverage=float("inf"))
    b.on_tick(tk(0, 3000.0, 3000.0))
    b.submit(SELL, 0.10, sl=3003.0)              # planned loss $30, more than the $20 account
    b.on_tick(tk(1, 3001.9, 3001.9))             # -$19
    assert b.positions
    b.on_tick(tk(2, 3002.0, 3002.0))             # -$20: equity $0 -> stop out (stop loss not reached)
    (t,) = b.trades
    assert t.reason == "stop_out" and t.net_pnl == pytest.approx(-20.0) and t.nbp_credit == 0


def test_daily_account_does_not_refill_while_a_trade_is_open_across_midnight():
    b = broker(daily_account=20.0, leverage=float("inf"))
    late = T0 + 11 * 3_600_000 + 50 * 60_000           # 23:50 UTC (T0 is 12:00)
    b.on_tick(Tick(late, 3000.0, 3000.0))
    b.submit(BUY, 0.10)                                  # no stop: only the account limits the loss
    b.on_tick(Tick(late + 20 * 60_000, 2999.0, 2999.0))  # 00:10 next day, -$10, still the old day's $20
    b.on_tick(Tick(late + 30 * 60_000, 2998.0, 2998.0))  # -$20 -> stop-out on the OLD day's account
    (t,) = b.trades
    assert t.reason == "stop_out" and t.net_pnl == pytest.approx(-20.0)
    b.on_tick(Tick(late + 31 * 60_000, 2998.0, 2998.0))  # flat on the new day: fresh $20
    assert b.submit(BUY, 0.01).status is OrderStatus.FILLED
