"""High win-rate reaction zone: small target, no stop loss, $20/day account. Goal: win rate 60%+ in every year.

    .venv/bin/python high_winrate.py run | analyse

Entries: the saved candidate (wick >= 30%, lookback 20), unchanged. Varied, decided before running:
  target   fixed $7.5 / $10 per oz, or a fraction of the 14-day average daily range (0.15 / 0.2 / 0.25 / 0.3 / 0.4)
  exit     closed by 20:00 UTC ("intraday") or held until target or the $20 account stop-out ("held")
Full-year continuous runs (no quarter splits), 2019-2026, $0.20 spread, 0.01 lots, max 2 trades, stop after a win.
Scaled targets need 14 days of daily bars, so they skip the first ~3 weeks of each year.

Selection rule (fixed in advance): among versions with win rate >= 60% overall, pick the highest
worst-year win rate, then the highest total net; it must be net positive in at least 6 of 8 years.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
TARGETS = {"$7.5": {"tp_usd": 7.5}, "$10": {"tp_usd": 10.0},
           **{f"{f:g} x daily range": {"tp_atr": f} for f in (0.15, 0.2, 0.25, 0.3, 0.4)}}
EXITS = {"intraday": 20, "held": 24}
RULES = {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 2, "stop_after_wins": 1}
BROKER_OVER = {"fixed_spread": 0.20, "slippage": 0.0, "daily_account": 20.0}
OUT = ROOT / "results" / "high_winrate"


def variants() -> dict:
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    base = {k: v for k, v in base.items() if k != "__rules__"}
    return {f"TP {t}, {e}": {**base, **tp, "flat_hour": fh} for t, tp in TARGETS.items() for e, fh in EXITS.items()}


def run_one(args):
    name, params, year = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**params)
    s.risk = DailyRiskRules(**{**BASE_RULES, **RULES})
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, **BROKER_OVER})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    df.insert(0, "year", year)
    df.insert(0, "variant", name)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        cache_for(year_file(y))
    jobs = [(n, p, y) for y in YEARS for n, p in variants().items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 14 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
                pd.concat(frames).to_csv(OUT / "trades.csv", index=False)
    pd.concat(frames).to_csv(OUT / "trades.csv", index=False)


def analyse():
    df = pd.read_csv(OUT / "trades.csv")
    df["win"] = df.net_pnl > 0
    g = df.groupby(["variant", "year"])
    by = pd.DataFrame({"trades": g.size(), "win_rate": 100 * g.win.mean(), "net": g.net_pnl.sum()})
    wr = by.win_rate.unstack()
    net = by.net.unstack()
    tot = df.groupby("variant").agg(trades=("win", "size"), win_rate=("win", "mean"), net=("net_pnl", "sum"),
                                    avg_win=("net_pnl", lambda x: x[x > 0].mean()),
                                    avg_loss=("net_pnl", lambda x: x[x <= 0].mean()))
    tot["win_rate"] *= 100
    tot["worst_year_wr"] = wr.min(axis=1)
    tot["years_positive"] = (net > 0).sum(axis=1)
    tot["breakeven_wr"] = 100 * -tot.avg_loss / (tot.avg_win - tot.avg_loss)
    pd.set_option("display.width", 250)
    print("win rate % by year\n", wr.round(0).to_string(), "\n")
    print("net $ by year\n", net.round(0).to_string(), "\n")
    print(tot.sort_values(["worst_year_wr"], ascending=False).round(1).to_string(), "\n")
    ok = tot[(tot.win_rate >= 60) & (tot.years_positive >= 6)]
    if ok.empty:
        print("selection: no version meets win rate >= 60% with 6+ positive years")
    else:
        pick = ok.sort_values(["worst_year_wr", "net"], ascending=False).index[0]
        print(f"selection: {pick}")
        (OUT / "selection.json").write_text(json.dumps({"variant": pick, "params": variants()[pick],
                                                        "rules": RULES, "broker": BROKER_OVER}, indent=2))


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
