"""Core data types shared by backtests and live tests.

All timestamps are integer milliseconds since the Unix epoch, UTC.
All prices are USD per troy ounce. One lot = ``contract_size`` ounces (100 by default).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

BUY = 1
SELL = -1


def ms_to_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)


def dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(round(dt.timestamp() * 1000))


class Tick:
    """One top-of-book quote. Kept as a slotted class because millions are created per run."""

    __slots__ = ("ts", "bid", "ask", "bid_vol", "ask_vol")

    def __init__(self, ts: int, bid: float, ask: float, bid_vol: float = 0.0, ask_vol: float = 0.0):
        self.ts = ts
        self.bid = bid
        self.ask = ask
        self.bid_vol = bid_vol
        self.ask_vol = ask_vol

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) * 0.5

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def time(self) -> datetime:
        return ms_to_dt(self.ts)

    def __repr__(self) -> str:
        return f"Tick({self.time.isoformat()}, bid={self.bid:.3f}, ask={self.ask:.3f})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Tick) and (self.ts, self.bid, self.ask) == (other.ts, other.bid, other.ask)


@dataclass(slots=True)
class Bar:
    """OHLC bar built from bid prices. ``ts`` is the bar's open time."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    ticks: int = 0
    spread: float = 0.0  # spread of the last tick in the bar

    @property
    def time(self) -> datetime:
        return ms_to_dt(self.ts)


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(slots=True)
class Order:
    id: int
    side: int
    lots: float
    type: OrderType
    price: float | None
    sl: float | None
    tp: float | None
    submit_ts: int
    active_ts: int          # when the order reaches the (simulated) server
    expiry_ts: int | None = None
    tag: str = ""
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_ts: int | None = None
    reason: str = ""


@dataclass(slots=True)
class Position:
    id: int
    side: int
    lots: float
    entry_price: float
    entry_ts: int
    sl: float | None = None
    tp: float | None = None
    commission: float = 0.0   # cost paid so far, positive number
    swap: float = 0.0         # signed: positive = credit
    tag: str = ""
    order_id: int = 0
    closing: bool = False     # a close request is in flight
    initial_sl: float | None = None  # stop loss at entry (sl may later trail)

    def unrealized(self, tick: Tick, contract_size: float) -> float:
        if self.side == BUY:
            return (tick.bid - self.entry_price) * self.lots * contract_size
        return (self.entry_price - tick.ask) * self.lots * contract_size


@dataclass(slots=True)
class Trade:
    """A closed position."""

    position_id: int
    side: int
    lots: float
    entry_ts: int
    entry_price: float
    exit_ts: int
    exit_price: float
    gross_pnl: float
    commission: float
    swap: float
    reason: str
    tag: str = ""
    mae: float = 0.0  # max adverse excursion in price units
    mfe: float = 0.0  # max favourable excursion in price units
    nbp_credit: float = 0.0  # loss written off by negative balance protection (daily account mode)
    initial_sl: float | None = None

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.commission + self.swap + self.nbp_credit

    @property
    def exit_type(self) -> str:
        """Plain-English exit: a stop hit after it was trailed into profit is a 'trailing stop'."""
        if self.reason == "sl" and self.gross_pnl > 0:
            return "trailing stop (profit locked)"
        return {"sl": "stop loss", "tp": "take profit", "eod": "end of day close", "stop_out": "account stop-out",
                "signal": "closed by strategy", "end": "end of data"}.get(self.reason, self.reason)

    def as_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "side": "BUY" if self.side == BUY else "SELL",
            "lots": self.lots,
            "entry_time": ms_to_dt(self.entry_ts).isoformat(),
            "entry_price": round(self.entry_price, 3),
            "initial_stop": round(self.initial_sl, 3) if self.initial_sl is not None else None,
            "risk_usd": round(abs(self.entry_price - self.initial_sl) * self.lots * 100, 2) if self.initial_sl else None,
            "exit_time": ms_to_dt(self.exit_ts).isoformat(),
            "exit_price": round(self.exit_price, 3),
            "gross_pnl": round(self.gross_pnl, 2),
            "commission": round(self.commission, 2),
            "swap": round(self.swap, 2),
            "nbp_credit": round(self.nbp_credit, 2),
            "net_pnl": round(self.net_pnl, 2),
            "reason": self.reason,
            "exit_type": self.exit_type,
            "tag": self.tag,
            "mae": round(self.mae, 3),
            "mfe": round(self.mfe, 3),
            "duration_s": (self.exit_ts - self.entry_ts) / 1000.0,
        }


@dataclass
class EquityPoint:
    ts: int
    balance: float
    equity: float


@dataclass
class RunResult:
    trades: list[Trade] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    initial_balance: float = 0.0
    ticks_processed: int = 0
    first_ts: int | None = None
    last_ts: int | None = None
    stopped_reason: str = "end of data"
    rejections: int = 0
