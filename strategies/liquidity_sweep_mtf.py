"""Multi-timeframe liquidity sweep: the full written spec.

  1H   bias       SuperTrend direction (+ optional MACD histogram agreement)
  15m  setup      wick through the high/low of the previous N bars, close back inside,
                  long rejection wick, RSI overbought/oversold or RSI divergence
  5m   confirm    MACD below/above signal and turning, SuperTrend on the same side
  1m   trigger    a 1m candle closing back inside the swept level, in the trade direction
  exit            fixed take profit (tp_points USD/oz) and an emergency stop beyond the sweep wick

All bars are built here from ticks (bid prices). Higher timeframes are updated first, so on a tick
that closes several bars at once the 1m trigger sees the latest 5m/15m/1H state. A setup is only
confirmed by 5m bars that start after the sweep bar closed, and only triggered by 1m bars after that,
so nothing uses information from the future.

Counters in ``self.stats`` (printed at the end) show how many candidates each stage let through.
"""
from collections import deque
from dataclasses import dataclass

from xautest.bars import BarBuilder
from xautest.indicators import MACD, RSI, SuperTrend
from xautest.models import BUY, SELL
from xautest.strategy import Strategy

MIN = 60_000


@dataclass
class Setup:
    side: int          # SELL after a high sweep, BUY after a low sweep
    level: float       # the swing high/low that was swept
    wick: float        # extreme of the sweep wick (the invalidation point)
    ready_ts: int      # sweep bar close time; later bars may confirm
    expires_ts: int
    confirmed_ts: int | None = None


class LiquiditySweepMTF(Strategy):
    timeframe = None  # bars for all four timeframes are built inside the strategy

    params = {
        # execution and risk
        "lots": 0.01,
        "risk_pct": 0.0,          # > 0: size so the stop loses this % of equity (overrides lots)
        "tp_points": 10.0,        # USD/oz (1000 points on a 2-decimal gold quote)
        "use_hard_sl": True,      # emergency stop beyond the sweep wick
        "sl_buffer": 0.50,        # USD/oz beyond the wick, on top of the spread
        "max_spread": 0.60,
        # 1H bias
        "h1_st_period": 10,
        "h1_st_mult": 3.0,
        "h1_macd_filter": True,   # also require 1H MACD histogram on the bias side
        # 15m sweep
        "m15_lookback": 20,
        "m15_min_wick": 0.40,     # rejection wick as a share of the sweep candle's range
        "rsi_period": 14,
        "rsi_overbought": 70.0,
        "rsi_oversold": 30.0,
        "use_divergence": True,   # accept RSI divergence instead of overbought/oversold
        "setup_valid_min": 60,    # minutes after the sweep bar closes to get confirmation + trigger
        # 5m confirmation
        "m5_st_period": 10,
        "m5_st_mult": 3.0,
    }

    STAGES = ("h1_bars", "sweeps", "rejections", "rsi_ok", "bias_aligned", "invalidated", "expired",
              "confirmed", "triggered", "skipped_spread", "skipped_size")

    def on_start(self):
        p = self.p
        self.b1h, self.b15, self.b5, self.b1 = (BarBuilder(tf) for tf in ("1h", "15min", "5min", "1min"))
        self.h1_st, self.h1_macd = SuperTrend(p.h1_st_period, p.h1_st_mult), MACD()
        self.m15_rsi = RSI(p.rsi_period)
        self.m15_hist: deque = deque(maxlen=p.m15_lookback)   # (high, low, rsi) of previous 15m bars
        self.m5_st, self.m5_macd = SuperTrend(p.m5_st_period, p.m5_st_mult), MACD()
        self.setup: Setup | None = None
        self.stats = {k: 0 for k in self.STAGES}

    # ------------------------------------------------------------------ routing
    def on_tick(self, tick):
        bar = self.b1h.update(tick)
        if bar:
            self.on_h1(bar)
        bar = self.b15.update(tick)
        if bar:
            self.on_m15(bar)
        bar = self.b5.update(tick)
        if bar:
            self.on_m5(bar)
        bar = self.b1.update(tick)
        if bar:
            self.on_m1(bar)

        s = self.setup
        if s is not None:
            if tick.ts >= s.expires_ts:
                self.setup = None
                self.stats["expired"] += 1
            elif (s.side == SELL and tick.bid > s.wick) or (s.side == BUY and tick.bid < s.wick):
                self.setup = None              # price went through the wick: the sweep failed
                self.stats["invalidated"] += 1

    @property
    def flat(self) -> bool:
        return not self.positions and not self.orders

    def bias(self) -> int:
        """+1 buys only, -1 sells only, 0 no trading."""
        if not self.h1_st.ready:
            return 0
        d = self.h1_st.direction
        if self.p.h1_macd_filter:
            if not self.h1_macd.ready:
                return 0
            if (d == 1 and self.h1_macd.hist <= 0) or (d == -1 and self.h1_macd.hist >= 0):
                return 0
        return d

    # ------------------------------------------------------------------ 1H
    def on_h1(self, bar):
        self.h1_st.update(bar)
        self.h1_macd.update(bar.close)
        self.stats["h1_bars"] += 1

    # ------------------------------------------------------------------ 15m
    def on_m15(self, bar):
        p = self.p
        rsi = self.m15_rsi.update(bar.close)
        hist = self.m15_hist
        if rsi is not None and len(hist) == hist.maxlen and self.flat:
            rng = bar.high - bar.low
            hi_i = max(range(len(hist)), key=lambda i: hist[i][0])
            lo_i = min(range(len(hist)), key=lambda i: hist[i][1])
            swing_hi, rsi_at_hi = hist[hi_i][0], hist[hi_i][2]
            swing_lo, rsi_at_lo = hist[lo_i][1], hist[lo_i][2]
            close_ts = bar.ts + 15 * MIN

            if rng > 0 and bar.high > swing_hi and bar.close < swing_hi:          # high swept, closed back in
                self.stats["sweeps"] += 1
                if (bar.high - max(bar.open, bar.close)) / rng >= p.m15_min_wick:
                    self.stats["rejections"] += 1
                    div = p.use_divergence and rsi_at_hi is not None and rsi < rsi_at_hi
                    if rsi > p.rsi_overbought or div:
                        self.stats["rsi_ok"] += 1
                        if self.bias() == -1:
                            self.stats["bias_aligned"] += 1
                            self.setup = Setup(SELL, swing_hi, bar.high, close_ts,
                                               close_ts + p.setup_valid_min * MIN)

            elif rng > 0 and bar.low < swing_lo and bar.close > swing_lo:         # low swept, closed back in
                self.stats["sweeps"] += 1
                if (min(bar.open, bar.close) - bar.low) / rng >= p.m15_min_wick:
                    self.stats["rejections"] += 1
                    div = p.use_divergence and rsi_at_lo is not None and rsi > rsi_at_lo
                    if rsi < p.rsi_oversold or div:
                        self.stats["rsi_ok"] += 1
                        if self.bias() == 1:
                            self.stats["bias_aligned"] += 1
                            self.setup = Setup(BUY, swing_lo, bar.low, close_ts,
                                               close_ts + p.setup_valid_min * MIN)
        hist.append((bar.high, bar.low, rsi))

    # ------------------------------------------------------------------ 5m
    def on_m5(self, bar):
        self.m5_st.update(bar)
        h = self.m5_macd.update(bar.close)
        s = self.setup
        if s is None or s.confirmed_ts is not None or bar.ts < s.ready_ts:
            return
        if not (self.m5_st.ready and self.m5_macd.ready):
            return
        prev = self.m5_macd.prev_hist
        if s.side == SELL:
            macd_ok = h < 0 and (prev >= 0 or h < prev)      # crossed below signal, or lower red bar
            st_ok = self.m5_st.direction == -1
        else:
            macd_ok = h > 0 and (prev <= 0 or h > prev)
            st_ok = self.m5_st.direction == 1
        if macd_ok and st_ok:
            s.confirmed_ts = bar.ts + 5 * MIN
            self.stats["confirmed"] += 1

    # ------------------------------------------------------------------ 1m
    def on_m1(self, bar):
        s = self.setup
        if s is None or s.confirmed_ts is None or bar.ts < s.confirmed_ts or not self.flat:
            return
        if s.side == SELL and not (bar.close < s.level and bar.close < bar.open):
            return
        if s.side == BUY and not (bar.close > s.level and bar.close > bar.open):
            return
        t, p = self.tick, self.p
        if t.spread > p.max_spread:
            self.stats["skipped_spread"] += 1
            return
        if s.side == SELL:
            tp = t.bid - p.tp_points
            sl = s.wick + t.spread + p.sl_buffer if p.use_hard_sl else None
            stop_dist = (sl - t.bid) if sl else None
        else:
            tp = t.ask + p.tp_points
            sl = s.wick - p.sl_buffer if p.use_hard_sl else None
            stop_dist = (t.ask - sl) if sl else None
        lots = p.lots
        if p.risk_pct > 0 and stop_dist:
            lots = int(self.equity * p.risk_pct / 100 / (stop_dist * self.broker.cfg.contract_size) / 0.01) * 0.01
            if lots < 0.01:
                self.stats["skipped_size"] += 1
                return
        (self.sell if s.side == SELL else self.buy)(round(lots, 2), sl=sl, tp=tp,
                                                    tag="short" if s.side == SELL else "long")
        self.stats["triggered"] += 1
        self.setup = None

    def on_stop(self):
        self.log.info("signal funnel: %s", ", ".join(f"{k}={v}" for k, v in self.stats.items()))
