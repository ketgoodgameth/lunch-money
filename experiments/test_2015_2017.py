"""The one-time test of the two versions locked in results/locked_for_2015_2017.json on 2015, 2016 and 2017.

    .venv/bin/python test_2015_2017.py

Full-year runs at $0.20 spread, 0.01 lots, one continuous account. R = profit / amount risked at the stop.
Pass rule (from the lock): total R over the three years above zero AND at least 2 of the 3 years positive.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from common import BROKER, ROOT, cache_for, year_file

YEARS = (2015, 2016, 2017)
OUT = ROOT / "results" / "test_2015_2017"


def run_one(args):
    name, test, year = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    from common import RULES
    logging.getLogger("strategy").setLevel(logging.ERROR)
    params = {k: v for k, v in test["params"].items() if k != "__rules__"}
    s = load_strategy(str(ROOT / "strategies" / test["file"]))(**params)
    s.risk = DailyRiskRules(**{**RULES, **test["params"]["__rules__"]})
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, **test["broker"]})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)[["entry_time", "exit_time", "side", "entry_price", "risk_usd", "net_pnl", "exit_type"]]
    df.insert(0, "year", year)
    df.insert(0, "version", name)
    return df


def data_check(year: int) -> str:
    """Dates and tick count only."""
    f = year_file(year)
    ts = pd.read_csv(f, usecols=["timestamp"], dtype={"timestamp": np.int64}).timestamp
    t = pd.to_datetime(ts, unit="ms", utc=True)
    d = pd.Series(1, index=t).resample("D").count(); wd = d[d.index.day_of_week < 5]
    return (f"{year}: {len(ts):,} ticks, {t.iloc[0]} -> {t.iloc[-1]}, out-of-order {int((ts.diff() < 0).sum())}, "
            f"weekdays without ticks: {[x.strftime('%m-%d') for x in wd[wd == 0].index]}")


def main():
    lock = json.loads((ROOT / "results" / "locked_for_2015_2017.json").read_text())
    for y in YEARS:
        print(data_check(y), flush=True)
        cache_for(year_file(y))
    jobs = [(n, t, y) for y in YEARS for n, t in lock["tests"].items()]
    with ProcessPoolExecutor(6) as pool:
        df = pd.concat(pool.map(run_one, jobs))
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "trades.csv.gz", index=False)
    df["R"] = df.net_pnl / df.risk_usd
    df["win"] = df.net_pnl > 0
    first = df.groupby("year").entry_time.min()      # both versions from the date the ATR is ready
    df = df[df.entry_time >= df.year.map(first)]
    pd.set_option("display.width", 220)
    g = df.groupby(["version", "year"])
    t = pd.DataFrame({"trades": g.size(), "win%": (100 * g.win.mean()).round(1), "R": g.R.sum().round(1),
                      "net $ (0.01 lots)": g.net_pnl.sum().round(0),
                      "TP%": (100 * g.apply(lambda d: (d.exit_type == "take profit").mean())).round(0),
                      "SL%": (100 * g.apply(lambda d: (d.exit_type == "stop loss").mean())).round(0),
                      "avg stop $": g.risk_usd.mean().round(1)})
    print("\n", t.to_string(), "\n")
    for name in lock["tests"]:
        d = df[df.version == name]
        by = d.groupby("year").R.sum()
        tstat = d.R.mean() / (d.R.std(ddof=1) / np.sqrt(len(d)))
        ok = by.sum() > 0 and (by > 0).sum() >= 2
        print(f"{name}: {'PASS' if ok else 'FAIL'} (total R {by.sum():+.1f}, {(by > 0).sum()} of 3 years positive, "
              f"t {tstat:.2f}, net ${d.net_pnl.sum():+.0f} at 0.01 lots, {len(d)} trades)")


if __name__ == "__main__":
    main()
