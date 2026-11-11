"""Example: EMA crossover on 5-minute bars with ATR stops.

Shows the bar API (on_bar), indicators, a spread filter on live quotes (on_tick data via
self.tick), a session filter and one position at a time. It is a template, not an edge.
"""
from xautest.indicators import ATR, EMA
from xautest.strategy import Strategy


class EmaCross(Strategy):
    timeframe = "5min"
    params = {
        "fast": 20,
        "slow": 50,
        "atr_period": 14,
        "sl_atr": 2.0,       # stop loss distance in ATRs
        "tp_atr": 3.0,       # take profit distance in ATRs
        "lots": 0.10,
        "max_spread": 0.50,  # USD/oz; skip entries when the market is wide
        "start_hour": 7,     # UTC session filter for entries
        "end_hour": 20,
    }

    def on_start(self):
        self.fast, self.slow, self.atr = EMA(self.p.fast), EMA(self.p.slow), ATR(self.p.atr_period)
        self.prev_diff = None

    def on_bar(self, bar):
        f, s, atr = self.fast.update(bar.close), self.slow.update(bar.close), self.atr.update(bar)
        diff = f - s
        prev, self.prev_diff = self.prev_diff, diff
        if prev is None or not (self.slow.ready and self.atr.ready):
            return

        crossed_up, crossed_down = prev <= 0 < diff, prev >= 0 > diff
        pos = self.positions[0] if self.positions else None

        # Exit on opposite signal.
        if pos is not None and ((pos.side > 0 and crossed_down) or (pos.side < 0 and crossed_up)):
            self.close(pos)
            pos = None

        if pos is not None or not (crossed_up or crossed_down):
            return
        if not self.p.start_hour <= self.time.hour < self.p.end_hour:
            return
        t = self.tick
        if t.spread > self.p.max_spread:
            return
        if crossed_up:
            self.buy(self.p.lots, sl=t.ask - self.p.sl_atr * atr, tp=t.ask + self.p.tp_atr * atr)
        else:
            self.sell(self.p.lots, sl=t.bid + self.p.sl_atr * atr, tp=t.bid - self.p.tp_atr * atr)
