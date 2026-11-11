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
