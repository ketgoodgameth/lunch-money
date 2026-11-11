"""Scalper: many small trades a day, always with a stop, exits sized to the current bar volatility.

  signal bars   tf ("1min" or "5min"); ATR(14) on those bars sets the take profit and stop:
                tp = tp_atr x ATR, sl = sl_atr x ATR (both from the entry price)
  modes
    fade        price stretched k x ATR away from EMA(ema) -> trade back toward it (mean reversion)
    breakout    bar closes beyond the highest high / lowest low of the last `lookback` bars, in the direction
                of EMA(ema) vs EMA(slow) -> trade the break (momentum)
    pullback    EMA(ema) vs EMA(slow) gives the trend; RSI(rsi_len) dips to `rsi_level` (or 100 - it) against
                it and turns back -> trade with the trend
  filters       entries only inside `sessions` (UTC); skip if the spread is more than max_cost x the target
                (scalps can't carry a big spread); `cooldown` bars after a losing trade
  exits         take profit, stop loss, `max_hold` bars, or everything closed at flat_hour UTC
Entries at market on the bar close. One position at a time; the daily rules (fixed 0.01 lots, $20/day account)
sit outside the strategy.
"""
from collections import deque

from xautest.bars import BarBuilder
from xautest.indicators import ATR, EMA, RSI
from xautest.models import BUY, SELL
from xautest.strategy import Strategy

MIN = 60_000


def parse_sessions(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in filter(None, (x.strip() for x in spec.split(","))):
        a, b = part.split("-")
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        out.append(((ha * 60 + ma) * MIN, (hb * 60 + mb) * MIN))
    return out


class Scalper(Strategy):
    timeframe = None

    params = {
        "mode": "fade",
        "tf": "1min",
        "lots": 0.01,
        "atr_len": 14,
        "tp_atr": 1.0,
        "sl_atr": 1.5,
        "k": 2.0,              # fade: stretch in ATRs
        "ema": 20,
        "slow": 50,
        "lookback": 20,        # breakout
        "rsi_len": 7,          # pullback
        "rsi_level": 30.0,
        "sessions": "07:00-16:00",
        "flat_hour": 20,
        "max_hold": 30,        # bars
        "max_cost": 0.25,      # spread must be <= this share of the take-profit distance
        "cooldown": 0,         # bars to wait after a losing trade
        "min_tp_usd": 0.0,     # never aim for less than this ($/oz)
    }

    def on_start(self):
        p = self.p
        self.bb = BarBuilder(p.tf)
        self.atr = ATR(p.atr_len)
        self.fast, self.slw = EMA(p.ema), EMA(p.slow)
        self.rsi = RSI(p.rsi_len)
        self.hist: deque = deque(maxlen=p.lookback)
        self.windows = parse_sessions(p.sessions)
        self.bar_n = 0
        self.entry_bar = None
        self.wait_until = -1
        self.prev_rsi = None
        self.stats = {"signals": 0, "entries": 0, "skipped_cost": 0, "time_exits": 0}

    def on_trade(self, trade):
        if trade.net_pnl < 0 and self.p.cooldown:
            self.wait_until = self.bar_n + self.p.cooldown

    def on_tick(self, t):
        bar = self.bb.update(t)
        if bar is not None:
            self.on_signal_bar(bar, t)
        if self.positions and (t.ts % 86_400_000) >= self.p.flat_hour * 3_600_000:
            self.close_all("eod")

    def on_signal_bar(self, bar, t):
        p = self.p
        self.bar_n += 1
        atr = self.atr.update(bar)
        f, s = self.fast.update(bar.close), self.slw.update(bar.close)
        r, r_prev = self.rsi.update(bar.close), self.prev_rsi
        self.prev_rsi = r
        hist = self.hist
        full = len(hist) == hist.maxlen
        hh = max(h for h, _ in hist) if full else None
        ll = min(lo for _, lo in hist) if full else None
        hist.append((bar.high, bar.low))

        if self.positions:
            if self.entry_bar is not None and self.bar_n - self.entry_bar >= p.max_hold:
                self.close_all("time")
                self.stats["time_exits"] += 1
            return
        if self.orders or not self.atr.ready or not self.slw.ready or atr is None or atr <= 0:
            return
        if self.bar_n < self.wait_until:
            return
        tod = t.ts % 86_400_000
        if not any(a <= tod < b for a, b in self.windows) or tod >= p.flat_hour * 3_600_000:
            return

        side = 0
        if p.mode == "fade":
            z = (bar.close - f) / atr
            side = SELL if z >= p.k else BUY if z <= -p.k else 0
        elif p.mode == "breakout" and full:
            if bar.close > hh and f > s:
                side = BUY
            elif bar.close < ll and f < s:
                side = SELL
        elif p.mode == "pullback" and r is not None and r_prev is not None:
            if f > s and r_prev <= p.rsi_level < r:
                side = BUY
            elif f < s and r_prev >= 100 - p.rsi_level > r:
                side = SELL
        if not side:
            return
        self.stats["signals"] += 1
        tp_d = max(p.tp_atr * atr, p.min_tp_usd)
        sl_d = p.sl_atr * atr
        if t.spread > p.max_cost * tp_d:
            self.stats["skipped_cost"] += 1
            return
        if side == BUY:
            o = self.buy(p.lots, sl=t.ask - sl_d, tp=t.ask + tp_d)
        else:
            o = self.sell(p.lots, sl=t.bid + sl_d, tp=t.bid - tp_d)
        if o is not None:
            self.entry_bar = self.bar_n
            self.stats["entries"] += 1
