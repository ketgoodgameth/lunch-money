"""Base class for strategies. Subclass it and override the ``on_*`` hooks you need.

The same strategy object runs unchanged in a backtest and a live test.

    class MyStrategy(Strategy):
        timeframe = "5min"            # optional: get on_bar() calls
        params = {"lots": 0.1}        # defaults, overridable from the CLI with --param lots=0.2

        def on_bar(self, bar):
            if not self.positions and bar.close > bar.open:
                self.buy(self.p.lots, sl=bar.close - 5, tp=bar.close + 10)
"""
from __future__ import annotations

import logging
from collections import deque
from types import SimpleNamespace
from typing import TYPE_CHECKING

from .models import BUY, SELL, Bar, Order, OrderType, Position, Tick, Trade, ms_to_dt

if TYPE_CHECKING:
    from .broker import SimBroker


class Strategy:
    timeframe: str | int | None = None
    bar_history: int = 2000
    params: dict = {}

    def __init__(self, **params):
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValueError(f"{type(self).__name__} has no parameter(s) {sorted(unknown)}; "
                             f"known: {sorted(self.params)}")
        self.p = SimpleNamespace(**{**self.params, **params})
        self.bars: deque[Bar] = deque(maxlen=self.bar_history)
        self.broker: SimBroker | None = None
        self.risk = None  # optional xautest.risk.DailyRiskRules; set by the engine/CLI
        self.live = False
        self.log = logging.getLogger(f"strategy.{type(self).__name__}")

    # --------------------------------------------------------------- hooks
    def on_start(self) -> None: ...
    def on_tick(self, tick: Tick) -> None: ...
    def on_bar(self, bar: Bar) -> None: ...
    def on_fill(self, order: Order, position: Position) -> None: ...
    def on_trade(self, trade: Trade) -> None: ...
    def on_reject(self, order: Order, reason: str) -> None:
        n = self.__dict__.get("_rejections_logged", 0) + 1
        self._rejections_logged = n
        if n <= 5:
            self.log.warning("order %s rejected: %s", order.id, reason)
        elif n == 6:
            self.log.warning("more orders rejected; further warnings suppressed (see rejected_orders in the summary)")
    def on_stop(self) -> None: ...

    # --------------------------------------------------------------- state
    @property
    def tick(self) -> Tick:
        return self.broker.tick

    @property
    def now(self) -> int:
        return self.broker.tick.ts

    @property
    def time(self):
        return ms_to_dt(self.broker.tick.ts)

    @property
    def positions(self) -> list[Position]:
        return self.broker.positions

    @property
    def orders(self) -> list[Order]:
        return self.broker.orders

    @property
    def net_lots(self) -> float:
        return self.broker.net_lots()

    @property
    def balance(self) -> float:
        return self.broker.balance

    @property
    def equity(self) -> float:
        return self.broker.equity()

    # --------------------------------------------------------------- actions
    def _order(self, side, lots, type, price, sl, tp, expiry_ms, tag) -> Order | None:
        """Send an order. With risk rules active, returns None when the rules block it, and
        replaces ``lots`` with the risk-based size."""
        if self.risk is not None:
            sized = self.risk.approve(self.broker, side, type, price, sl, lots=lots)
            if sized is None:
                return None
            order = self.broker.submit(side, sized, type, price, sl, tp, expiry_ms, tag)
            self.risk.submitted(order)
            return order
        return self.broker.submit(side, lots, type, price, sl, tp, expiry_ms, tag)

    def buy(self, lots: float, sl: float | None = None, tp: float | None = None, tag: str = "") -> Order | None:
        return self._order(BUY, lots, OrderType.MARKET, None, sl, tp, None, tag)

    def sell(self, lots: float, sl: float | None = None, tp: float | None = None, tag: str = "") -> Order | None:
        return self._order(SELL, lots, OrderType.MARKET, None, sl, tp, None, tag)

    def buy_limit(self, price: float, lots: float, sl=None, tp=None, expiry_ms: int | None = None, tag=""):
        return self._order(BUY, lots, OrderType.LIMIT, price, sl, tp, expiry_ms, tag)

    def sell_limit(self, price: float, lots: float, sl=None, tp=None, expiry_ms: int | None = None, tag=""):
        return self._order(SELL, lots, OrderType.LIMIT, price, sl, tp, expiry_ms, tag)

    def buy_stop(self, price: float, lots: float, sl=None, tp=None, expiry_ms: int | None = None, tag=""):
        return self._order(BUY, lots, OrderType.STOP, price, sl, tp, expiry_ms, tag)

    def sell_stop(self, price: float, lots: float, sl=None, tp=None, expiry_ms: int | None = None, tag=""):
        return self._order(SELL, lots, OrderType.STOP, price, sl, tp, expiry_ms, tag)

    def close(self, position: Position, reason: str = "signal") -> None:
        self.broker.close(position, reason)

    def close_all(self, reason: str = "signal") -> None:
        self.broker.close_all(reason)

    def cancel(self, order: Order) -> None:
        self.broker.cancel(order)

    def cancel_all(self) -> None:
        for o in list(self.broker.orders):
            self.broker.cancel(o)

    def modify(self, position: Position, sl=..., tp=...) -> None:
        self.broker.modify(position, sl=sl, tp=tp)
