"""Reaction zone, TP $10 intraday: up to 10 trades a day, 1m entry timing, and a confidence score that decides
how many trades the day allows.

    .venv/bin/python more_trades.py run | analyse

Grid (27 versions), every one 2019-2026, full-year runs, $0.20 spread, 0.01 lots, $20/day account, no stop loss:
  trades a day  "max 2, stop after a win" (current) / "max 5" / "max 10" (no stop after a win; the $20 account still
                ends the day at -$20)
  entry         "5m"     market order on the 5m confirmation (current)
                "1m"     wait for the 1m MACD to cross while price is back inside the level
                "retest" limit order at the swept level after the 5m confirmation (price must come back to the wick)
  confidence    "off" / "gate 0.5" (every trade needs >= 2 of 4 checks) /
                "scaled" (n-th trade of the day needs 0.25 + 0.125 n: more agreeing checks -> more trades allowed)
                Checks: 1H MACD on the trade's side, 15m SuperTrend on the trade's side, today's closed trades not
                losing, last 15m candle not a news spike.

Selection rule, fixed before running: on 2019-2024 only; win rate >= 55%; beats the current version
(max 2 / 5m / off) in at least 4 of the 6 years; highest net. 2025-2026 is the check: not worse than the current
version. A version that passes is added to the 2018 lock before any 2018 data is downloaded.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file
from loss_analysis import base_params

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
DISCOVERY = (2019, 2020, 2021, 2022, 2023, 2024)
OUT = ROOT / "results" / "more_trades"
BROKER_OVER = {"fixed_spread": 0.20, "slippage": 0.0, "daily_account": 20.0}
TRADES = {"max 2, stop after win": {"max_trades": 2, "stop_after_wins": 1},
          "max 5": {"max_trades": 5, "stop_after_wins": 0},
          "max 10": {"max_trades": 10, "stop_after_wins": 0}}
ENTRY = {"5m": {"exec_mode": "m5"}, "1m": {"exec_mode": "m1"}, "retest": {"exec_mode": "m1_retest"}}
CONF = {"off": {}, "gate 0.5": {"conf_base": 0.5}, "scaled": {"conf_base": 0.25, "conf_step": 0.125}}
BASELINE = "max 2, stop after win | 5m | off"


def versions() -> dict:
    return {f"{a} | {b} | {c}": (TRADES[a], {**ENTRY[b], **CONF[c]})
            for a, b, c in product(TRADES, ENTRY, CONF)}


def run_one(args):
    name, (rules, params), year = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**base_params(**params))
    s.risk = DailyRiskRules(**{**BASE_RULES, "fixed_lots": 0.01, "max_daily_loss": 20.0, **rules})
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, **BROKER_OVER})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    df.insert(0, "year", year)
    df.insert(0, "version", name)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(n, v, y) for y in YEARS for n, v in versions().items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 27 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(OUT / "trades.csv.gz", index=False)


def analyse():
    df = pd.read_csv(OUT / "trades.csv.gz")
    df["win"] = df.net_pnl > 0
    df["day"] = df.entry_time.str[:10]
    pd.set_option("display.width", 250)
    net = df.groupby(["version", "year"]).net_pnl.sum().unstack().round(0)
    disc = df[df.year.isin(DISCOVERY)]
    g, gd = df.groupby("version"), disc.groupby("version")
    daily = df.groupby(["version", "day"]).net_pnl.sum()
    tot = pd.DataFrame({
        "trades": g.size(), "per_day": (g.size() / g.day.nunique()).round(2),
        "max_day": df.groupby(["version", "day"]).size().groupby("version").max(),
        "win_rate": (100 * g.win.mean()).round(1), "wr_2019_24": (100 * gd.win.mean()).round(1),
        "net_2019_24": net[list(DISCOVERY)].sum(axis=1), "net_2025_26": net[[2025, 2026]].sum(axis=1),
        "net": net.sum(axis=1), "years_pos": (net > 0).sum(axis=1),
        "losing_days": daily.lt(0).groupby("version").mean().mul(100).round(0),
        "dd": g.apply(lambda d: (d.sort_values("exit_time").net_pnl.cumsum()
                                 - d.sort_values("exit_time").net_pnl.cumsum().cummax()).min()).round(0),
    })
    tot["beats_base_2019_24"] = net[list(DISCOVERY)].gt(net.loc[BASELINE, list(DISCOVERY)]).sum(axis=1)
    print("net $ by year\n", net.to_string(), "\n")
    print(tot.sort_values("net_2019_24", ascending=False).to_string(), "\n")
    ok = tot[(tot.index != BASELINE) & (tot.wr_2019_24 >= 55) & (tot.beats_base_2019_24 >= 4)]
    if ok.empty:
        print("selection: none passes")
        sel = {"pick": None}
    else:
        pick = ok.net_2019_24.idxmax()
        check = bool(tot.net_2025_26[pick] >= tot.net_2025_26[BASELINE])
        print(f"selection on 2019-2024: {pick}; 2025-2026 check {'PASS' if check else 'FAIL'} "
              f"({tot.net_2025_26[pick]:+.0f} vs current {tot.net_2025_26[BASELINE]:+.0f})")
        sel = {"pick": pick, "check_2025_26": check}
    (OUT / "selection.json").write_text(json.dumps(sel, indent=2))


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
