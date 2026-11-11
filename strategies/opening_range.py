"""Opening-range breakout in the 1-hour trend direction. One pending order a day, intraday only.

  1. Record the high and low (bid) between range_start and range_start + range_minutes (UTC).
  2. When the range is complete, read the 1-hour SuperTrend(10, 3):
       up   -> buy stop at range high + buffer, stop loss at range low - buffer
       down -> sell stop at range low - buffer, stop loss at range high + buffer
     Skipped if price is already beyond the entry, or the stop would be wider than max_stop USD/oz.
  3. The order expires at entry_end_hour; anything open is closed at flat_hour (UTC).
  4. Exit: trail (after +trail_start_r x R, trail trail_dist_r x R behind the best price) or rr (fixed target).

  stop_frac=0.5 puts the stop halfway between the entry and the far side of the range (1.0 = far side).
  range_tz="America/New_York" anchors range_start to New York local time (follows daylight saving);
  direction="against" trades opposite to the 1-hour trend (a placebo check for the trend filter).
Position size comes from the daily risk rules, so `lots` is only a placeholder.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from xautest.bars import BarBuilder
from xautest.indicators import SuperTrend
from xautest.models import BUY
from xautest.strategy import Strategy

DAY = 86_400_000
MIN = 60_000


class OpeningRange(Strategy):
    timeframe = None

    params = {
        "lots": 0.01,
        "range_start": "07:00",
        "range_minutes": 60,
        "buffer": 0.10,
        "max_stop": 20.0,
        "entry_end_hour": 18,
        "flat_hour": 20,
        "exit": "trail",
        "rr": 2.0,
        "trail_start_r": 1.0,
        "trail_dist_r": 0.5,
        "max_spread": 0.60,
        "range_tz": "UTC",
        "direction": "with",
        "stop_frac": 1.0,
    }

    def on_start(self):
        self.hm = tuple(int(x) for x in self.p.range_start.split(":"))
        self.tz = None if self.p.range_tz == "UTC" else ZoneInfo(self.p.range_tz)
        if self.p.direction not in ("with", "against"):
            raise ValueError("direction must be 'with' or 'against'")
        self._set_range(None)
        self.b1h, self.st = BarBuilder("1h"), SuperTrend(10, 3.0)
        self.day = None
        self.hi = self.lo = None
        self.placed = False
        self.trail = {}
        self.stats = {"days": 0, "orders": 0, "skipped_wide": 0, "skipped_no_trend": 0,
                      "skipped_already_broken": 0, "skipped_spread": 0}

    def on_tick(self, t):
        bar = self.b1h.update(t)
        if bar is not None:
            self.st.update(bar)
        day, tod = t.ts // DAY, t.ts % DAY
        if day != self.day:
            self.day, self.hi, self.lo, self.placed = day, None, None, False
            if self.tz is not None:
                self._set_range(day)
        if self.r0 <= tod < self.r1:
            self.hi = t.bid if self.hi is None else max(self.hi, t.bid)
            self.lo = t.bid if self.lo is None else min(self.lo, t.bid)
        elif tod >= self.r1 and not self.placed and self.hi is not None and tod < self.p.entry_end_hour * 3_600_000:
            self.placed = True
            self.place(t, day)
        if self.trail:
            self.manage(t)
        if tod >= self.p.flat_hour * 3_600_000 and (self.positions or self.orders):
            self.cancel_all()
            self.close_all("eod")

    def _set_range(self, day):
        """Range start as ms after UTC midnight; for a local time zone, recomputed each day (DST)."""
        h, m = self.hm
        if self.tz is None or day is None:
            self.r0 = (h * 60 + m) * MIN
        else:
            d = datetime.fromtimestamp(day * DAY / 1000, tz=timezone.utc).date()
            local = datetime(d.year, d.month, d.day, h, m, tzinfo=self.tz)
            self.r0 = int(local.timestamp() * 1000) - day * DAY
        self.r1 = self.r0 + self.p.range_minutes * MIN

    def place(self, t, day):
        p = self.p
        self.stats["days"] += 1
        if not self.st.ready:
            self.stats["skipped_no_trend"] += 1
            return
        if t.spread > p.max_spread:
            self.stats["skipped_spread"] += 1
            return
        up = (self.st.direction == 1) == (self.p.direction == "with")
        entry = self.hi + p.buffer if up else self.lo - p.buffer
        far = self.lo - p.buffer if up else self.hi + p.buffer
        stop = entry - p.stop_frac * (entry - far)
        if (up and t.ask >= entry) or (not up and t.bid <= entry):
            self.stats["skipped_already_broken"] += 1
            return
        if abs(entry - stop) > p.max_stop:
            self.stats["skipped_wide"] += 1
            return
        tp = entry + (1 if up else -1) * p.rr * abs(entry - stop) if p.exit == "rr" else None
        expiry = day * DAY + p.entry_end_hour * 3_600_000
        order = (self.buy_stop if up else self.sell_stop)(entry, p.lots, sl=stop, tp=tp, expiry_ms=expiry)
        if order is not None:
            self.stats["orders"] += 1

    def on_fill(self, order, position):
        if self.p.exit == "trail" and position.sl is not None:
            self.trail[position.id] = [abs(position.entry_price - position.sl), position.entry_price]

    def manage(self, t):
        p = self.p
        for pos in self.positions:
            st = self.trail.get(pos.id)
            if st is None:
                continue
            r, best = st
            if pos.side == BUY:
                if t.bid > best:
                    st[1] = best = t.bid
                if r > 0 and best - pos.entry_price >= p.trail_start_r * r and best - p.trail_dist_r * r > pos.sl:
                    self.modify(pos, sl=best - p.trail_dist_r * r)
            else:
                if t.ask < best:
                    st[1] = best = t.ask
                if r > 0 and pos.entry_price - best >= p.trail_start_r * r and best + p.trail_dist_r * r < pos.sl:
                    self.modify(pos, sl=best + p.trail_dist_r * r)

    def on_trade(self, trade):
        self.trail.pop(trade.position_id, None)
