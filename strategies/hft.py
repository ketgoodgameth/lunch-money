"""HFT-style tick bot: reacts to bursts in the tick stream and holds for seconds to a few minutes.

  signal    the mid price moved at least `trigger` $/oz over the last `window_ms` (measured tick by tick)
  modes
    momentum  trade in the direction of the burst, at market
    fade      trade against the burst, at market
    fade_limit  trade against the burst with a resting limit order `limit_off` $/oz further out (the burst has
                to keep going to fill it); the order is cancelled after `limit_ttl_ms`
  exits     take profit `tp` and stop `sl` ($/oz from the price at the signal), or after `hold_ms`,
            or everything closed at flat_hour UTC
  filters   entries only inside `sessions` (UTC); skip if the spread is wider than `max_spread`;
            `cooldown_ms` after every exit
One position at a time. Size and the daily loss cap come from the daily rules outside the strategy.

This is "high frequency" only as far as a retail account allows: it sees one quote stream (no order book, no
queue position) and every order arrives after the broker's delay.
"""
from collections import deque

from xautest.models import BUY, SELL
from xautest.strategy import Strategy

DAY, MIN = 86_400_000, 60_000


def parse_sessions(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in filter(None, (x.strip() for x in spec.split(","))):
        a, b = part.split("-")
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        out.append(((ha * 60 + ma) * MIN, (hb * 60 + mb) * MIN))
    return out


class HFT(Strategy):
    timeframe = None

    params = {
        "mode": "momentum",        # momentum | fade | fade_limit
        "lots": 0.01,
        "window_ms": 1_000,
        "trigger": 0.4,            # $/oz move of the mid price inside the window
        "tp": 0.5,                 # $/oz
        "sl": 0.5,                 # $/oz
        "hold_ms": 60_000,
        "cooldown_ms": 5_000,
        "limit_off": 0.2,          # fade_limit: how much further the burst must go to fill the order
        "limit_ttl_ms": 5_000,
        "sessions": "01:00-20:00",
        "flat_hour": 20,
        "max_spread": 0.5,         # $/oz
    }

    def on_start(self):
        p = self.p
        self.win: deque = deque()
        self.windows = parse_sessions(p.sessions)
        self.flat_ms = p.flat_hour * 3_600_000
        self.next_ok = 0
        self.exit_at = None
        self.stats = {"signals": 0, "entries": 0, "skipped_spread": 0, "time_exits": 0, "limit_fills": 0}

    def on_fill(self, order, position):
        self.exit_at = position.entry_ts + self.p.hold_ms
        if order.price is not None:
            self.stats["limit_fills"] += 1

    def on_trade(self, trade):
        self.exit_at = None
        self.next_ok = trade.exit_ts + self.p.cooldown_ms

    def on_tick(self, t):
        ts = t.ts
        mid = (t.bid + t.ask) * 0.5
        win = self.win
        win.append((ts, mid))
        cut = ts - self.p.window_ms
        while win[0][0] < cut:
            win.popleft()
        if self.exit_at is not None:                      # in a trade: time exit or end of day
            if ts >= self.exit_at or ts % DAY >= self.flat_ms:
                self.exit_at = None
                if self.positions:
                    self.close_all("time")
                    self.stats["time_exits"] += 1
            return
        move = mid - win[0][1]
        p = self.p
        if -p.trigger < move < p.trigger or ts < self.next_ok or self.positions or self.orders:
            return
        tod = ts % DAY
        if tod >= self.flat_ms or not any(a <= tod < b for a, b in self.windows):
            return
        self.stats["signals"] += 1
        if t.ask - t.bid > p.max_spread:
            self.stats["skipped_spread"] += 1
            return
        up = move > 0
        if p.mode == "momentum":
            o = self.buy(p.lots, sl=t.ask - p.sl, tp=t.ask + p.tp) if up else \
                self.sell(p.lots, sl=t.bid + p.sl, tp=t.bid - p.tp)
        elif p.mode == "fade":
            o = self.sell(p.lots, sl=t.bid + p.sl, tp=t.bid - p.tp) if up else \
                self.buy(p.lots, sl=t.ask - p.sl, tp=t.ask + p.tp)
        else:
            if up:
                px = t.bid + p.limit_off
                o = self.sell_limit(px, p.lots, sl=px + p.sl, tp=px - p.tp, expiry_ms=ts + p.limit_ttl_ms)
            else:
                px = t.ask - p.limit_off
                o = self.buy_limit(px, p.lots, sl=px - p.sl, tp=px + p.tp, expiry_ms=ts + p.limit_ttl_ms)
            self.next_ok = ts + p.limit_ttl_ms          # one resting order per burst
        if o is not None:
            self.stats["entries"] += 1
