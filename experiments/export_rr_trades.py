"""Every trade of the user's framework (reaction-zone entries, 0.01 lots, $10 account = the stop, held until
1:3 or 1:5 target or stop-out), $0.20 spread, one full-year run per year 2019-2026.

    .venv/bin/python export_rr_trades.py

Writes results/rr_framework/trades/trades_1to<rr>.csv (all years) and summary.csv.
Full-year continuous runs, so totals can differ slightly from the quarter-by-quarter tables.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file
from rr_framework import COSTS, RULES

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
RRS = (3, 5)


def run(args):
    year, rr = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    base = json.loads((ROOT / "results" / "candidates" / "reaction_zone_wick30.json").read_text())["params"]
    params = {k: v for k, v in base.items() if k != "__rules__"} | {"tp_usd": 10.0 * rr, "flat_hour": 24}
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**params)
    s.risk = DailyRiskRules(**{**BASE_RULES, **RULES})
    broker = SimBroker(BrokerConfig(**{**BROKER, **COSTS["$0.20"]}))
    res = Engine(s, broker, ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    df.insert(0, "year", year)
    df.insert(1, "rr", f"1:{rr}")
    return df


if __name__ == "__main__":
    out = ROOT / "results" / "rr_framework" / "trades"
    out.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        cache_for(year_file(y))
    jobs = [(y, rr) for rr in RRS for y in YEARS]
    with ProcessPoolExecutor(8) as pool:
        frames = list(pool.map(run, jobs))
    df = pd.concat(frames, ignore_index=True)
    for rr in RRS:
        df[df.rr == f"1:{rr}"].to_csv(out / f"trades_1to{rr}.csv", index=False)
    s = df.groupby(["rr", "year"]).agg(trades=("net_pnl", "size"), wins=("net_pnl", lambda x: int((x > 0).sum())),
                                       net=("net_pnl", "sum")).round(2)
    s.to_csv(out / "summary.csv")
    print(s.to_string())
