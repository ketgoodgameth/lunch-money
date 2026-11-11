"""Trend pullback on a liquidity sweep, with stops sized by volatility and kept within the $10 cap.

Built from what the 2023-2025 tests showed:
  * the 1-hour SuperTrend direction carried a small edge before costs in all three years -> it sets the bias
  * 1-minute stops ($1-3) let a $0.20 spread eat 7-20% of each trade's risk -> slower setup candles and a
    stop floor in ATR units, so stops are wide compared with costs
  * fixed-dollar settings broke when gold's price level changed -> distances are ATR multiples; the only
    dollar figure is max_stop, the widest stop a 0.01-lot position can have at $10 risk

Long (short is the mirror image):
  1. 1H SuperTrend(10, 3) is up.
  2. A setup candle (`tf`) trades below the lowest low of the previous `lookback` setup candles (a sweep of
     sell-side liquidity) and closes back above that low (rejection).
  3. Buy at market on the first quote after that candle closes, if the spread is normal.
  4. Stop below the sweep low by buffer_atr x ATR, and at least min_stop_atr x ATR from the entry.
     Wider than max_stop USD/oz -> skip the setup.
  5. No take profit: after +trail_start_r x R the stop trails trail_dist_r x R behind the best price.
     Optional flat_hour (UTC) closes anything still open; entry_hours="7-18" limits new entries to
     those UTC hours (intraday trading).
Position size comes from the daily risk rules ($10 per trade), so `lots` is only a placeholder.
"""
from collections import deque

from xautest.bars import BarBuilder
from xautest.indicators import ATR, SuperTrend
from xautest.models import BUY, SELL
from xautest.strategy import Strategy


class TrendPullback(Strategy):
    timeframe = None  # bars are built inside the strategy

    params = {
        "lots": 0.01,
        "tf": "15min",
        "lookback": 8,
        "atr_period": 14,
        "buffer_atr": 0.2,
        "min_stop_atr": 1.0,
        "max_stop": 10.0,
        "trail_start_r": 1.0,
        "trail_dist_r": 0.5,
        "max_spread": 0.60,
        "st_period": 10,
        "st_mult": 3.0,
        "flat_hour": -1,
        "entry_hours": "",
        "trend": "h1_supertrend",   # "none": take sweeps in both directions (a reversal strategy)
    }

    def on_start(self):
        p = self.p
        self.b1h, self.bset = BarBuilder("1h"), BarBuilder(p.tf)
        self.st = SuperTrend(p.st_period, p.st_mult)
        self.atr = ATR(p.atr_period)
        self.hist = deque(maxlen=p.lookback)   # (high, low) of previous setup candles
        self.trail = {}                          # position id -> [R, best price]
        self.stats = {"setups": 0, "entries": 0, "skipped_wide": 0, "skipped_spread": 0, "skipped_hours": 0}
        self.entry_hours = tuple(int(x) for x in p.entry_hours.split("-")) if p.entry_hours else None

    # ------------------------------------------------------------------ bars
    def on_tick(self, tick):
        bar = self.b1h.update(tick)
        if bar is not None:
            self.st.update(bar)
        bar = self.bset.update(tick)
        if bar is not None:
            self.on_setup_bar(bar, tick)
        if self.trail:
            self.manage(tick)
        if self.p.flat_hour >= 0 and (self.positions or self.orders) and (tick.ts // 3_600_000) % 24 == self.p.flat_hour:
            self.cancel_all()
            self.close_all("eod")

    def on_setup_bar(self, bar, tick):
        atr = self.atr.update(bar)
        p, hist = self.p, self.hist
        use_trend = p.trend != "none"
        if (self.st.ready or not use_trend) and self.atr.ready and len(hist) == hist.maxlen \
                and not self.positions and not self.orders:
            lo = min(h[1] for h in hist)
            hi = max(h[0] for h in hist)
            side = 0
            up_ok = not use_trend or self.st.direction == 1
            down_ok = not use_trend or self.st.direction == -1
            if up_ok and bar.low < lo and bar.close > lo:
                side, entry = BUY, tick.ask
                stop = min(bar.low - p.buffer_atr * atr, entry - p.min_stop_atr * atr)
            elif down_ok and bar.high > hi and bar.close < hi:
                side, entry = SELL, tick.bid
                stop = max(bar.high + p.buffer_atr * atr, entry + p.min_stop_atr * atr)
            if side:
                self.stats["setups"] += 1
                h = (tick.ts // 3_600_000) % 24
                if self.entry_hours and not (self.entry_hours[0] <= h < self.entry_hours[1]):
                    self.stats["skipped_hours"] += 1
                elif tick.spread > p.max_spread:
                    self.stats["skipped_spread"] += 1
                elif abs(entry - stop) > p.max_stop:
                    self.stats["skipped_wide"] += 1
                elif (self.buy if side == BUY else self.sell)(p.lots, sl=stop) is not None:
                    self.stats["entries"] += 1
        hist.append((bar.high, bar.low))

    # ------------------------------------------------------------------ exits
    def on_fill(self, order, position):
        if position.sl is not None:
            self.trail[position.id] = [abs(position.entry_price - position.sl), position.entry_price]

    def manage(self, tick):
        p = self.p
        for pos in self.positions:
            st = self.trail.get(pos.id)
            if st is None:
                continue
            r, best = st
            if pos.side == BUY:
                if tick.bid > best:
                    st[1] = best = tick.bid
                if r > 0 and best - pos.entry_price >= p.trail_start_r * r:
                    new_sl = best - p.trail_dist_r * r
                    if new_sl > pos.sl:
                        self.modify(pos, sl=new_sl)
            else:
                if tick.ask < best:
                    st[1] = best = tick.ask
                if r > 0 and pos.entry_price - best >= p.trail_start_r * r:
                    new_sl = best + p.trail_dist_r * r
                    if new_sl < pos.sl:
                        self.modify(pos, sl=new_sl)

    def on_trade(self, trade):
        self.trail.pop(trade.position_id, None)
