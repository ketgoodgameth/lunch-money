"""Tick-level simulated broker used by BOTH backtests and live (forward) tests.

Fill rules (one tick = one real bid/ask quote, so there is no intrabar guessing):
  * Market buy fills at ask + slippage; market sell at bid - slippage.
  * Buy limit triggers when ask <= limit and fills at that ask (never worse than the limit).
    Sell limit triggers when bid >= limit and fills at that bid.
  * Buy stop triggers when ask >= stop and fills at ask + slippage (gaps fill worse, as in reality).
    Sell stop triggers when bid <= stop and fills at bid - slippage.
  * Long positions close on the bid, shorts on the ask. A stop loss behaves like a stop order
    (slippage, gap-through), a take profit like a limit order.
  * Latency: an order or close request sent at time t is acted on at the first tick with
    ts >= t + latency_ms. With latency 0 a market order fills on the tick the strategy saw.
  * Commission is charged per lot per side. Swap is applied at the 17:00 New York rollover,
    with a triple charge on ``triple_swap_weekday`` (Wednesday by default).
  * Margin is checked on every fill; a margin level below ``stop_out_level`` closes the worst
    position until the account is safe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import (BUY, SELL, EquityPoint, Order, OrderStatus, OrderType, Position, Tick, Trade)

NEW_YORK = ZoneInfo("America/New_York")


@dataclass
class BrokerConfig:
    initial_balance: float = 10_000.0
    contract_size: float = 100.0         # ounces per lot
    commission_per_lot: float = 3.5      # USD per lot per side
    slippage: float = 0.0                # USD/oz, applied adversely to market and stop fills
    extra_spread: float = 0.0            # USD/oz added to the raw spread (broker markup)
    zero_spread: bool = False            # quote both sides at the mid price (hypothetical)
    fixed_spread: float | None = None    # re-quote every tick at this spread around its mid price
    latency_ms: int = 0
    leverage: float = 100.0              # float("inf") = unlimited: no margin, stop-out at zero equity
    stop_out_level: float = 0.5          # equity / used margin
    swap_long: float = 0.0               # USD per lot per night (negative = you pay)
    swap_short: float = 0.0
    triple_swap_weekday: int = 2         # Monday=0 ... Wednesday=2
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0
    equity_sample_ms: int = 60_000
    # Daily account (e.g. a $20-a-day Exness account with negative balance protection): each day the
    # account holds `daily_account` USD at risk. Equity reaching 0 stops out every position, any loss
    # beyond that is written off, and no new orders are accepted until the next day. Profits are
    # treated as withdrawn, so every day starts with the same amount at risk.
    daily_account: float | None = None
    day_start_hour: float = 0.0          # UTC hour the account day starts


def next_rollover_ms(ts: int) -> int:
    """Next 17:00 America/New_York strictly after ts (DST aware)."""
    now = datetime.fromtimestamp(ts / 1000, tz=NEW_YORK)
    cand = datetime(now.year, now.month, now.day, 17, tzinfo=NEW_YORK)
    if cand <= now:
        d = now.date() + timedelta(days=1)
        cand = datetime(d.year, d.month, d.day, 17, tzinfo=NEW_YORK)
    return int(cand.timestamp() * 1000)


class SimBroker:
    def __init__(self, config: BrokerConfig | None = None):
        self.cfg = config or BrokerConfig()
        self.balance = self.cfg.initial_balance
        self.positions: list[Position] = []
        self.orders: list[Order] = []            # resting/pending orders
        self.trades: list[Trade] = []
        self.equity_curve: list[EquityPoint] = []
        self.events: list[tuple] = []            # drained by the engine
        self.tick: Tick | None = None
        self.rejections = 0
        self._close_requests: list[tuple[int, int, str]] = []  # (active_ts, position_id, reason)
        self._next_id = 1
        self._next_rollover: int | None = None
        self._next_sample: int | None = None
        self._excursion: dict[int, list[float]] = {}  # position id -> [mae, mfe]
        self._day: int | None = None
        self._day_floor: float | None = None   # equity at which the daily account is at $0
        self._day_dead = False
        self.nbp_written_off = 0.0

    # ------------------------------------------------------------------ helpers
    def _id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def adjust(self, tick: Tick) -> Tick:
        """Apply the configured spread markup to a raw quote."""
        if self.cfg.zero_spread:
            m = (tick.bid + tick.ask) * 0.5
            return Tick(tick.ts, m, m, tick.bid_vol, tick.ask_vol)
        if self.cfg.fixed_spread is not None:
            m, h = (tick.bid + tick.ask) * 0.5, self.cfg.fixed_spread * 0.5
            return Tick(tick.ts, m - h, m + h, tick.bid_vol, tick.ask_vol)
        if self.cfg.extra_spread:
            h = self.cfg.extra_spread / 2
            return Tick(tick.ts, tick.bid - h, tick.ask + h, tick.bid_vol, tick.ask_vol)
        return tick

    def _round_lots(self, lots: float) -> float:
        step = self.cfg.lot_step
        return round(round(lots / step) * step, 8)

    def equity(self, tick: Tick | None = None) -> float:
        tick = tick or self.tick
        if tick is None:
            return self.balance
        cs = self.cfg.contract_size
        return self.balance + sum(p.unrealized(tick, cs) for p in self.positions)

    def used_margin(self, tick: Tick | None = None) -> float:
        tick = tick or self.tick
        if tick is None:
            return 0.0
        return sum(p.lots for p in self.positions) * self.cfg.contract_size * tick.mid / self.cfg.leverage

    def net_lots(self) -> float:
        return round(sum(p.side * p.lots for p in self.positions), 8)

    # ------------------------------------------------------------------ order entry
    def submit(self, side: int, lots: float, type: OrderType = OrderType.MARKET, price: float | None = None,
               sl: float | None = None, tp: float | None = None, expiry_ms: int | None = None,
               tag: str = "") -> Order:
        if self.tick is None:
            raise RuntimeError("cannot trade before the first tick")
        if self._day_dead:
            order = Order(self._id(), side, self._round_lots(lots), type, price, sl, tp, self.tick.ts,
                          self.tick.ts, expiry_ms, tag)
            self._reject(order, "daily account is at $0 (stopped out); trading resumes next day")
            return order
        if side not in (BUY, SELL):
            raise ValueError("side must be BUY (1) or SELL (-1)")
        if type is not OrderType.MARKET and price is None:
            raise ValueError(f"{type.value} order needs a price")
        now = self.tick.ts
        order = Order(self._id(), side, self._round_lots(lots), type, price, sl, tp, now,
                      now + self.cfg.latency_ms, expiry_ms, tag)
        if not self.cfg.min_lot <= order.lots <= self.cfg.max_lot:
            self._reject(order, f"lot size {lots} outside [{self.cfg.min_lot}, {self.cfg.max_lot}]")
            return order
        self.orders.append(order)
        if self.cfg.latency_ms == 0:
            self._process_orders(self.tick)
        return order

    def cancel(self, order: Order) -> None:
        if order.status is OrderStatus.PENDING and order in self.orders:
            self.orders.remove(order)
            order.status = OrderStatus.CANCELLED
            self.events.append(("cancel", order))

    def close(self, position: Position, reason: str = "signal") -> None:
        if position not in self.positions or position.closing:
            return
        position.closing = True
        self._close_requests.append((self.tick.ts + self.cfg.latency_ms, position.id, reason))
        if self.cfg.latency_ms == 0:
            self._process_closes(self.tick)

    def close_all(self, reason: str = "signal") -> None:
        for p in list(self.positions):
            self.close(p, reason)

    def flatten(self, reason: str = "end") -> None:
        """Close everything immediately at the current tick, ignoring latency; cancel resting orders."""
        t = self.tick
        for p in list(self.positions):
            price = t.bid - self.cfg.slippage if p.side == BUY else t.ask + self.cfg.slippage
            self._close_position(p, price, t.ts, reason)
        self._close_requests.clear()
        for o in list(self.orders):
            self.cancel(o)

    def modify(self, position: Position, sl: float | None = ..., tp: float | None = ...) -> None:
        """Change SL/TP. Pass None to remove one; omit an argument to keep it."""
        if sl is not ...:
            position.sl = sl
        if tp is not ...:
            position.tp = tp

    # ------------------------------------------------------------------ tick processing
    def on_tick(self, tick: Tick) -> None:
        self.tick = tick
        ts = tick.ts
        if self.cfg.daily_account is not None:
            day = (ts - int(self.cfg.day_start_hour * 3_600_000)) // 86_400_000
            # The day's $20 is only refilled once flat: a trade held past the day boundary keeps using
            # the day it was opened on (the same day the risk rules book it to).
            if day != self._day and not self.positions:
                self._day, self._day_dead = day, False
                self._day_floor = self.balance - self.cfg.daily_account
        if self._next_rollover is None:
            self._next_rollover = next_rollover_ms(ts)
            self._next_sample = ts
        while ts >= self._next_rollover:
            self._apply_swap(self._next_rollover)
            self._next_rollover = next_rollover_ms(self._next_rollover)
        if self._close_requests:
            self._process_closes(tick)
        if self.orders:
            self._process_orders(tick)
        if self.positions:
            self._check_exits(tick)
            self._check_margin(tick)
        if self.positions and self._day_floor is not None and self.equity(tick) <= self._day_floor:
            self._daily_stop_out(tick)
        if ts >= self._next_sample:
            self.equity_curve.append(EquityPoint(ts, self.balance, self.equity(tick)))
            self._next_sample = ts - ts % self.cfg.equity_sample_ms + self.cfg.equity_sample_ms

    def _process_orders(self, tick: Tick) -> None:
        bid, ask, slip = tick.bid, tick.ask, self.cfg.slippage
        for o in list(self.orders):
            if o.expiry_ts is not None and tick.ts >= o.expiry_ts:
                self.orders.remove(o)
                o.status = OrderStatus.EXPIRED
                self.events.append(("expire", o))
                continue
            if tick.ts < o.active_ts:
                continue
            fill = None
            if o.type is OrderType.MARKET:
                fill = ask + slip if o.side == BUY else bid - slip
            elif o.type is OrderType.LIMIT:
                if o.side == BUY and ask <= o.price:
                    fill = ask
                elif o.side == SELL and bid >= o.price:
                    fill = bid
            else:  # STOP
                if o.side == BUY and ask >= o.price:
                    fill = ask + slip
                elif o.side == SELL and bid <= o.price:
                    fill = bid - slip
            if fill is not None:
                self.orders.remove(o)
                self._fill(o, fill, tick)

    def _fill(self, o: Order, price: float, tick: Tick) -> None:
        cfg = self.cfg
        need = o.lots * cfg.contract_size * tick.mid / cfg.leverage
        free = self.equity(tick) - self.used_margin(tick)
        if need > free:
            self._reject(o, f"insufficient margin: need {need:,.2f}, free {free:,.2f}")
            return
        commission = cfg.commission_per_lot * o.lots
        self.balance -= commission
        o.status, o.fill_price, o.fill_ts = OrderStatus.FILLED, price, tick.ts
        pos = Position(self._id(), o.side, o.lots, price, tick.ts, o.sl, o.tp, commission, 0.0, o.tag, o.id,
                       initial_sl=o.sl)
        self.positions.append(pos)
        self._excursion[pos.id] = [0.0, 0.0]
        self.events.append(("fill", o, pos))

    def _reject(self, o: Order, reason: str) -> None:
        o.status, o.reason = OrderStatus.REJECTED, reason
        self.rejections += 1
        self.events.append(("reject", o, reason))

    def _process_closes(self, tick: Tick) -> None:
        remaining = []
        for active_ts, pid, reason in self._close_requests:
            if tick.ts < active_ts:
                remaining.append((active_ts, pid, reason))
                continue
            pos = next((p for p in self.positions if p.id == pid), None)
            if pos is not None:
                price = tick.bid - self.cfg.slippage if pos.side == BUY else tick.ask + self.cfg.slippage
                self._close_position(pos, price, tick.ts, reason)
        self._close_requests = remaining

    def _check_exits(self, tick: Tick) -> None:
        bid, ask, slip = tick.bid, tick.ask, self.cfg.slippage
        for p in list(self.positions):
            exc = self._excursion[p.id]
            if p.side == BUY:
                move = bid - p.entry_price
                if p.sl is not None and bid <= p.sl:
                    self._close_position(p, bid - slip, tick.ts, "sl")
                    continue
                if p.tp is not None and bid >= p.tp:
                    self._close_position(p, bid, tick.ts, "tp")
                    continue
            else:
                move = p.entry_price - ask
                if p.sl is not None and ask >= p.sl:
                    self._close_position(p, ask + slip, tick.ts, "sl")
                    continue
                if p.tp is not None and ask <= p.tp:
                    self._close_position(p, ask, tick.ts, "tp")
                    continue
            if move < exc[0]:
                exc[0] = move
            elif move > exc[1]:
                exc[1] = move

    def _check_margin(self, tick: Tick) -> None:
        while self.positions:
            used, eq = self.used_margin(tick), self.equity(tick)
            if used > 0:
                if eq / used >= self.cfg.stop_out_level:
                    return
            elif eq > 0:  # unlimited leverage: no margin is used, so stop out only when equity is gone
                return
            cs = self.cfg.contract_size
            worst = min(self.positions, key=lambda p: p.unrealized(tick, cs))
            price = tick.bid - self.cfg.slippage if worst.side == BUY else tick.ask + self.cfg.slippage
            self._close_position(worst, price, tick.ts, "stop_out")

    def _daily_stop_out(self, tick: Tick) -> None:
        """Daily account equity hit $0: close everything at market, cancel orders, stop for the day."""
        for p in list(self.positions):
            price = tick.bid - self.cfg.slippage if p.side == BUY else tick.ask + self.cfg.slippage
            self._close_position(p, price, tick.ts, "stop_out")
        self._close_requests.clear()
        for o in list(self.orders):
            self.cancel(o)
        self._day_dead = True

    def _close_position(self, p: Position, price: float, ts: int, reason: str) -> None:
        cfg = self.cfg
        gross = (price - p.entry_price) * p.side * p.lots * cfg.contract_size
        commission = cfg.commission_per_lot * p.lots
        self.balance += gross - commission
        p.commission += commission
        self.positions.remove(p)
        credit = 0.0
        if self._day_floor is not None and not self.positions and self.balance <= self._day_floor + 1e-9:
            # Negative balance protection: the account can't go below $0; the broker writes off the rest.
            credit = self._day_floor - self.balance
            self.balance = self._day_floor
            self.nbp_written_off += credit
            self._day_dead = True
        mae, mfe = self._excursion.pop(p.id, [0.0, 0.0])
        final = (price - p.entry_price) * p.side
        trade = Trade(p.id, p.side, p.lots, p.entry_ts, p.entry_price, ts, price, gross, p.commission, p.swap,
                      reason, p.tag, mae=min(mae, final), mfe=max(mfe, final), nbp_credit=credit,
                      initial_sl=p.initial_sl)
        self.trades.append(trade)
        self.equity_curve.append(EquityPoint(ts, self.balance, self.equity()))
        self.events.append(("close", trade))

    def _apply_swap(self, rollover_ms: int) -> None:
        if not self.positions:
            return
        weekday = datetime.fromtimestamp(rollover_ms / 1000, tz=NEW_YORK).weekday()
        if weekday >= 5:
            return
        mult = 3 if weekday == self.cfg.triple_swap_weekday else 1
        for p in self.positions:
            rate = self.cfg.swap_long if p.side == BUY else self.cfg.swap_short
            amount = rate * p.lots * mult
            p.swap += amount
            self.balance += amount
