import numpy as np
from xautest import Strategy
from xautest.indicators import ATR, EMA

class LiquiditySweepExecution(Strategy):
    timeframe = "1min"

    params = {
        "lots": 0.01,
        "tp_points": 10.0,          # 1000 points / 100 pips on XAU/USD ($10.00 move)
        "lookback_sweep": 60,       # 1m bars to determine recent liquidity highs/lows
        "rejection_wick_ratio": 0.40,# Minimum wick percentage to qualify rejection
        "atr_period": 14,
        "atr_buffer_mult": 1.5,     # Buffer above/below wick to prevent spread stop-outs
        "be_trigger_points": 2.5,   # Move to break-even after +250 points ($2.50)
        "rsi_period": 14,
        "fast_ema": 12,
        "slow_ema": 26,
        "signal_ema": 9,
    }

    def init(self):
        self.entry_price = None
        self.trade_direction = None
        self.be_moved = False

    def _calc_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        diffs = np.diff(closes[-(period + 1):])
        gains = np.maximum(diffs, 0.0)
        losses = np.maximum(-diffs, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calc_macd(self, closes):
        if len(closes) < self.p.slow_ema + self.p.signal_ema:
            return 0.0
        fast = np.mean(closes[-self.p.fast_ema:])
        slow = np.mean(closes[-self.p.slow_ema:])
        return fast - slow

    def _calc_atr(self, bars):
        if len(bars) < self.p.atr_period + 1:
            return 0.5
        tr_list = []
        for i in range(-self.p.atr_period, 0):
            current = bars[i]
            prev = bars[i - 1]
            tr = max(
                current.high - current.low,
                abs(current.high - prev.close),
                abs(current.low - prev.close)
            )
            tr_list.append(tr)
        return float(np.mean(tr_list))

    def on_bar(self, bar):
        bars = list(self.bars)
        if len(bars) < max(self.p.lookback_sweep + 1, self.p.slow_ema + self.p.signal_ema):
            return

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])

        # Current 1m bar dynamics
        curr_bar = bars[-1]
        candle_range = curr_bar.high - curr_bar.low
        upper_wick = curr_bar.high - max(curr_bar.open, curr_bar.close)
        lower_wick = min(curr_bar.open, curr_bar.close) - curr_bar.low

        # Indicators
        rsi = self._calc_rsi(closes, self.p.rsi_period)
        macd_val = self._calc_macd(closes)
        atr_val = self._calc_atr(bars)
        spread_buffer = atr_val * self.p.atr_buffer_mult

        # Key liquidity levels prior to the current candle
        sweep_high = np.max(highs[-(self.p.lookback_sweep + 1):-1])
        sweep_low = np.min(lows[-(self.p.lookback_sweep + 1):-1])

        # --- BEARISH REJECTION (Sell Entry) ---
        swept_high = curr_bar.high > sweep_high
        rejection_sell = (
            candle_range > 0 and
            (upper_wick / candle_range) >= self.p.rejection_wick_ratio and
            curr_bar.close < sweep_high
        )
        momentum_bearish = rsi >= 60.0 or macd_val < 0.0

        if not self.positions and swept_high and rejection_sell and momentum_bearish:
            entry = self.tick.bid
            sl = curr_bar.high + spread_buffer
            tp = entry - self.p.tp_points

            self.sell(self.p.lots, sl=sl, tp=tp)
            self.entry_price = entry
            self.trade_direction = "SELL"
            self.be_moved = False
            return

        # --- BULLISH REJECTION (Buy Entry) ---
        swept_low = curr_bar.low < sweep_low
        rejection_buy = (
            candle_range > 0 and
            (lower_wick / candle_range) >= self.p.rejection_wick_ratio and
            curr_bar.close > sweep_low
        )
        momentum_bullish = rsi <= 40.0 or macd_val > 0.0

        if not self.positions and swept_low and rejection_buy and momentum_bullish:
            entry = self.tick.ask
            sl = curr_bar.low - spread_buffer
            tp = entry + self.p.tp_points

            self.buy(self.p.lots, sl=sl, tp=tp)
            self.entry_price = entry
            self.trade_direction = "BUY"
            self.be_moved = False
            return

    def on_tick(self, tick):
        if not self.positions or self.be_moved or self.entry_price is None:
            return

        # Break-even trailing trigger
        pos = self.positions[0]
        if self.trade_direction == "SELL":
            profit_points = self.entry_price - tick.ask
            if profit_points >= self.p.be_trigger_points:
                # Lock SL at entry price
                self.modify(pos, sl=self.entry_price, tp=pos.tp)
                self.be_moved = True

        elif self.trade_direction == "BUY":
            profit_points = tick.bid - self.entry_price
            if profit_points >= self.p.be_trigger_points:
                # Lock SL at entry price
                self.modify(pos, sl=self.entry_price, tp=pos.tp)
                self.be_moved = True

    def on_trade(self, trade):
        # Reset state on order resolution
        if not self.positions:
            self.entry_price = None
            self.trade_direction = None
            self.be_moved = False
