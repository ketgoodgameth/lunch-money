"""How much do slippage and delay cost the high win-rate versions? Full-year runs 2019-2026, $0.20 spread.

    .venv/bin/python execution_costs.py run | analyse

Simulator rules: slippage (USD/oz) is applied against you on market entries, end-of-day closes and stop-outs;
the take profit fills at its price like a limit order. Delay = time from signal to fill; price can move meanwhile.
Baseline (earlier results): 100 ms delay, no slippage.
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from common import ROOT, cache_for, year_file
import high_winrate as hw

VERSIONS = ("TP $7.5, held", "TP $7.5, intraday", "TP $10, intraday")
SCENARIOS = {
    "VPS, $0.05 slippage": {"latency_ms": 5, "slippage": 0.05},
    "VPS, $0.10 slippage": {"latency_ms": 5, "slippage": 0.10},
    "home PC, $0.10 slippage": {"latency_ms": 150, "slippage": 0.10},
    "manual on phone (3 s), $0.10": {"latency_ms": 3000, "slippage": 0.10},
    "bad fills, $0.30 slippage": {"latency_ms": 5, "slippage": 0.30},
}
OUT = ROOT / "results" / "execution_costs"


def run_one(args):
    version, scen, year = args
    hw.BROKER_OVER = {**hw.BROKER_OVER, **SCENARIOS[scen]}
    df = hw.run_one((version, hw.variants()[version], year))
    df.insert(1, "scenario", scen)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    for y in hw.YEARS:
        cache_for(year_file(y))
    jobs = [(v, s, y) for y in hw.YEARS for v in VERSIONS for s in SCENARIOS]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 15 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
                pd.concat(frames).to_csv(OUT / "trades.csv", index=False)
    pd.concat(frames).to_csv(OUT / "trades.csv", index=False)


def analyse():
    base = pd.read_csv(ROOT / "results" / "high_winrate" / "trades.csv")
    base = base[base.variant.isin(VERSIONS)].assign(scenario="baseline: 100 ms, no slippage")
    df = pd.concat([base, pd.read_csv(OUT / "trades.csv")])
    df["win"] = df.net_pnl > 0
    order = ["baseline: 100 ms, no slippage", *SCENARIOS]
    rows = []
    for (v, s), d in df.groupby(["variant", "scenario"]):
        yearly = d.groupby("year").net_pnl.sum()
        w, l = d.net_pnl[d.win].mean(), d.net_pnl[~d.win].mean()
        rows.append({"version": v, "scenario": s, "trades": len(d), "win_rate": 100 * d.win.mean(),
                     "breakeven": 100 * -l / (w - l), "net": d.net_pnl.sum(), "years_positive": int((yearly > 0).sum()),
                     "worst_year_wr": d.groupby("year").win.mean().min() * 100})
    t = pd.DataFrame(rows)
    t["scenario"] = pd.Categorical(t.scenario, order, ordered=True)
    t = t.sort_values(["version", "scenario"])
    t["vs_baseline"] = t.net - t.groupby("version").net.transform("first")
    pd.set_option("display.width", 250)
    print(t.round(1).to_string(index=False))
    t.to_csv(OUT / "summary.csv", index=False)


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
