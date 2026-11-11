import numpy as np
from xautest import Strategy
from xautest.indicators import ATR, EMA

class LiquiditySweepExecution(Strategy):
    timeframe = "1min"

    params = {
        "lots": 0.01,
        "tp_points": 10.0,       # 1000 points / 100 pips on Gold ($10 move)
        "use_hard_sl": False,    # Set True if you want a safety cap at the wick
        "lookback_sweep": 60,    # Bars on 1m to identify the local liquidity high
        "rsi_period": 14,
        "st_period": 10,
        "st_multiplier": 3.0,
    }

    def _calc_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        diffs = np.diff(closes[-(period + 1):])
        gains = np.maximum(diffs, 0)
        losses = np.maximum(-diffs, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calc_macd(self, closes, fast=12, slow=26, signal=9):
        if len(closes) < slow + signal:
            return 0.0, 0.0
        fast_ema = np.mean(closes[-fast:])
        slow_ema = np.mean(closes[-slow:])
        macd_line = fast_ema - slow_ema
        return macd_line

    def on_bar(self, bar):
        bars = list(self.bars)
        if len(bars) < self.p.lookback_sweep + 1:
            return

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])

        # Current bar details
        current = bars[-1]
        candle_range = current.high - current.low
        upper_wick = current.high - max(current.open, current.close)
        lower_wick = min(current.open, current.close) - current.low

        # Indicators
        rsi = self._calc_rsi(closes, self.p.rsi_period)
        macd_val = self._calc_macd(closes)

        # Recent liquidity levels (excluding the current candle)
        recent_high = np.max(highs[-(self.p.lookback_sweep + 1):-1])
        recent_low = np.min(lows[-(self.p.lookback_sweep + 1):-1])

        # --- BEARISH SETUP (As shown in your trade) ---
        # 1. Sweep: Price pierced the recent high
        liquidity_swept_high = current.high > recent_high

        # 2. Rejection: Price closed back below the swept high and left a prominent upper wick (>= 40% of range)
        rejection_down = (
            candle_range > 0 and
            (upper_wick / candle_range) >= 0.40 and
            current.close < recent_high
        )

        # 3. Indicator check: RSI coming down or elevated, MACD turning negative
        momentum_bearish = rsi >= 60.0 or macd_val < 0

        if not self.positions and liquidity_swept_high and rejection_down and momentum_bearish:
            tp_price = self.tick.bid - self.p.tp_points
            sl_price = (current.high + 0.5) if self.p.use_hard_sl else None

            self.sell(self.p.lots, sl=sl_price, tp=tp_price)

        # --- BULLISH SETUP (Inverse) ---
        liquidity_swept_low = current.low < recent_low
        rejection_up = (
            candle_range > 0 and
            (lower_wick / candle_range) >= 0.40 and
            current.close > recent_low
        )
        momentum_bullish = rsi <= 40.0 or macd_val > 0

        if not self.positions and liquidity_swept_low and rejection_up and momentum_bullish:
            tp_price = self.tick.ask + self.p.tp_points
            sl_price = (current.low - 0.5) if self.p.use_hard_sl else None

            self.buy(self.p.lots, sl=sl_price, tp=tp_price)

    def on_tick(self, tick):
        pass

    def on_trade(self, trade):
        pass
