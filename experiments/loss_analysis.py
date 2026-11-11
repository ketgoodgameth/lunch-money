"""Where do the losses come from? TP $10 intraday, 2019-2026, every trade tagged with what the market looked like at entry.

    .venv/bin/python loss_analysis.py run       # full-year runs with entry features -> results/loss_analysis/trades.csv
    .venv/bin/python loss_analysis.py analyse   # loss rate by feature bucket, discovery years vs check years

Same settings as high_winrate.py "TP $10, intraday" ($0.20 spread, $20/day account, 0.01 lots, max 2, stop after win).
Discovery years 2019-2024; 2025-2026 are only looked at after a filter is chosen.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
DISCOVERY = (2019, 2020, 2021, 2022, 2023, 2024)
OUT = ROOT / "results" / "loss_analysis"
RULES = {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 2, "stop_after_wins": 1}
BROKER_OVER = {"fixed_spread": 0.20, "slippage": 0.0, "daily_account": 20.0}


def base_params(**over) -> dict:
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    return {k: v for k, v in base.items() if k != "__rules__"} | {"tp_usd": 10.0, "flat_hour": 20} | over


def tagged_class():
    sys.path.insert(0, str(ROOT))
    from xautest.cli import load_strategy
    from xautest.indicators import ATR
    from xautest.models import SELL
    base = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))

    class Tagged(base):
        def on_start(self):
            super().on_start()
            self.h1_atr, self.m5_atr = ATR(14), ATR(14)
            self._day, self._dopen, self._dhi, self._dlo, self._n = None, None, None, None, 0
            self._bar15, self._info, self._pending = None, {}, ""

        def on_tick(self, t):
            day = t.ts // 86_400_000
            if day != self._day:
                self._day, self._dopen, self._dhi, self._dlo, self._n = day, t.bid, t.bid, t.bid, 0
            self._dhi, self._dlo = max(self._dhi, t.bid), min(self._dlo, t.bid)
            super().on_tick(t)

        def on_m15(self, bar):
            self._bar15 = bar
            super().on_m15(bar)

        def _new_setup(self, side, level, wick, bar_ts, how):
            super()._new_setup(side, level, wick, bar_ts, how)
            b = self._bar15
            rng = b.high - b.low
            body = abs(b.close - b.open)
            self._info = {"how": how, "rej_range": rng, "rej_body": body / rng if rng else 0,
                          "rej_wick": ((b.high - max(b.open, b.close)) if side == SELL else (min(b.open, b.close) - b.low)) / rng,
                          "rsi15": self.m15_rsi.value, "rej_ts": bar_ts}

        def on_m5(self, bar, t=None):
            self.m5_atr.update(bar)
            super().on_m5(bar, t)

        def buy(self, lots, sl=None, tp=None, tag=""):
            return super().buy(lots, sl=sl, tp=tp, tag=self._pending or tag)

        def sell(self, lots, sl=None, tp=None, tag=""):
            return super().sell(lots, sl=sl, tp=tp, tag=self._pending or tag)

        def _execute(self, s, t):
            d = 1 if s.side != SELL else -1
            price = t.ask if d == 1 else t.bid
            datr = self.datr.value if self.datr.ready else np.nan
            f = dict(self._info)
            f.update({
                "n_today": self._n + 1,
                "hour": (t.ts % 86_400_000) / 3_600_000,
                "wday": int((t.ts // 86_400_000 + 3) % 7),            # 0 = Monday
                "to_wick": abs(price - s.wick), "to_level": abs(price - s.level),
                "datr": datr,
                "m5_atr": self.m5_atr.value if self.m5_atr.ready else np.nan,
                "h1_st_dist": d * (price - self.h1_st.value) if self.h1_st.ready else np.nan,
                "h1_hist": d * self.h1_macd.hist if self.h1_macd.ready else np.nan,
                "day_move": d * (price - self._dopen),                # + = trading with today's move
                "day_range": self._dhi - self._dlo,
                "day_pos": ((price - self._dlo) / (self._dhi - self._dlo)) if self._dhi > self._dlo else 0.5,
                "wait_min": (t.ts - f.get("rej_ts", t.ts)) / 60_000,
                "price": price,
            })
            f.pop("rej_ts", None)
            self._pending = json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()})
            n0 = self.stats["executed"]
            super()._execute(s, t)
            self._pending = ""
            if self.stats["executed"] > n0:
                self._n += 1

    return Tagged


def run_one(year):
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = tagged_class()(**base_params())
    s.risk = DailyRiskRules(**{**BASE_RULES, **RULES})
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, **BROKER_OVER})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    df.insert(0, "year", year)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(8) as pool:
        df = pd.concat(pool.map(run_one, YEARS))
    df.to_csv(OUT / "trades.csv", index=False)
    print(len(df), "trades;", df.tag.isna().sum(), "untagged")


def load() -> pd.DataFrame:
    df = pd.read_csv(OUT / "trades.csv")
    feats = pd.DataFrame([json.loads(t) if isinstance(t, str) and t.startswith("{") else {} for t in df.tag])
    df = pd.concat([df.drop(columns="tag"), feats], axis=1)
    df["win"] = df.net_pnl > 0
    df["stopout"] = df.net_pnl <= -19
    df["day"] = df.entry_time.str[:10]
    df["first_lost"] = df.groupby("day").win.transform(lambda s: (~s).iloc[0] if len(s) > 1 else False) & (df.n_today == 2)
    df["to_wick_atr"] = df.to_wick / df.datr
    df["datr_rel"] = df.datr / df.price * 100            # daily range in % of price
    df["dist20"] = 20 / df.datr                          # $20 adverse move in daily ranges
    df["day_range_atr"] = df.day_range / df.datr
    df["day_move_atr"] = df.day_move / df.datr
    df["m5_atr_rel"] = df.m5_atr / df.datr
    return df


def analyse():
    df = load()
    disc = df[df.year.isin(DISCOVERY)]
    pd.set_option("display.width", 200)
    print(f"{len(df)} trades. Exit types:\n{df.groupby('exit_type').net_pnl.agg(['count', 'sum', 'mean']).round(2)}\n")
    print("by year: win rate, stop-outs, net")
    print(df.groupby("year").agg(n=("win", "size"), wr=("win", "mean"), stopouts=("stopout", "mean"), net=("net_pnl", "sum"),
                                 datr=("datr", "median"), dist20=("dist20", "median")).round(3), "\n")
    be = lambda g: -g[~g.win].net_pnl.mean() / (g[g.win].net_pnl.mean() - g[~g.win].net_pnl.mean())
    cats = ["how", "n_today", "wday", "first_lost"]
    nums = ["hour", "rej_range", "rej_body", "rej_wick", "rsi15", "to_wick", "to_level", "to_wick_atr", "datr", "dist20",
            "m5_atr_rel", "h1_st_dist", "h1_hist", "day_move_atr", "day_range_atr", "day_pos", "wait_min"]
    print("Discovery years 2019-2024: per bucket trades, win rate, net per trade (avg $/trade)")
    for c in cats + nums:
        key = disc[c] if c in cats else pd.qcut(disc[c], 5, duplicates="drop")
        g = disc.groupby(key, observed=True).agg(n=("win", "size"), wr=("win", "mean"), per_trade=("net_pnl", "mean"),
                                                 net=("net_pnl", "sum"))
        print(f"\n{c}\n{g.round(3).to_string()}")


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
