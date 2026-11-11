"""Example: Asian-range breakout, written tick by tick.

1. 00:00-07:00 UTC: track the Asian session high/low from live quotes.
2. 07:00 UTC: place a buy stop above the high and a sell stop below the low
   (one-cancels-the-other: when one fills, the other is cancelled).
3. Size each trade so hitting the stop loses ``risk_pct`` of equity.
4. Move the stop to breakeven, then trail it, as the trade moves in our favour.
5. Close anything still open at ``exit_hour``.

It shows pending orders, on_fill, modify(), risk sizing and fast integer time
handling. It is a template to learn the API, not a tested edge.
"""
from xautest.strategy import Strategy

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


class AsiaBreakout(Strategy):
    # No timeframe: this strategy works on ticks only, so on_bar is never called.
    params = {
        "range_end_hour": 7,     # UTC hour the Asian range ends and orders go in
        "order_expiry_hour": 12, # unfilled breakout orders are cancelled at this UTC hour
        "exit_hour": 20,         # flatten at this UTC hour
        "buffer": 0.30,          # USD/oz beyond the range for the entry stop
        "min_range": 5.0,        # skip days whose range is too tight (USD/oz)...
        "max_range": 40.0,       # ...or too wide
        "tp_mult": 1.5,          # take profit = tp_mult x range
        "breakeven_mult": 0.7,   # move SL to entry once profit >= this x range
        "trail_mult": 0.8,       # afterwards trail SL this many ranges behind price
        "risk_pct": 0.5,         # % of equity lost if the stop is hit
        "max_spread": 0.60,      # don't place orders when the market is this wide
    }

    def on_start(self):
        self.day = None          # UTC day number we are tracking
        self.hi = self.lo = None # Asian range
        self.placed = False      # breakout orders already sent today
        self.range = 0.0

    def on_tick(self, tick):
        p = self.p
        day, hour = tick.ts // DAY_MS, (tick.ts % DAY_MS) // HOUR_MS

        if day != self.day:                         # new UTC day: reset the range
            self.day, self.hi, self.lo, self.placed = day, None, None, False

        if hour < p.range_end_hour:                 # 1. build the range from bids
            self.hi = tick.bid if self.hi is None else max(self.hi, tick.bid)
            self.lo = tick.bid if self.lo is None else min(self.lo, tick.bid)
            return

        if hour >= p.exit_hour:                     # 5. end of day
            if self.positions or self.orders:
                self.cancel_all()
                self.close_all("eod")
            return

        if not self.placed and self.hi is not None: # 2. place the breakout orders once
            self.placed = True
            self.place_orders(tick, day)

        for pos in self.positions:                  # 4. breakeven and trailing stop
            self.manage(pos, tick)

    def place_orders(self, tick, day):
        p = self.p
        rng = self.hi - self.lo
        if not p.min_range <= rng <= p.max_range or tick.spread > p.max_spread:
            return
        self.range = rng
        expiry = day * DAY_MS + p.order_expiry_hour * HOUR_MS
        buy_at, sell_at = self.hi + p.buffer, self.lo - p.buffer
        lots = self.lots_for_risk(stop_distance=buy_at - sell_at)
        if lots <= 0:
            return
        # Only place a side if price hasn't already broken out (a stop order beyond the
        # market would fill instantly, which is not what we want here).
        if tick.ask < buy_at:
            self.buy_stop(buy_at, lots, sl=sell_at, tp=buy_at + p.tp_mult * rng, expiry_ms=expiry, tag="long")
        if tick.bid > sell_at:
            self.sell_stop(sell_at, lots, sl=buy_at, tp=sell_at - p.tp_mult * rng, expiry_ms=expiry, tag="short")

    def on_fill(self, order, position):
        self.cancel_all()                           # one-cancels-the-other

    def manage(self, pos, tick):
        p, rng = self.p, self.range
        if pos.side > 0:
            profit = tick.bid - pos.entry_price
            new_sl = pos.sl
            if profit >= p.breakeven_mult * rng:
                new_sl = max(pos.sl, pos.entry_price, tick.bid - p.trail_mult * rng)
        else:
            profit = pos.entry_price - tick.ask
            new_sl = pos.sl
            if profit >= p.breakeven_mult * rng:
                new_sl = min(pos.sl, pos.entry_price, tick.ask + p.trail_mult * rng)
        if new_sl != pos.sl:
            self.modify(pos, sl=new_sl)

    def lots_for_risk(self, stop_distance: float) -> float:
        """Lots such that a stop of ``stop_distance`` USD/oz loses risk_pct of equity."""
        contract = self.broker.cfg.contract_size    # 100 oz per lot
        risk_usd = self.equity * self.p.risk_pct / 100
        lots = int(risk_usd / (stop_distance * contract) / 0.01) * 0.01  # round DOWN to 0.01
        return round(lots, 2)

    def on_trade(self, trade):
        if self.live:                               # print closed trades when forward testing
            self.log.info("%s closed: %s %.2f lots, net %.2f", trade.tag, trade.reason, trade.lots, trade.net_pnl)
