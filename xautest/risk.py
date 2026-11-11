"""Daily money-management rules that sit between any strategy and the broker.

Every order a strategy sends goes through ``DailyRiskRules.approve``, which can block it or
resize it. Strategies need no changes; they just need to set a stop loss so the trade can be sized.

Rules (all optional, per day):
  * risk_per_trade    size each trade so hitting its stop loses this many USD
  * max_daily_loss    stop for the day once closed losses reach this; never risk more than what is left
  * max_trades        stop after this many trades (0 = no limit)
  * stop_after_wins   stop after this many winning trades in a row (1 = first win; 0 = never)
  * stop_after_losses stop after this many losing trades in a row (0 = never)
  * weekly_loss_limit / monthly_loss_limit
                      stop trading for the rest of the week (Mon-Sun, UTC) / calendar month once closed losses
                      in that period reach this many USD (0 = off)
  * min_stop          skip trades whose stop is closer than this many USD/oz (e.g. to keep a per-ounce
                      cost below a set share of the risk: $0.20 / 3% = $6.67)
  (stop_after_win=True is the older spelling of stop_after_wins=1)
One position at a time, so each trade's result is known before the next one is allowed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import BUY, OrderType, Trade

DAY_MS = 86_400_000


@dataclass
class DayState:
    trades: int = 0
    pnl: float = 0.0
    won: bool = False
    win_streak: int = 0
    loss_streak: int = 0
    stopped: str = ""          # why trading ended for the day ("" = still open)


@dataclass
class DailyRiskRules:
    risk_per_trade: float = 10.0
    max_daily_loss: float = 20.0
    max_trades: int = 2
    stop_after_win: bool = True
    day_start_hour: float = 0.0   # UTC hour the trading day starts (Exness server time is UTC)
    min_risk: float = 1.0         # don't bother trading with less risk budget than this
    stop_after_wins: int | None = None    # None: 1 if stop_after_win else 0
    stop_after_losses: int = 0
    min_stop: float = 0.0
    fixed_lots: float | None = None   # trade this size regardless of the stop; trades without a stop allowed
    use_strategy_lots: bool = False   # trade the size the strategy asks for; trades without a stop allowed
    weekly_loss_limit: float = 0.0
    monthly_loss_limit: float = 0.0   # trade this size regardless of the stop; trades without a stop allowed

    days: dict[int, DayState] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {
        "approved": 0, "blocked_day_done": 0, "blocked_position_open": 0,
        "skipped_no_stop": 0, "skipped_stop_too_wide": 0, "skipped_stop_too_tight": 0, "blocked_period_limit": 0})
    week_pnl: dict[int, float] = field(default_factory=dict)
    month_pnl: dict[tuple, float] = field(default_factory=dict)
    _order_day: dict[int, int] = field(default_factory=dict)     # order id -> day it was sent
    _position_day: dict[int, int] = field(default_factory=dict)  # position id -> that day

    def __post_init__(self):
        if self.stop_after_wins is None:
            self.stop_after_wins = 1 if self.stop_after_win else 0

    @staticmethod
    def week_of(day: int) -> int:
        return (day + 3) // 7          # day 0 (1970-01-01) was a Thursday; weeks start Monday

    @staticmethod
    def month_of(day: int) -> tuple:
        import datetime as _dt
        d = _dt.date(1970, 1, 1) + _dt.timedelta(days=day)
        return (d.year, d.month)

    def day_of(self, ts: int) -> int:
        return (ts - int(self.day_start_hour * 3_600_000)) // DAY_MS

    def _state(self, day: int) -> DayState:
        return self.days.setdefault(day, DayState())

    def approve(self, broker, side: int, type: OrderType, price: float | None, sl: float | None,
                lots: float | None = None) -> float | None:
        """Return the lot size to use, or None to block the order."""
        tick = broker.tick
        st = self._state(self.day_of(tick.ts))
        if broker.positions or broker.orders:
            self.stats["blocked_position_open"] += 1
            return None
        if st.stopped:
            self.stats["blocked_day_done"] += 1
            return None
        day = self.day_of(tick.ts)
        if (self.weekly_loss_limit and self.week_pnl.get(self.week_of(day), 0.0) <= -self.weekly_loss_limit + 1e-9) or \
                (self.monthly_loss_limit and self.month_pnl.get(self.month_of(day), 0.0) <= -self.monthly_loss_limit + 1e-9):
            self.stats["blocked_period_limit"] += 1
            return None
        if self.use_strategy_lots and lots:
            st.trades += 1
            self.stats["approved"] += 1
            return lots
        if self.fixed_lots:
            st.trades += 1
            self.stats["approved"] += 1
            return self.fixed_lots
        if sl is None:
            self.stats["skipped_no_stop"] += 1
            return None
        budget = min(self.risk_per_trade, self.max_daily_loss + min(st.pnl, 0.0))
        if budget < self.min_risk:
            st.stopped = st.stopped or "loss limit"
            self.stats["blocked_day_done"] += 1
            return None
        entry = price if (type is not OrderType.MARKET and price is not None) else (tick.ask if side == BUY else tick.bid)
        stop_dist = abs(entry - sl)
        cfg = broker.cfg
        if stop_dist <= 0:
            self.stats["skipped_no_stop"] += 1
            return None
        if stop_dist < self.min_stop:
            self.stats["skipped_stop_too_tight"] += 1
            return None
        lots = int(budget / (stop_dist * cfg.contract_size) / cfg.lot_step + 1e-9) * cfg.lot_step
        if lots < cfg.min_lot:
            self.stats["skipped_stop_too_wide"] += 1
            return None
        st.trades += 1
        self.stats["approved"] += 1
        return round(lots, 8)

    def submitted(self, order) -> None:
        self._order_day[order.id] = self.day_of(order.submit_ts)

    def filled(self, order, position) -> None:
        if order.id in self._order_day:
            self._position_day[position.id] = self._order_day.pop(order.id)

    def record(self, trade: Trade) -> None:
        """Book a closed trade against the day its order was sent."""
        day = self._position_day.pop(trade.position_id, self.day_of(trade.entry_ts))
        st = self._state(day)
        st.pnl += trade.net_pnl
        wk, mo = self.week_of(day), self.month_of(day)
        self.week_pnl[wk] = self.week_pnl.get(wk, 0.0) + trade.net_pnl
        self.month_pnl[mo] = self.month_pnl.get(mo, 0.0) + trade.net_pnl
        if trade.net_pnl > 0:
            st.won = True
            st.win_streak, st.loss_streak = st.win_streak + 1, 0
        else:  # a scratch (0) or a loss continues a losing streak
            st.win_streak, st.loss_streak = 0, st.loss_streak + 1
        if st.stopped:
            return
        if self.stop_after_wins and st.win_streak >= self.stop_after_wins:
            st.stopped = "win"
        elif st.pnl <= -self.max_daily_loss + 1e-9:
            st.stopped = "loss limit"
        elif self.stop_after_losses and st.loss_streak >= self.stop_after_losses:
            st.stopped = "losses"
        elif self.max_trades and st.trades >= self.max_trades:
            st.stopped = "max trades"

    def rejected(self, order) -> None:
        """An approved order that never filled (rejected, cancelled, expired) doesn't count as a trade."""
        day = self._order_day.pop(order.id, None)
        if day is not None:
            self._state(day).trades -= 1
