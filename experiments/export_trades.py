"""Every trade of the New York breakout (stop at 0.5 of the range, $0.20 spread), one full-year run per year.

    .venv/bin/python export_trades.py [stop_frac] [spread]

Writes results/half_stop/trades/trades_<year>.csv, trades_all_years.csv and summary_by_year.csv.
Each year runs as one continuous backtest, so totals can differ slightly from the quarter-by-quarter tables.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from common import BROKER, ROOT, RULES, cache_for, year_file

YEARS = (2022, 2023, 2024, 2025, 2026)


def run_year(args):
    year, stop_frac, spread = args
    sys.path.insert(0, str(ROOT))
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import summarize, trades_frame
    from xautest.risk import DailyRiskRules
    lock = json.loads((ROOT / "results" / "flat_year" / "locked_for_2026.json").read_text())
    p = lock["primary"]["params"]
    params = {k: v for k, v in p.items() if k != "__rules__"} | {"stop_frac": stop_frac}
    s = load_strategy(str(ROOT / "strategies" / "opening_range.py"))(**params)
    s.risk = DailyRiskRules(**{**RULES, **p["__rules__"]})
    broker = SimBroker(BrokerConfig(**{**BROKER, "fixed_spread": spread}))
    res = Engine(s, broker, ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    df.insert(0, "year", year)
    sm = summarize(res)
    return year, df, {"year": year, "trades": sm["trades"], "net_profit": sm["net_profit"],
                      "win_rate_pct": sm.get("win_rate_pct"), "max_drawdown": sm.get("max_drawdown"),
                      "longs": int((df.side == "BUY").sum()) if len(df) else 0,
                      "shorts": int((df.side == "SELL").sum()) if len(df) else 0}


if __name__ == "__main__":
    stop_frac = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    spread = float(sys.argv[2]) if len(sys.argv) > 2 else 0.20
    out = ROOT / "results" / "half_stop" / "trades"
    out.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        cache_for(year_file(y))
    with ProcessPoolExecutor(len(YEARS)) as pool:
        results = sorted(pool.map(run_year, [(y, stop_frac, spread) for y in YEARS]), key=lambda r: r[0])
    frames, rows = [], []
    for y, df, row in results:
        df.to_csv(out / f"trades_{y}.csv", index=False)
        frames.append(df); rows.append(row)
    pd.concat(frames).to_csv(out / "trades_all_years.csv", index=False)
    summ = pd.DataFrame(rows)
    summ.to_csv(out / "summary_by_year.csv", index=False)
    print(summ.to_string(index=False))
    print(f"\ntotal net {summ.net_profit.sum():.2f} over {summ.trades.sum()} trades; files in {out}")
