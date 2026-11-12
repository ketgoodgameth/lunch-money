"""HFT-style tick bot (strategies/hft.py): build on 2021, then test the picks on every other year.

    .venv/bin/python hft.py build     # 24 versions on 2021 at zero cost and $0.20 -> results/hft/build_days.csv.gz
    .venv/bin/python hft.py pick      # apply the selection rule -> results/hft/selection.json
    .venv/bin/python hft.py test      # the picks on 2018-2020, 2022-2026 + cost and delay stress -> test_days.csv.gz
    .venv/bin/python hft.py report

Rules (the user's $50-a-day rules): 0.01 lots, any number of trades, the day ends once $50 is lost
($50 daily account with negative balance protection). 01:00-20:00 UTC, flat by 20:00, 100 ms order delay.
Versions: burst trigger ($0.4 in 1 s / $0.8 in 5 s / $2 in 30 s / $4 in 30 s) x mode (momentum / fade at
market / fade with a resting limit) x exit (quick: $0.5 target, $0.5 stop, 60 s; slow: $2, $2, 300 s).

Selection rule, fixed before running: on 2021 at $0.20 only, keep versions with at least 250 trades, profit
factor >= 1.10 and a profit in at least 3 of the 4 quarters; pick the highest net. The best version of each mode
at $0.20 is carried to the test years as well so the write-up can show all three, but only the pick counts.
"Zero cost" quotes both sides at the mid price: it is an upper bound, not a tradeable result (part of what a
fade earns there is the bid and ask taking turns to move, which nobody can trade at the mid).
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import pandas as pd

from common import BROKER, ROOT, cache_for, year_file

BUILD_YEAR = 2021
TEST_YEARS = (2018, 2019, 2020, 2022, 2023, 2024, 2025, 2026)
OUT = ROOT / "results" / "hft"
CAP = 50.0
RULES = {"fixed_lots": 0.01, "max_daily_loss": CAP, "max_trades": 0, "stop_after_wins": 0, "risk_per_trade": CAP}
COSTS = {"zero cost": {"zero_spread": True},
         "$0.20 spread": {"fixed_spread": 0.20},
         "Raw $0.05 + $3.50/side": {"fixed_spread": 0.05, "commission_per_lot": 3.5},
         "$0.20, no delay": {"fixed_spread": 0.20, "latency_ms": 0}}
TRIGGERS = {"$0.4 in 1s": (1_000, 0.4), "$0.8 in 5s": (5_000, 0.8), "$2 in 30s": (30_000, 2.0),
            "$4 in 30s": (30_000, 4.0)}
EXITS = {"quick": {"tp": 0.5, "sl": 0.5, "hold_ms": 60_000}, "slow": {"tp": 2.0, "sl": 2.0, "hold_ms": 300_000}}
MODES = ("momentum", "fade", "fade_limit")
STD = "$0.20 spread"


def versions() -> dict:
    return {f"{m} | {t} | {e}": {"mode": m, "window_ms": TRIGGERS[t][0], "trigger": TRIGGERS[t][1], **EXITS[e]}
            for m, t, e in product(MODES, TRIGGERS, EXITS)}


def run_one(args):
    """One version x year x cost -> one row per trading day."""
    name, params, year, cost = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = load_strategy(str(ROOT / "strategies" / "hft.py"))(**params)
    s.risk = DailyRiskRules(**RULES)
    cfg = {**BROKER, "daily_account": CAP, **COSTS[cost]}
    res = Engine(s, SimBroker(BrokerConfig(**cfg)), ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    days: dict = {}
    for t in res.trades:
        d = days.setdefault(t.entry_ts // 86_400_000, {"trades": 0, "wins": 0, "net": 0.0, "won": 0.0, "lost": 0.0,
                                                       "hold_s": 0.0, "tp": 0, "sl": 0, "time": 0, "other": 0})
        n = t.net_pnl
        d["trades"] += 1; d["net"] += n; d["hold_s"] += (t.exit_ts - t.entry_ts) / 1000
        if n > 0:
            d["wins"] += 1; d["won"] += n
        else:
            d["lost"] -= n
        d["tp" if t.reason == "tp" else "sl" if t.reason == "sl" else "time" if t.reason == "time" else "other"] += 1
    df = pd.DataFrame.from_dict(days, orient="index").rename_axis("day").reset_index()
    if df.empty:
        df = pd.DataFrame(columns=["day", "trades", "wins", "net", "won", "lost", "hold_s", "tp", "sl", "time", "other"])
    for col, val in (("cost", cost), ("year", year), ("version", name)):
        df.insert(0, col, val)
    return df


def run(jobs, path):
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 8 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(path, index=False)


def summary(df: pd.DataFrame, by=("version", "cost")) -> pd.DataFrame:
    df = df.copy()
    df["q"] = pd.to_datetime(df.day, unit="D").dt.quarter
    g = df.groupby(list(by))
    tr = g.trades.sum()
    out = pd.DataFrame({
        "trades": tr, "per_day": (tr / g.day.nunique()).round(0), "win_rate": (100 * g.wins.sum() / tr).round(1),
        "net": g.net.sum().round(0), "per_trade": (g.net.sum() / tr).round(3),
        "pf": (g.won.sum() / g.lost.sum().clip(lower=1e-9)).round(2),
        "q_pos": g.apply(lambda d: int((d.groupby("q").net.sum() > 0).sum())),
        "days_at_cap": g.apply(lambda d: int((d.net <= -CAP + 0.5).sum())), "days": g.day.nunique(),
        "worst_day": g.net.min().round(1), "hold_s": (g.hold_s.sum() / tr).round(0),
        "tp%": (100 * g.tp.sum() / tr).round(0), "sl%": (100 * g.sl.sum() / tr).round(0),
        "time%": (100 * g.time.sum() / tr).round(0),
    })
    return out


def build():
    run([(n, p, BUILD_YEAR, c) for c in ("zero cost", STD) for n, p in versions().items()], OUT / "build_days.csv.gz")


def pick():
    df = pd.read_csv(OUT / "build_days.csv.gz")
    s = summary(df)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    for c in ("zero cost", STD):
        print(f"== {BUILD_YEAR}, {c} ==\n", s.xs(c, level="cost").sort_values("net", ascending=False).to_string(), "\n")
    std = s.xs(STD, level="cost")
    ok = std[(std.trades >= 250) & (std.pf >= 1.10) & (std.q_pos >= 3)]
    mode_best = {m: std[std.index.str.startswith(m + " |")].net.idxmax() for m in MODES}
    sel = {"pick": ok.net.idxmax() if len(ok) else None, "passing": list(ok.index), "best_per_mode": mode_best}
    print("passing the rule:", sel["passing"] or "none")
    print("pick:", sel["pick"], "| best of each mode:", mode_best)
    (OUT / "selection.json").write_text(json.dumps(sel, indent=2))


def carried(sel) -> list:
    return list(dict.fromkeys(([sel["pick"]] if sel["pick"] else []) + list(sel["best_per_mode"].values())))


def test():
    sel = json.loads((OUT / "selection.json").read_text())
    allv = versions()
    names = carried(sel)
    jobs = [(n, allv[n], y, c) for n in names for y in TEST_YEARS for c in ("zero cost", STD)]
    jobs += [(n, allv[n], y, c) for n in names for y in (BUILD_YEAR,) + TEST_YEARS
             for c in ("Raw $0.05 + $3.50/side", "$0.20, no delay")]
    run(jobs, OUT / "test_days.csv.gz")


def report():
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    sel = json.loads((OUT / "selection.json").read_text())
    names = carried(sel)
    b = pd.read_csv(OUT / "build_days.csv.gz")
    both = pd.concat([b[b.version.isin(names)], pd.read_csv(OUT / "test_days.csv.gz")])
    print(summary(both).to_string(), "\n")
    net = both.groupby(["version", "cost", "year"]).net.sum().unstack().round(0)
    net["test years total"] = net[list(TEST_YEARS)].sum(axis=1)
    net["test years +"] = (net[list(TEST_YEARS)] > 0).sum(axis=1)
    print(f"net $ by year ({BUILD_YEAR} = build year)\n", net.to_string(), "\n")
    pt = (both.groupby(["version", "cost"]).net.sum() / both.groupby(["version", "cost"]).trades.sum()).unstack().round(3)
    print("net $ per trade (0.01 lots = 1 oz, so this is also $/oz), all years\n", pt.to_string())


if __name__ == "__main__":
    {"build": build, "pick": pick, "test": test, "report": report}[sys.argv[1]]()
