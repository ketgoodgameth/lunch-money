"""Your v2 liquidity-sweep ENTRIES, unchanged, with a choice of EXIT method.

R = distance from the actual fill price to the initial stop loss (so 1R = the $10 risked under
the daily rules).

  exit="fixed"  v2 as written: take profit $10/oz, stop to break-even after +$2.50
  exit="rr"     take profit at rr x R, no break-even  (rr=2: 1:2 scalper, rr=10: 1:10 sniper)
  exit="trail"  no take profit; once the trade is +trail_start_r x R, the stop follows the best price
                at trail_dist_r x R behind it and never moves back (profit lock)

Timeframe: tf="5min" (or "15min", ...) runs the same v2 sweep logic on slower candles; lookback_sweep
and the ATR buffer then count in those candles.

Cost reduction:
  min_stop=X            widen any stop closer than X USD/oz to exactly X (risk stays $10 via sizing,
                        so the spread becomes a smaller share of each trade's risk)
  entry="limit"         instead of a market order, place a limit order one spread better (buy at the
                        bid, sell at the ask); cancelled after limit_expiry_s seconds if not filled

Entry filters (they only block v2 signals, never add any):
  hours="7-20"          only enter between these UTC hours (London + New York)
  trend="h1_supertrend" buys only while the 1-hour SuperTrend(10, 3) is up, sells only while it is down
  skip_reopen_min=N     no entries for N minutes after the daily/weekly reopen (spreads are widest)
"""
from liquidity_sweep_v2 import LiquiditySweepExecution

from xautest.bars import BarBuilder
from xautest.indicators import SuperTrend
from xautest.models import BUY, OrderType


class LiquiditySweepExits(LiquiditySweepExecution):
    params = {
        **LiquiditySweepExecution.params,
        "exit": "fixed",
        "rr": 2.0,
        "trail_start_r": 1.0,
        "trail_dist_r": 1.0,
        "hours": "",
        "trend": "none",
        "skip_reopen_min": 0,
        "tf": "1min",
        "min_stop": 0.0,
        "entry": "market",
        "limit_expiry_s": 60,
    }

    def __init__(self, **params):
        super().__init__(**params)
        self.timeframe = self.p.tf   # the engine builds bars at this timeframe

    def on_start(self):
        self.init()          # v2 sets its state in init(); the engine calls on_start()
        self.trail = {}      # position id -> [R, best price so far]
        p = self.p
        self.hours = tuple(int(x) for x in p.hours.split("-")) if p.hours else None
        if p.trend not in ("none", "h1_supertrend"):
            raise ValueError(f"unknown trend filter {p.trend!r}")
        self.h1, self.h1_st = BarBuilder("1h"), SuperTrend(10, 3.0)
        self.last_ts = self.reopen_ts = self.tracked_ts = None
        self.blocked = {"hours": 0, "trend": 0, "reopen": 0}

    def _track(self, tick):
        """Update filter state once per tick, before v2 looks for entries on that tick."""
        if tick.ts == self.tracked_ts:
            return
        self.tracked_ts = tick.ts
        if self.p.trend != "none":
            bar = self.h1.update(tick)
            if bar is not None:
                self.h1_st.update(bar)
        if self.last_ts is not None and tick.ts - self.last_ts > 30 * 60_000:
            self.reopen_ts = tick.ts           # first quote after the daily break or the weekend
        self.last_ts = tick.ts

    def on_bar(self, bar):
        self._track(self.tick)
        super().on_bar(bar)

    def _allowed(self, side) -> bool:
        p = self.p
        if self.hours:
            h = (self.now // 3_600_000) % 24
            a, b = self.hours
            if not (a <= h < b if a < b else (h >= a or h < b)):
                self.blocked["hours"] += 1
                return False
        if p.trend == "h1_supertrend" and (not self.h1_st.ready or self.h1_st.direction != side):
            self.blocked["trend"] += 1
            return False
        if p.skip_reopen_min and self.reopen_ts is not None and self.now - self.reopen_ts < p.skip_reopen_min * 60_000:
            self.blocked["reopen"] += 1
            return False
        return True

    def _order(self, side, lots, type, price, sl, tp, expiry_ms, tag):
        # Every v2 entry goes through here, so the exit can be swapped without touching the entry code.
        if not self._allowed(side):
            return None
        p, t = self.p, self.tick
        entry = t.ask if side == BUY else t.bid
        if p.entry == "limit" and type is OrderType.MARKET:
            type, price = OrderType.LIMIT, (t.bid if side == BUY else t.ask)   # one spread better
            expiry_ms = self.now + int(p.limit_expiry_s * 1000)
            entry = price
        if p.min_stop and sl is not None and abs(entry - sl) < p.min_stop:
            sl = entry - side * p.min_stop
        if p.exit == "trail":
            tp = None
        elif p.exit == "rr" and sl is not None:
            tp = entry + side * p.rr * abs(entry - sl)
        return super()._order(side, lots, type, price, sl, tp, expiry_ms, tag)

    def on_fill(self, order, position):
        if self.p.exit == "fixed" or position.sl is None:
            return
        r = abs(position.entry_price - position.sl)
        if self.p.exit == "rr":   # re-anchor the target on the real fill price (latency, slippage)
            self.modify(position, tp=position.entry_price + position.side * self.p.rr * r)
        else:
            self.trail[position.id] = [r, position.entry_price]

    def on_tick(self, tick):
        self._track(tick)
        if self.p.exit == "fixed":
            return super().on_tick(tick)          # v2's break-even rule
        if self.p.exit != "trail" or not self.trail:
            return
        for pos in self.positions:
            st = self.trail.get(pos.id)
            if st is None:
                continue
            r, best = st
            if pos.side == BUY:
                if tick.bid > best:
                    st[1] = best = tick.bid
                if r > 0 and best - pos.entry_price >= self.p.trail_start_r * r:
                    new_sl = best - self.p.trail_dist_r * r
                    if new_sl > pos.sl:
                        self.modify(pos, sl=new_sl)
            else:
                if tick.ask < best:
                    st[1] = best = tick.ask
                if r > 0 and pos.entry_price - best >= self.p.trail_start_r * r:
                    new_sl = best + self.p.trail_dist_r * r
                    if new_sl < pos.sl:
                        self.modify(pos, sl=new_sl)

    def on_trade(self, trade):
        super().on_trade(trade)
        self.trail.pop(trade.position_id, None)
