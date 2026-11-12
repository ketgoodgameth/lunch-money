"""Direction-filtered scalper: the reaction-zone strategy picks the side, quick scalps trade only that side.

Built from the trade-history finding that your fast trades (closed in under a minute) win 78% and that trades on
the strategy's side do better than trades against it. The strategy's own trades (entry time, exit time, side) come
in as `signals`, either a CSV (entry_time, exit_time, side) or a list of (entry_ms, exit_ms, side).

  active window  "trade": from a strategy entry until that strategy trade closes
                 "day":   from a strategy entry until flat_hour UTC (side = the latest strategy trade that day)
  entries        "market":   enter whenever flat (after `cooldown_s` since the last scalp closed)
                 "pullback": a 1m candle closes against the side (a dip in a buy window), enter on its close
                 "m1_macd":  the 1m MACD histogram crosses to the side, enter on that candle's close
  exits          take profit tp_usd and stop sl_usd ($/oz from the fill), max_hold_min minutes, flat_hour
  flip           True trades the opposite side: the control that shows whether the direction matters
One position at a time; the daily rules (fixed 0.01 lots, $20/day account) sit outside the strategy.
"""
import pandas as pd

from xautest.bars import BarBuilder
from xautest.indicators import MACD
from xautest.models import BUY, SELL
from xautest.strategy import Strategy

DAY = 86_400_000


def load_signals(src) -> list[tuple[int, int, int]]:
    if isinstance(src, (list, tuple)):
        return sorted((int(a), int(b), int(s)) for a, b, s in src)
    df = pd.read_csv(src)
    ms = lambda c: pd.to_datetime(df[c], utc=True, format="ISO8601").dt.as_unit("ms").astype("int64")
    side = df.side.map(lambda x: BUY if str(x).upper() in ("BUY", "1") else SELL)
    return sorted(zip(ms("entry_time").tolist(), ms("exit_time").tolist(), side.tolist()))


class BiasScalp(Strategy):
    timeframe = None

    params = {
        "signals": [],
        "window": "trade",
        "entry": "pullback",
        "tp_usd": 2.0,
        "sl_usd": 4.0,
        "max_hold_min": 5.0,
        "cooldown_s": 30.0,
        "flip": False,
        "lots": 0.01,
        "flat_hour": 20,
        "max_cost": 0.5,       # skip when the spread is more than this share of the take profit
    }

    def on_start(self):
        self.sig = load_signals(self.p.signals)
        self.k = 0                       # signals[:k] have started
        self.bb, self.macd = BarBuilder("1min"), MACD()
        self.wait_until = 0
        self.stats = {"entries": 0, "time_exits": 0, "skipped_cost": 0}

    def side_now(self, ts: int) -> int:
        while self.k < len(self.sig) and self.sig[self.k][0] <= ts:
            self.k += 1
        if not self.k:
            return 0
        entry, exit_, side = self.sig[self.k - 1]
        if entry // DAY != ts // DAY or (ts % DAY) >= self.p.flat_hour * 3_600_000:
            return 0
        if self.p.window == "trade" and ts >= exit_:
            return 0
        return -side if self.p.flip else side

    def on_trade(self, trade):
        self.wait_until = trade.exit_ts + int(self.p.cooldown_s * 1000)

    def on_tick(self, t):
        p = self.p
        bar = self.bb.update(t)
        if bar is not None:
            self.macd.update(bar.close)
        if self.positions:
            pos = self.positions[0]
            if (t.ts % DAY) >= p.flat_hour * 3_600_000:
                self.close_all("eod")
            elif t.ts - pos.entry_ts >= p.max_hold_min * 60_000:
                self.close_all("time")
                self.stats["time_exits"] += 1
            return
        if self.orders or t.ts < self.wait_until:
            return
        side = self.side_now(t.ts)
        if not side:
            return
        if p.entry == "pullback":
            if bar is None or not (bar.close < bar.open if side == BUY else bar.close > bar.open):
                return
        elif p.entry == "m1_macd":
            h, ph = self.macd.hist, self.macd.prev_hist
            if bar is None or not self.macd.ready or ph is None or not (side * h > 0 >= side * ph):
                return
        if t.spread > p.max_cost * p.tp_usd:
            self.stats["skipped_cost"] += 1
            return
        if side == BUY:
            o = self.buy(p.lots, sl=t.ask - p.sl_usd, tp=t.ask + p.tp_usd, tag="scalp")
        else:
            o = self.sell(p.lots, sl=t.bid + p.sl_usd, tp=t.bid - p.tp_usd, tag="scalp")
        if o is not None:
            self.stats["entries"] += 1
