"""Scalping bot: build on the most stable year (2021, from scalp_year_pick.py), then test on every other year.

    .venv/bin/python scalp.py build     # 48 versions on 2021  -> results/scalp/build_trades.csv
    .venv/bin/python scalp.py pick      # apply the selection rule -> results/scalp/selection.json
    .venv/bin/python scalp.py test      # the picks on 2019, 2020, 2022-2026 (+ cost stress) -> test_trades.csv
    .venv/bin/python scalp.py report
    (add --round2 to any of them for the second round, see versions_r2)

Rules: 0.01 lots, the $20/day account (negative balance protection) is the only daily limit; no cap on the number
of trades and no stop after a win (a scalper trades all day). Costs: $0.20 spread (Exness Standard), 100 ms.
Versions: 3 entry types (fade / breakout / pullback) x signal bars 1m or 5m x hours (active 07:00-16:00 UTC, or
quiet 01:00-06:00 + 16:00-20:00) x take profit 0.75 or 1.5 ATR x stop 1 or 2 ATR.

Selection rule, fixed before running: on 2021 only, keep versions with at least 250 trades, profit factor >= 1.10
and a profit in at least 3 of the 4 quarters; pick the highest net. The best version of each entry type is also
carried to the test years so the write-up can show all three, but only the pick counts.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import pandas as pd

from common import BROKER, ROOT, cache_for, year_file

BUILD_YEAR = 2021
TEST_YEARS = (2019, 2020, 2022, 2023, 2024, 2025, 2026)
OUT = ROOT / "results" / "scalp"
RULES = {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 0, "stop_after_wins": 0, "risk_per_trade": 20.0}
COSTS = {"$0.20 spread": {"fixed_spread": 0.20},
         "$0.20 + $0.05 slippage": {"fixed_spread": 0.20, "slippage": 0.05},
         "Raw $0.05 + $3.50/side": {"fixed_spread": 0.05, "commission_per_lot": 3.5}}
SESSIONS = {"active": "07:00-16:00", "quiet": "01:00-06:00,16:00-20:00"}
MODE_PARAM = {"fade": {"k": 2.0}, "breakout": {"lookback": 20}, "pullback": {"rsi_level": 30.0}}


def versions(rnd: int | None = None) -> dict:
    rnd = rnd or ROUND
    if rnd == 2:
        return versions_r2()
    out = {}
    for mode, tf, sess, tp, sl in product(MODE_PARAM, ("1min", "5min"), SESSIONS, (0.75, 1.5), (1.0, 2.0)):
        name = f"{mode} {tf} {sess} tp{tp:g} sl{sl:g}"
        out[name] = {"mode": mode, "tf": tf, "sessions": SESSIONS[sess], "tp_atr": tp, "sl_atr": sl, **MODE_PARAM[mode]}
    return out


# Round 2, decided after round 1 (all 48 lost; per trade they lost about the spread; only quiet-hours fades had
# an edge before costs, ~+$0.15/trade, too small against $0.40-0.80 targets). Same rule, same year:
# fades only, 5m/15m bars so targets are a few dollars, spread capped at 15% of the target.
QUIET = {"quiet": SESSIONS["quiet"], "asia": "00:00-06:00"}


def versions_r2() -> dict:
    out = {}
    for tf, sess, k, tp, sl in product(("5min", "15min"), QUIET, (2.0, 3.0), (1.0, 2.0), (2.0, 3.0)):
        out[f"r2 fade {tf} {sess} k{k:g} tp{tp:g} sl{sl:g}"] = {
            "mode": "fade", "tf": tf, "sessions": QUIET[sess], "k": k, "tp_atr": tp, "sl_atr": sl, "max_cost": 0.15}
    return out


ROUND = 2 if "--round2" in sys.argv else 1
SUFFIX = "_r2" if ROUND == 2 else ""


def run_one(args):
    name, params, year, cost = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = load_strategy(str(ROOT / "strategies" / "scalper.py"))(**params)
    s.risk = DailyRiskRules(**RULES)
    cfg = {**BROKER, "daily_account": 20.0, **COSTS[cost]}
    res = Engine(s, SimBroker(BrokerConfig(**cfg)), ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
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
            if i % 8 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(path, index=False)


def summary(df: pd.DataFrame, by=("version",)) -> pd.DataFrame:
    df = df.copy()
    df["win"] = df.net_pnl > 0
    df["q"] = pd.to_datetime(df.entry_time, utc=True, format="ISO8601").dt.quarter
    g = df.groupby(list(by))
    out = pd.DataFrame({
        "trades": g.size(), "win_rate": (100 * g.win.mean()).round(1), "net": g.net_pnl.sum().round(1),
        "per_trade": g.net_pnl.mean().round(3),
        "pf": g.apply(lambda d: d[d.net_pnl > 0].net_pnl.sum() / max(-d[d.net_pnl < 0].net_pnl.sum(), 1e-9)).round(2),
        "q_pos": g.apply(lambda d: (d.groupby("q").net_pnl.sum() > 0).sum()),
        "stopout_days": g.apply(lambda d: (d.groupby(d.entry_time.str[:10]).net_pnl.sum() <= -19.5).sum()),
        "exits": g.apply(lambda d: ", ".join(f"{k} {v}" for k, v in d.exit_type.value_counts().items())),
    })
    return out


def build():
    run([(n, p, BUILD_YEAR, "$0.20 spread") for n, p in versions().items()], OUT / f"build_trades{SUFFIX}.csv.gz")


def pick():
    df = pd.read_csv(OUT / f"build_trades{SUFFIX}.csv.gz")
    s = summary(df).sort_values("net", ascending=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 70)
    print(s.to_string(), "\n")
    ok = s[(s.trades >= 250) & (s.pf >= 1.10) & (s.q_pos >= 3)]
    groups = MODE_PARAM if ROUND == 1 else {"r2 fade 5min": 0, "r2 fade 15min": 0}
    mode_best = {m: s[s.index.str.startswith(m)].net.idxmax() for m in groups}
    sel = {"pick": ok.net.idxmax() if len(ok) else None, "passing": list(ok.index), "best_per_mode": mode_best}
    print("passing the rule:", sel["passing"] or "none")
    print("pick:", sel["pick"], "| best of each type:", mode_best)
    (OUT / f"selection{SUFFIX}.json").write_text(json.dumps(sel, indent=2))


def test():
    sel = json.loads((OUT / f"selection{SUFFIX}.json").read_text())
    allv = versions()
    names = list(dict.fromkeys(([sel["pick"]] if sel["pick"] else []) + list(sel["best_per_mode"].values())))
    jobs = [(n, allv[n], y, "$0.20 spread") for n in names for y in TEST_YEARS]
    stress = sel["pick"] or max(sel["best_per_mode"].values(), key=lambda n: 0)
    jobs += [(stress, allv[stress], y, c) for y in (BUILD_YEAR,) + TEST_YEARS for c in COSTS if c != "$0.20 spread"]
    run(jobs, OUT / f"test_trades{SUFFIX}.csv.gz")


def report():
    pd.set_option("display.width", 250)
    b = pd.read_csv(OUT / f"build_trades{SUFFIX}.csv.gz")
    t = pd.read_csv(OUT / f"test_trades{SUFFIX}.csv.gz")
    sel = json.loads((OUT / f"selection{SUFFIX}.json").read_text())
    names = list(dict.fromkeys(([sel["pick"]] if sel["pick"] else []) + list(sel["best_per_mode"].values())))
    both = pd.concat([b[b.version.isin(names)], t])
    s = summary(both, by=("version", "cost", "year"))
    print(s.drop(columns="exits").to_string(), "\n")
    net = both.groupby(["version", "cost", "year"]).net_pnl.sum().unstack().round(0)
    net["test years total"] = net[list(TEST_YEARS)].sum(axis=1)
    net["test years +"] = (net[list(TEST_YEARS)] > 0).sum(axis=1)
    print("net $ by year (2021 = build year)\n", net.to_string())


if __name__ == "__main__":
    {"build": build, "pick": pick, "test": test, "report": report}[sys.argv[1]]()
