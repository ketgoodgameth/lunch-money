"""Reaction-zone strategy: a mechanical version of the discretionary method.

  1H   direction   SuperTrend(10, 3) side, optionally agreeing with the 1H MACD histogram.
                   Fair value gaps (FVGs) on 1H and 15m are tracked as reaction zones.
  15m  rejection   In a 1H downtrend: a 15m candle rallies into resistance -- sweeps the highest high of the
                   last `swing_lookback` candles, or (zones="sweep+fvg") trades into an unfilled bearish FVG --
                   and closes back below it with a long upper wick, RSI overbought or showing divergence.
                   Uptrends are the mirror image (sweep of lows / bullish FVG support).
  5m   confirm     MACD below its signal and falling (or just crossed), 5m SuperTrend red.
  1m   execute     1m MACD line crosses below its signal while price is back below the zone: sell at market.
                   (exec_mode="m5": enter straight away on the 5m confirmation instead, if price is below the zone.)
  exit             take profit tp_usd per ounce; stop loss none (sl_mode="none": the $20/day account stop-out is
                   the only limit) or just beyond the rejection wick (sl_mode="wick").
  break-even       be_trigger > 0: once a trade is that many $/oz in profit, a stop goes to entry + be_offset.
  when             entries only inside `sessions` (UTC windows, e.g. "12:30-16:00" for the New York open);
                   anything open is closed at flat_hour UTC.

Sizing (size_mode="atr"): keep a fixed dollar risk (account_risk, the $10 account) but choose the lot size so
the stop-out distance is stop_atr_frac x the 14-day average daily range; with rr > 0 the target is rr times
that distance (so +rr x account_risk in dollars). Never below 0.01 lots. size_mode="fixed" uses `lots`.
With size_mode="fixed" and tp_atr > 0 the take profit is tp_atr x the 14-day average daily range instead of tp_usd,
so the target stays equally reachable in quiet and busy years.

A fair value gap: candle 1's high below candle 3's low (bullish, support) or candle 1's low above candle 3's
high (bearish, resistance). It stays a zone until price trades through it or it is max_fvg_age candles old.
"""
from collections import deque
from dataclasses import dataclass

from xautest.bars import BarBuilder
from xautest.indicators import ATR, MACD, RSI, SuperTrend
from xautest.models import BUY, SELL
from xautest.strategy import Strategy

MIN = 60_000


@dataclass
class Zone:
    side: int      # +1 bullish gap (support below price), -1 bearish gap (resistance above)
    lo: float
    hi: float
    age: int = 0


class FVGTracker:
    def __init__(self, max_age: int = 48):
        self.prev = deque(maxlen=2)
        self.zones: list[Zone] = []
        self.max_age = max_age

    def update(self, bar) -> None:
        keep = []
        for z in self.zones:
            z.age += 1
            filled = bar.low <= z.lo if z.side == 1 else bar.high >= z.hi
            if not filled and z.age <= self.max_age:
                keep.append(z)
        self.zones = keep
        if len(self.prev) == 2:
            first = self.prev[0]
            if first.high < bar.low:
                self.zones.append(Zone(1, first.high, bar.low))
            elif first.low > bar.high:
                self.zones.append(Zone(-1, bar.high, first.low))
        self.prev.append(bar)


@dataclass
class Setup:
    side: int
    level: float       # price the rejection closed back beyond
    wick: float        # extreme of the rejection candle
    ready_ts: int
    expires_ts: int
    confirmed_ts: int | None = None


def parse_sessions(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in filter(None, (x.strip() for x in spec.split(","))):
        a, b = part.split("-")
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        out.append(((ha * 60 + ma) * MIN, (hb * 60 + mb) * MIN))
    return out


class ReactionZone(Strategy):
    timeframe = None

    params = {
        "lots": 0.01,
        "tp_usd": 10.0,
        "sl_mode": "none",
        "sl_buffer": 0.5,
        "zones": "sweep+fvg",
        "swing_lookback": 20,
        "min_wick": 0.40,
        "rsi_overbought": 70.0,
        "rsi_oversold": 30.0,
        "use_divergence": True,
        "h1_macd_filter": True,
        "setup_valid_min": 60,
        "max_fvg_age": 48,
        "sessions": "12:30-16:00",
        "flat_hour": 20,
        "max_spread": 0.60,
        "exec_mode": "m1",
        "size_mode": "fixed",
        "account_risk": 10.0,
        "stop_atr_frac": 0.25,
        "rr": 0.0,
        "tp_atr": 0.0,
        "be_trigger": 0.0,
        "be_offset": 0.0,
    }

    def on_start(self):
        p = self.p
        self.b1h, self.b15, self.b5, self.b1 = (BarBuilder(tf) for tf in ("1h", "15min", "5min", "1min"))
        self.bday, self.datr = BarBuilder("1d"), ATR(14)
        self.h1_st, self.h1_macd = SuperTrend(10, 3.0), MACD()
        self.fvg_h1, self.fvg_15 = FVGTracker(p.max_fvg_age), FVGTracker(p.max_fvg_age)
        self.m15_rsi = RSI(14)
        self.hist15: deque = deque(maxlen=p.swing_lookback)     # (high, low, rsi)
        self.m5_st, self.m5_macd, self.m1_macd = SuperTrend(10, 3.0), MACD(), MACD()
        self.windows = parse_sessions(p.sessions)
        self.setup: Setup | None = None
        self._be_done: set[int] = set()
        self.stats = {"h1_bars": 0, "rejections": 0, "via_sweep": 0, "via_fvg": 0, "confirmed": 0,
                      "executed": 0, "expired": 0, "invalidated": 0, "outside_session": 0}

    # ---------------------------------------------------------------- routing
    def on_tick(self, t):
        bar = self.bday.update(t)
        if bar is not None:
            self.datr.update(bar)
        bar = self.b1h.update(t)
        if bar is not None:
            self.h1_st.update(bar); self.h1_macd.update(bar.close); self.fvg_h1.update(bar)
            self.stats["h1_bars"] += 1
        bar = self.b15.update(t)
        if bar is not None:
            self.on_m15(bar)
        bar = self.b5.update(t)
        if bar is not None:
            self.on_m5(bar, t)
        bar = self.b1.update(t)
        if bar is not None:
            self.on_m1(bar, t)
        s = self.setup
        if s is not None:
            if t.ts >= s.expires_ts:
                self.setup = None; self.stats["expired"] += 1
            elif (s.side == SELL and t.bid > s.wick) or (s.side == BUY and t.bid < s.wick):
                self.setup = None; self.stats["invalidated"] += 1
        if self.p.be_trigger > 0:
            self._break_even(t)
        if (self.positions or self.orders) and (t.ts % 86_400_000) >= self.p.flat_hour * 3_600_000:
            self.cancel_all()
            self.close_all("eod")

    def _break_even(self, t):
        """Once a trade is be_trigger $/oz in profit, put a stop at entry +/- be_offset (it never moves again)."""
        p = self.p
        for pos in self.positions:
            if pos.id in self._be_done:
                continue
            gain = (t.bid - pos.entry_price) if pos.side == BUY else (pos.entry_price - t.ask)
            if gain >= p.be_trigger:
                self._be_done.add(pos.id)
                self.modify(pos, sl=pos.entry_price + (p.be_offset if pos.side == BUY else -p.be_offset))

    def bias(self) -> int:
        if not self.h1_st.ready:
            return 0
        d = self.h1_st.direction
        if self.p.h1_macd_filter:
            if not self.h1_macd.ready or (d == 1 and self.h1_macd.hist <= 0) or (d == -1 and self.h1_macd.hist >= 0):
                return 0
        return d

    # ---------------------------------------------------------------- 15m: rejection at a reaction zone
    def on_m15(self, bar):
        p = self.p
        rsi = self.m15_rsi.update(bar.close)
        hist = self.hist15
        if rsi is not None and len(hist) == hist.maxlen and not self.positions and not self.orders:
            b = self.bias()
            rng = bar.high - bar.low
            if b and rng > 0:
                if b == -1:
                    i = max(range(len(hist)), key=lambda k: hist[k][0])
                    swing, rsi_at = hist[i][0], hist[i][2]
                    wick_ok = (bar.high - max(bar.open, bar.close)) / rng >= p.min_wick
                    level, how = None, ""
                    if bar.high > swing and bar.close < swing:
                        level, how = swing, "sweep"
                    elif p.zones == "sweep+fvg":
                        for z in self.fvg_h1.zones + self.fvg_15.zones:
                            if z.side == -1 and bar.high >= z.lo and bar.close < z.lo:
                                level, how = z.lo, "fvg"
                                break
                    div = p.use_divergence and how == "sweep" and rsi_at is not None and rsi < rsi_at
                    if level is not None and wick_ok and (rsi >= p.rsi_overbought or div):
                        self._new_setup(SELL, level, bar.high, bar.ts, how)
                else:
                    i = min(range(len(hist)), key=lambda k: hist[k][1])
                    swing, rsi_at = hist[i][1], hist[i][2]
                    wick_ok = (min(bar.open, bar.close) - bar.low) / rng >= p.min_wick
                    level, how = None, ""
                    if bar.low < swing and bar.close > swing:
                        level, how = swing, "sweep"
                    elif p.zones == "sweep+fvg":
                        for z in self.fvg_h1.zones + self.fvg_15.zones:
                            if z.side == 1 and bar.low <= z.hi and bar.close > z.hi:
                                level, how = z.hi, "fvg"
                                break
                    div = p.use_divergence and how == "sweep" and rsi_at is not None and rsi > rsi_at
                    if level is not None and wick_ok and (rsi <= p.rsi_oversold or div):
                        self._new_setup(BUY, level, bar.low, bar.ts, how)
        hist.append((bar.high, bar.low, rsi))
        self.fvg_15.update(bar)

    def _new_setup(self, side, level, wick, bar_ts, how):
        close_ts = bar_ts + 15 * MIN
        self.setup = Setup(side, level, wick, close_ts, close_ts + self.p.setup_valid_min * MIN)
        self.stats["rejections"] += 1
        self.stats["via_" + how] += 1

    # ---------------------------------------------------------------- 5m: momentum confirmation
    def on_m5(self, bar, t=None):
        self.m5_st.update(bar)
        h = self.m5_macd.update(bar.close)
        s = self.setup
        if s is None or s.confirmed_ts is not None or bar.ts < s.ready_ts:
            return
        if not (self.m5_st.ready and self.m5_macd.ready):
            return
        prev = self.m5_macd.prev_hist
        if s.side == SELL:
            ok = h < 0 and (prev >= 0 or h < prev) and self.m5_st.direction == -1
        else:
            ok = h > 0 and (prev <= 0 or h > prev) and self.m5_st.direction == 1
        if ok:
            s.confirmed_ts = bar.ts + 5 * MIN
            self.stats["confirmed"] += 1
            if self.p.exec_mode == "m5" and t is not None:
                inside = bar.close < s.level if s.side == SELL else bar.close > s.level
                if inside:
                    self._execute(s, t)

    # ---------------------------------------------------------------- 1m: execution on the crossover
    def on_m1(self, bar, t):
        h = self.m1_macd.update(bar.close)
        s = self.setup
        if self.p.exec_mode != "m1":
            return
        if s is None or s.confirmed_ts is None or bar.ts < s.confirmed_ts or self.positions or self.orders:
            return
        if not self.m1_macd.ready or self.m1_macd.prev_hist is None:
            return
        prev = self.m1_macd.prev_hist
        crossed = (h < 0 <= prev) if s.side == SELL else (h > 0 >= prev)
        back_inside = bar.close < s.level if s.side == SELL else bar.close > s.level
        if not (crossed and back_inside):
            return
        self._execute(s, t)

    def scaled_size(self) -> tuple[float, float]:
        """(lots, stop-out distance in USD/oz) for a fixed dollar risk at stop_atr_frac x daily ATR."""
        p = self.p
        target = max(p.stop_atr_frac * self.datr.value, 1e-9)
        lots = max(0.01, int(p.account_risk / (target * 100) / 0.01 + 1e-9) * 0.01)
        lots = round(lots, 2)
        return lots, p.account_risk / (lots * 100)

    def _execute(self, s, t):
        if self.positions or self.orders:
            return
        tod = t.ts % 86_400_000
        if not any(a <= tod < b for a, b in self.windows):
            self.stats["outside_session"] += 1
            return
        p = self.p
        if t.spread > p.max_spread:
            return
        lots, tp_dist = p.lots, p.tp_usd
        if p.size_mode == "atr":
            if not self.datr.ready:
                return
            lots, stop_dist = self.scaled_size()
            if p.rr > 0:
                tp_dist = p.rr * stop_dist
        elif p.tp_atr > 0:
            if not self.datr.ready:
                return
            tp_dist = p.tp_atr * self.datr.value
        if s.side == SELL:
            tp = t.bid - tp_dist
            sl = s.wick + t.spread + p.sl_buffer if p.sl_mode == "wick" else None
            order = self.sell(lots, sl=sl, tp=tp)
        else:
            tp = t.ask + tp_dist
            sl = s.wick - p.sl_buffer if p.sl_mode == "wick" else None
            order = self.buy(lots, sl=sl, tp=tp)
        if order is not None:
            self.stats["executed"] += 1
            self.setup = None
