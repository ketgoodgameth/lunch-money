"""Analyse a real trade log against Dukascopy ticks.

    .venv/bin/python trade_log.py PATH_TO_LOG [--tz 0]

Accepts this project's trades CSV and most broker exports (CSV / XLSX / HTML) with columns for open time,
close time, side (buy/sell), volume (lots), open price, close price and profit. Times are converted from
--tz hours (Exness server time is UTC, so 0) to UTC.

Writes results/trade_log/: trades_enriched.csv (every trade + excursions + market state at entry) and
prints: statistics, results by hour/session/weekday, how far trades went against you, and which of the
strategy conditions were true when you entered.
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments")); sys.path.insert(0, str(ROOT / "strategies"))
from common import cache_for, year_file                     # noqa: E402
from xautest.feeds.arrays import ArrayFeed, load_cache      # noqa: E402
from xautest.indicators import MACD, RSI, SuperTrend        # noqa: E402
from xautest.bars import BarBuilder                         # noqa: E402

CONTRACT = 100.0
ALIASES = {
    "open_time": ["entry_time", "opening_time_utc", "open time", "opening time", "time", "open_time", "opened", "open date"],
    "close_time": ["exit_time", "closing_time_utc", "close time", "closing time", "close_time", "closed", "close date"],
    "side": ["side", "type", "direction", "order type", "deal type"],
    "lots": ["lots", "volume", "size", "lot", "volume (lots)"],
    "open_price": ["entry_price", "opening_price", "open price", "opening price", "price", "open_price"],
    "close_price": ["exit_price", "closing_price", "close price", "closing price", "close_price"],
    "profit": ["net_pnl", "profit", "p/l", "pnl", "profit (usd)", "net profit"],
    "symbol": ["symbol", "instrument"],
    "sl": ["initial_stop", "s/l", "sl", "stop loss"],
    "tp": ["t/p", "tp", "take profit"],
}


def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", str(c).strip().lower())


def load_log(path: Path, tz_hours: float = 0.0) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path)
    elif path.suffix.lower() in (".html", ".htm"):
        raw = max(pd.read_html(path), key=len)
    else:
        raw = pd.read_csv(path, sep=None, engine="python")
    cols = {_norm(c): c for c in raw.columns}
    out = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in cols and cols[n] not in out.values():
                out[key] = cols[n]
                break
    missing = [k for k in ("open_time", "close_time", "side", "lots", "open_price", "close_price") if k not in out]
    if missing:
        raise SystemExit(f"could not find columns for {missing}; columns are: {list(raw.columns)}")
    df = pd.DataFrame({k: raw[v] for k, v in out.items()})
    if "symbol" in df:
        df = df[df.symbol.astype(str).str.upper().str.contains("XAU")]
    side = df.side.astype(str).str.lower()
    df = df[side.str.contains("buy|sell")].copy()
    df["side"] = np.where(df.side.astype(str).str.lower().str.contains("buy"), 1, -1)
    shift = pd.Timedelta(hours=tz_hours)
    for c in ("open_time", "close_time"):
        df[c] = pd.to_datetime(df[c], utc=True, format="mixed") - shift
    for c in ("lots", "open_price", "close_price", "profit", "sl", "tp"):
        if c in df:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(r"[^\d.\-]", "", regex=True), errors="coerce")
    if "profit" not in df:
        df["profit"] = (df.close_price - df.open_price) * df.side * df.lots * CONTRACT
    return df.sort_values("open_time").reset_index(drop=True)


def stats(df: pd.DataFrame) -> None:
    p = df.profit
    wins, losses = p[p > 0], p[p <= 0]
    streak = best = 0
    for x in p:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    eq = p.cumsum()
    print(f"trades {len(df)}  from {df.open_time.min():%Y-%m-%d} to {df.close_time.max():%Y-%m-%d}")
    print(f"net ${p.sum():,.2f}   win rate {len(wins) / len(df):.0%}   avg win ${wins.mean():.2f}   avg loss ${losses.mean():.2f}"
          f"   profit per trade ${p.mean():.2f}")
    print(f"largest win ${p.max():.2f}   largest loss ${p.min():.2f}   longest losing streak {best}   "
          f"max drawdown ${float((eq - eq.cummax()).min()):.2f}")
    print(f"lots: median {df.lots.median():g}, max {df.lots.max():g};   hold time: median "
          f"{(df.close_time - df.open_time).median()}, longest {(df.close_time - df.open_time).max()}")
    by = pd.DataFrame({"profit": p, "hour": df.open_time.dt.hour, "weekday": df.open_time.dt.day_name(),
                       "side": np.where(df.side > 0, "BUY", "SELL")})
    print("\nby entry hour (UTC):", by.groupby("hour").profit.agg(["count", "sum"]).round(1).T.to_string())
    print("\nby weekday:", by.groupby("weekday").profit.sum().round(1).to_dict())
    print("by side:", by.groupby("side").profit.agg(["count", "sum"]).round(1).to_dict("index"))


def excursions(df: pd.DataFrame) -> pd.DataFrame:
    """Worst (MAE) and best (MFE) price move during each trade, in $/oz and in $ at the trade's size."""
    mae, mfe = [], []
    cache = {}
    for _, r in df.iterrows():
        y = r.open_time.year
        if y not in cache:
            try:
                cache[y] = load_cache(cache_for(year_file(y)))
            except (FileNotFoundError, KeyError):
                cache[y] = None
        a = cache[y]
        if a is None:
            mae.append(np.nan); mfe.append(np.nan); continue
        ts = a["ts"]
        lo = np.searchsorted(ts, int(r.open_time.timestamp() * 1000))
        hi = np.searchsorted(ts, int(r.close_time.timestamp() * 1000), "right")
        if hi <= lo:
            mae.append(0.0); mfe.append(0.0); continue
        mid = (np.asarray(a["bid"][lo:hi]) + np.asarray(a["ask"][lo:hi])) / 2
        move = (mid - r.open_price) * r.side
        mae.append(float(move.min())); mfe.append(float(move.max()))
    df = df.copy()
    df["mae_per_oz"], df["mfe_per_oz"] = mae, mfe
    df["mae_usd"] = df.mae_per_oz * df.lots * CONTRACT
    return df


class StateRecorder:
    """Replays ticks and snapshots the indicator state at each requested time."""

    def __init__(self, times_ms):
        self.queue = sorted(set(times_ms))
        self.snap = {}
        self.b1h, self.b15, self.b5, self.b1 = (BarBuilder(tf) for tf in ("1h", "15min", "5min", "1min"))
        self.h1_st, self.h1_macd, self.r15 = SuperTrend(10, 3.0), MACD(), RSI(14)
        self.m5_st, self.m5_macd, self.m1_macd = SuperTrend(10, 3.0), MACD(), MACD()
        self.h15, self.last15 = [], None
        from reaction_zone import FVGTracker
        self.fvg_h1, self.fvg_15 = FVGTracker(), FVGTracker()
        self.m1_cross_ts = {1: None, -1: None}

    def feed(self, t):
        if (b := self.b1h.update(t)) is not None:
            self.h1_st.update(b); self.h1_macd.update(b.close); self.fvg_h1.update(b)
        if (b := self.b15.update(t)) is not None:
            rsi = self.r15.update(b.close)
            self.last15 = (b, rsi, list(self.h15[-20:]))
            self.h15.append((b.high, b.low)); self.h15 = self.h15[-40:]
            self.fvg_15.update(b)
        if (b := self.b5.update(t)) is not None:
            self.m5_st.update(b); self.m5_macd.update(b.close)
        if (b := self.b1.update(t)) is not None:
            h = self.m1_macd.update(b.close)
            prev = self.m1_macd.prev_hist
            if prev is not None:
                if h > 0 >= prev:
                    self.m1_cross_ts[1] = t.ts
                elif h < 0 <= prev:
                    self.m1_cross_ts[-1] = t.ts
        while self.queue and self.queue[0] <= t.ts:
            self.snap[self.queue.pop(0)] = self.state(t)

    def state(self, t):
        st = {"h1_supertrend": self.h1_st.direction if self.h1_st.ready else 0,
              "h1_macd_hist": self.h1_macd.hist if self.h1_macd.ready else np.nan,
              "m5_supertrend": self.m5_st.direction if self.m5_st.ready else 0,
              "m5_macd_hist": self.m5_macd.hist if self.m5_macd.ready else np.nan,
              "m1_macd_hist": self.m1_macd.hist if self.m1_macd.ready else np.nan,
              "spread": t.ask - t.bid, "price": t.bid}
        for side in (1, -1):
            c = self.m1_cross_ts[side]
            st[f"min_since_m1_cross_{'up' if side == 1 else 'down'}"] = (t.ts - c) / 60_000 if c else np.nan
        if self.last15:
            b, rsi, hist = self.last15
            rng = b.high - b.low
            st["m15_rsi"] = rsi
            st["m15_swept_high"] = bool(hist) and b.high > max(h for h, _ in hist)
            st["m15_swept_low"] = bool(hist) and b.low < min(lo for _, lo in hist)
            st["m15_upper_wick"] = (b.high - max(b.open, b.close)) / rng if rng else 0.0
            st["m15_lower_wick"] = (min(b.open, b.close) - b.low) / rng if rng else 0.0
        px = t.bid
        for name, tr in (("h1", self.fvg_h1), ("m15", self.fvg_15)):
            above = [z.lo - px for z in tr.zones if z.side == -1 and z.lo >= px - 1e-9]
            below = [px - z.hi for z in tr.zones if z.side == 1 and z.hi <= px + 1e-9]
            st[f"{name}_fvg_above_dist"] = min(above) if above else np.nan
            st[f"{name}_fvg_below_dist"] = min(below) if below else np.nan
        return st


def to_ms(t: pd.Series) -> pd.Series:
    """UTC datetimes -> epoch ms, whatever pandas' internal resolution."""
    return (t - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)


def entry_states(df: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    for y, g in df.groupby(df.open_time.dt.year):
        try:
            a = load_cache(cache_for(year_file(y)))
        except (FileNotFoundError, KeyError):
            continue
        times = to_ms(g.open_time).tolist()
        first = int(min(times)) - 5 * 86_400_000          # 5 days of warm-up for the indicators
        rec = StateRecorder(times)
        for t in ArrayFeed(a, first, int(max(times)) + 1):
            rec.feed(t)
        rows.update(rec.snap)
    st = pd.DataFrame([rows.get(int(x), {}) for x in to_ms(df.open_time)])
    return pd.concat([df.reset_index(drop=True), st], axis=1)


def conditions(df: pd.DataFrame) -> None:
    """How often each strategy condition was true at the user's entries (in the trade's direction)."""
    s = df.side
    def share(mask):
        return f"{np.nanmean(mask.astype(float)):.0%}"
    rsi_ext = np.where(s < 0, df.m15_rsi >= 70, df.m15_rsi <= 30)
    print("\nat your entries (trade direction):")
    print(f"  1H SuperTrend agreed            {share(df.h1_supertrend == s)}")
    print(f"  1H MACD histogram agreed        {share(np.sign(df.h1_macd_hist) == s)}")
    print(f"  last 15m candle swept the level {share(np.where(s < 0, df.m15_swept_high, df.m15_swept_low))}")
    print(f"  15m rejection wick >= 40%       {share(np.where(s < 0, df.m15_upper_wick, df.m15_lower_wick) >= 0.4)}")
    print(f"  15m RSI extreme (70/30)         {share(rsi_ext)}   median RSI at sells {df.m15_rsi[s < 0].median():.0f}, "
          f"at buys {df.m15_rsi[s > 0].median():.0f}")
    print(f"  5m SuperTrend agreed            {share(df.m5_supertrend == s)}")
    print(f"  5m MACD histogram agreed        {share(np.sign(df.m5_macd_hist) == s)}")
    cross = np.where(s > 0, df.min_since_m1_cross_up, df.min_since_m1_cross_down)
    print(f"  1m MACD crossed within 5 min    {share(pd.Series(cross) <= 5)}   median minutes since cross {np.nanmedian(cross):.0f}")
    near = np.where(s < 0, df[["h1_fvg_above_dist", "m15_fvg_above_dist"]].min(axis=1),
                    df[["h1_fvg_below_dist", "m15_fvg_below_dist"]].min(axis=1))
    print(f"  unfilled FVG within $3          {share(pd.Series(near) <= 3)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--tz", type=float, default=0.0, help="hours the log's clock is ahead of UTC (Exness: 0)")
    a = ap.parse_args()
    df = load_log(Path(a.log), a.tz)
    stats(df)
    df = excursions(df)
    print(f"\nexcursions: worst move against a trade ${df.mae_per_oz.min():.2f}/oz "
          f"(${df.mae_usd.min():.2f} at its size); trades that went more than $20 against you: "
          f"{int((df.mae_usd < -20).sum())}; more than $50: {int((df.mae_usd < -50).sum())}")
    df = entry_states(df)
    conditions(df)
    out = ROOT / "results" / "trade_log"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "trades_enriched.csv", index=False)
    print(f"\nwrote {out / 'trades_enriched.csv'}")
