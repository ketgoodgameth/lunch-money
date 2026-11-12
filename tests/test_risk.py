from datetime import datetime, timezone

import pytest

from xautest import BrokerConfig, Engine, SimBroker, Strategy
from xautest.models import Tick, dt_to_ms
from xautest.risk import DailyRiskRules

D1 = dt_to_ms(datetime(2025, 10, 6, 8, 0, tzinfo=timezone.utc))
D2 = D1 + 86_400_000
S = 1000  # 1 s


class AlwaysSell(Strategy):
    """Sells whenever flat, stop `stop` above, target `target` below."""
    params = {"stop": 1.0, "target": 1.0}

    def on_tick(self, t):
        if not self.positions and not self.orders:
            self.sell(5.0, sl=t.ask + self.p.stop, tp=t.bid - self.p.target)  # 5 lots is ignored by the rules


def run(prices, rules=None, **params):
    s = AlwaysSell(**params)
    s.risk = rules or DailyRiskRules(risk_per_trade=10, max_daily_loss=20, max_trades=2, stop_after_win=True)
    ticks = [Tick(ts, p, p) for ts, p in prices]   # zero spread keeps the arithmetic obvious
    b = SimBroker(BrokerConfig(commission_per_lot=0, leverage=float("inf"), tp_fill="tick"))  # rule arithmetic uses gap fills
    res = Engine(s, b, ticks, close_on_end=True).run()
    return res, s.risk


def test_win_first_trade_stops_the_day_then_next_day_trades_again():
    res, r = run([(D1, 3000.0), (D1 + S, 2998.9), (D1 + 2 * S, 2998.9), (D1 + 3 * S, 2990.0),
                  (D2, 3000.0), (D2 + S, 2998.9)])
    day1 = [t for t in res.trades if t.entry_ts < D2]
    assert len(day1) == 1 and day1[0].lots == pytest.approx(0.10)       # $10 / ($1 stop x 100 oz)
    assert day1[0].net_pnl == pytest.approx(11.0)
    assert r.days[r.day_of(D1)].stopped == "win"
    assert r.stats["blocked_day_done"] >= 1
    assert len([t for t in res.trades if t.entry_ts >= D2]) == 1


def test_loss_allows_second_trade_sized_to_remaining_budget_then_stops():
    # trade 1 gaps through its stop: -$11; trade 2 can risk only $9 (20 - 11); it also loses
    res, r = run([(D1, 3000.0), (D1 + S, 3001.1), (D1 + 2 * S, 3002.2), (D1 + 3 * S, 3010.0), (D1 + 4 * S, 3000.0)])
    assert [round(t.lots, 2) for t in res.trades] == [0.10, 0.09]
    assert [round(t.net_pnl, 2) for t in res.trades] == [-11.0, -9.9]
    st = r.days[r.day_of(D1)]
    assert st.trades == 2 and st.stopped == "loss limit" and st.pnl == pytest.approx(-20.9)


def test_loss_then_win_stops():
    res, r = run([(D1, 3000.0), (D1 + S, 3001.0), (D1 + 2 * S, 2999.9), (D1 + 3 * S, 2999.0)])
    assert [round(t.net_pnl, 2) for t in res.trades] == [-10.0, 11.0]
    assert r.days[r.day_of(D1)].stopped == "win"


def test_break_even_first_trade_still_allows_one_more_then_max_trades():
    # target 0: each trade closes at its entry price (P&L 0, not a win). Trade 1 doesn't end the
    # day, trade 2 is allowed, and after it the day is over even though the next tick would sell again.
    res, r = run([(D1, 3000.0), (D1 + S, 3000.0), (D1 + 2 * S, 3000.0), (D1 + 3 * S, 3001.0),
                  (D1 + 4 * S, 3000.0)], target=0.0)
    assert [round(t.net_pnl, 2) for t in res.trades] == [0.0, 0.0]
    st = r.days[r.day_of(D1)]
    assert st.trades == 2 and st.stopped == "max trades"


def test_gap_through_stop_can_lose_more_than_planned_and_ends_the_day():
    res, r = run([(D1, 3000.0), (D1 + S, 3010.0), (D1 + 2 * S, 3000.0)])
    (t,) = res.trades
    assert t.net_pnl == pytest.approx(-100.0)            # $1 stop planned, price jumped $10
    assert r.days[r.day_of(D1)].stopped == "loss limit"


def test_stop_too_wide_for_minimum_lot_is_skipped():
    res, r = run([(D1, 3000.0), (D1 + S, 2900.0)], stop=11.0)   # 0.01 lots x $11 = $11 > $10
    assert not res.trades and r.stats["skipped_stop_too_wide"] >= 1


def test_zero_spread_quotes_mid():
    b = SimBroker(BrokerConfig(zero_spread=True))
    t = b.adjust(Tick(D1, 3000.0, 3000.4))
    assert t.bid == t.ask == pytest.approx(3000.2)


def test_stop_after_two_wins_in_a_row():
    rules = DailyRiskRules(10, 20, max_trades=0, stop_after_wins=2)
    # trades: win, loss, loss, win, win -> stop (the next tick would have given a third win)
    res, r = run([(D1, 3000.0), (D1 + S, 2998.9), (D1 + 2 * S, 3000.0), (D1 + 3 * S, 3001.0),
                  (D1 + 4 * S, 2999.9), (D1 + 5 * S, 2998.8), (D1 + 6 * S, 2997.7), (D1 + 7 * S, 2990.0)],
                 rules=rules)
    assert [t.net_pnl > 0 for t in res.trades] == [True, False, False, True, True]
    assert r.days[r.day_of(D1)].stopped == "win"


def test_stop_after_two_losses_in_a_row_even_if_up_on_the_day():
    rules = DailyRiskRules(10, 20, max_trades=0, stop_after_wins=0, stop_after_losses=2)
    res, r = run([(D1, 3000.0), (D1 + S, 2995.0), (D1 + 2 * S, 2996.0), (D1 + 3 * S, 2997.0),
                  (D1 + 4 * S, 3000.0)], rules=rules)
    assert [round(t.net_pnl, 1) for t in res.trades] == [50.0, -10.0, -10.0]
    st = r.days[r.day_of(D1)]
    assert st.stopped == "losses" and st.pnl == pytest.approx(30.0)


def test_no_win_stop_trades_until_the_loss_cap():
    rules = DailyRiskRules(10, 20, max_trades=0, stop_after_wins=0)
    res, r = run([(D1, 3000.0), (D1 + S, 2998.9), (D1 + 2 * S, 2999.9), (D1 + 3 * S, 3000.9),
                  (D1 + 4 * S, 3001.9), (D1 + 5 * S, 3010.0)], rules=rules)
    # +11 then -10, -10, -10: stops when the day is down $19 -> no: stops at <= -20 after the 4th trade
    assert len(res.trades) == 4 and r.days[r.day_of(D1)].stopped == "loss limit"


def test_min_stop_skips_trades_with_tight_stops():
    rules = DailyRiskRules(10, 20, 2, True, min_stop=1.5)
    res, r = run([(D1, 3000.0), (D1 + S, 2998.9)], rules=rules, stop=1.0)
    assert not res.trades and r.stats["skipped_stop_too_tight"] >= 1
    rules = DailyRiskRules(10, 20, 2, True, min_stop=1.5)
    res, r = run([(D1, 3000.0), (D1 + S, 2998.0)], rules=rules, stop=2.0, target=2.0)
    assert len(res.trades) == 1


def test_fixed_lots_without_stop():
    class NoStopSell(AlwaysSell):
        def on_tick(self, t):
            if not self.positions and not self.orders:
                self.sell(5.0, tp=t.bid - 1.0)
    s = NoStopSell()
    s.risk = DailyRiskRules(10, 20, 2, True, fixed_lots=0.01)
    b = SimBroker(BrokerConfig(commission_per_lot=0, leverage=float("inf"), tp_fill="tick"))  # rule arithmetic uses gap fills
    res = Engine(s, b, [Tick(D1, 3000.0, 3000.0), Tick(D1 + S, 2998.9, 2998.9)]).run()
    (t,) = res.trades
    assert t.lots == 0.01 and t.reason == "tp"


def test_weekly_loss_limit_blocks_rest_of_week_then_resets():
    from datetime import datetime, timezone
    mon = dt_to_ms(datetime(2025, 3, 3, 10, tzinfo=timezone.utc))            # a Monday
    day = 86_400_000
    rules = DailyRiskRules(10, 20, 2, True, weekly_loss_limit=15.0)
    prices = [(mon, 3000.0), (mon + S, 3001.0),                                # Mon: -$10
              (mon + day, 3001.0), (mon + day + S, 3002.0),                    # Tue: -$10 -> week -$20 <= -15
              (mon + 2 * day, 3002.0), (mon + 2 * day + S, 3003.0),            # Wed: blocked
              (mon + 7 * day, 3003.0), (mon + 7 * day + S, 3001.9)]            # next Monday: trades again
    res, r = run(prices, rules=rules)
    days = sorted({t.entry_ts // day for t in res.trades})
    assert len(res.trades) == 3 and (mon + 2 * day) // day not in days
    assert r.stats["blocked_period_limit"] >= 1


class Sizer:
    """Just enough broker for DailyRiskRules.approve."""
    def __init__(self, balance, price=3000.0):
        self.balance, self.positions, self.orders = balance, [], []
        self.tick = Tick(D1, price, price)
        self.cfg = BrokerConfig()


def lots_for(rules, balance, stop):
    from xautest.models import SELL, OrderType
    return rules.approve(Sizer(balance), SELL, OrderType.MARKET, None, 3000.0 + stop)


def test_risk_pct_sizes_from_the_balance_and_the_stop():
    r = DailyRiskRules(risk_pct=3.0, max_trades=0, stop_after_wins=0, min_lot_fallback=True)
    assert r.max_daily_loss == 0.0                                  # no dollar cap fights the percentage
    assert lots_for(r, 1400.0, 20.0) == pytest.approx(0.02)         # $42 / ($20 x 100 oz)
    assert lots_for(r, 5000.0, 20.0) == pytest.approx(0.07)
    assert lots_for(r, 1000.0, 20.0) == pytest.approx(0.01)         # $30 -> 0.015 -> rounded down
    assert lots_for(r, 300.0, 20.0) == pytest.approx(0.01)          # below the minimum lot: fallback trades 0.01


def test_without_the_fallback_a_trade_smaller_than_the_minimum_lot_is_skipped():
    r = DailyRiskRules(risk_pct=1.0, max_trades=0, stop_after_wins=0)
    assert lots_for(r, 1000.0, 20.0) is None and r.stats["skipped_stop_too_wide"] == 1
    assert lots_for(r, 2000.0, 20.0) == pytest.approx(0.01)


def test_drawdown_brake_halves_the_risk_below_the_high():
    r = DailyRiskRules(risk_pct=10.0, dd_brake=0.15, max_trades=0, stop_after_wins=0)
    assert lots_for(r, 2000.0, 20.0) == pytest.approx(0.10)
    assert lots_for(r, 1700.0, 20.0) == pytest.approx(0.04)         # 15% below 2000: 5% of 1700 = $85
    assert lots_for(r, 1900.0, 20.0) == pytest.approx(0.09)


def test_max_stop_skips_wide_stops_and_zero_daily_loss_means_no_cap():
    r = DailyRiskRules(10, 0, max_trades=0, stop_after_wins=0, max_stop=5.0)
    assert lots_for(r, 10_000.0, 6.0) is None
    assert lots_for(r, 10_000.0, 4.0) == pytest.approx(0.02)        # $10 / ($4 x 100) = 0.025 -> 0.02
    r.days[r.day_of(D1)].pnl = -500.0                               # no cap: a big losing day does not block
    assert lots_for(r, 10_000.0, 4.0) == pytest.approx(0.02)


def test_defaults_and_positional_arguments_are_unchanged():
    assert DailyRiskRules().max_daily_loss == 20.0
    r = DailyRiskRules(10, 20, 2, True)
    assert (r.risk_per_trade, r.max_daily_loss, r.max_trades, r.stop_after_wins, r.risk_pct) == (10, 20, 2, 1, 0.0)
    r._state(r.day_of(D1)).pnl = -15.0
    assert lots_for(r, 10_000.0, 1.0) == pytest.approx(0.05)        # only $5 of the $20 day is left
