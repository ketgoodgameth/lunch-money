"""Risk management for growing an account: the reaction zone strategy on a $1,000 account that compounds.

    .venv/bin/python portfolio_growth.py run       # trade lists per stop distance, 2018-2026 -> results/portfolio_growth/trades.csv.gz
    .venv/bin/python portfolio_growth.py analyse   # sizing rules on a $1,000 account -> results/portfolio_growth.log

Goal (user, 2026-11-12): grow the portfolio instead of risking a set budget per day. Start with $1,000.

Step 1, trade lists. The saved reaction zone (wick 30%, TP $10, flat 20:00 UTC, max 2 trades a day, a win ends the
day) at 0.01 lots with a real stop loss a fixed distance from entry: none / $10 / $15 / $20 / $25 / $35 / $50 per oz.
No daily account: one continuous account. With a price-distance stop a trade's result per ounce does not depend on
its size, so every sizing rule below is an exact re-scaling of these lists (lots rounded down to 0.01, minimum 0.01).

Step 2, sizing rules on $1,000:
  fixed 0.01 lots                      no compounding (the comparison)
  risk r% of equity per trade          lots = equity x r% / (stop distance x 100); r = 1, 2, 3, 5, 7.5, 10
  ... with a drawdown brake            risk is halved while equity is 15% or more below its high
An account that falls below $100 is counted as ruined and stops.

Selection, fixed before running (no unseen year is left, so this is a rule, not a clean test):
  build on 2018-2023: 2,000 five-year paths drawn month by month (with replacement) from those years; keep
  versions where fewer than 5% of paths ever fall 50% from a high; pick the highest median final equity.
  check on 2024-2026: start $1,000 on 2024-01-01, real order of trades; pass = ends above $1,000 and never falls
  50% from a high.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import numpy as np
import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file
from loss_analysis import base_params

YEARS = (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
BUILD = (2018, 2019, 2020, 2021, 2022, 2023)
CHECK = (2024, 2025, 2026)
OUT = ROOT / "results" / "portfolio_growth"
START = 1000.0
RUIN = 100.0
STOPS = {"no stop": 0.0, "stop $10": 10.0, "stop $15": 15.0, "stop $20": 20.0, "stop $25": 25.0, "stop $35": 35.0,
         "stop $50": 50.0}
RISKS = (1.0, 2.0, 3.0, 5.0, 7.5, 10.0)
BRAKE = {"": None, " + brake": 0.15}
RULES = {**BASE_RULES, "use_strategy_lots": True, "max_daily_loss": 1e9, "max_trades": 2, "stop_after_wins": 1}
PATHS, HORIZON_MONTHS, SEED = 2000, 60, 7


def run_one(args):
    name, stop, year = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    over = {"lots": 0.01, "day_cap": 1e9, "trade_stop_usd": stop} if stop else {"lots": 0.01}
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**base_params(**over))
    s.risk = DailyRiskRules(**RULES)
    cfg = {**BROKER, "fixed_spread": 0.20, "slippage": 0.0, "daily_account": None, "initial_balance": 1e7}
    res = Engine(s, SimBroker(BrokerConfig(**cfg)), ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)[["entry_time", "exit_time", "side", "lots", "net_pnl", "exit_type"]]
    df.insert(0, "year", year)
    df.insert(0, "stop", name)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(n, d, y) for y in YEARS for n, d in STOPS.items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 7 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(OUT / "trades.csv.gz", index=False)


def models() -> dict:
    """name -> (risk % or None for fixed 0.01 lots, brake drawdown or None)"""
    out = {"fixed 0.01 lots": (None, None)}
    for r, (b, dd) in product(RISKS, BRAKE.items()):
        out[f"risk {r:g}%{b}"] = (r, dd)
    return out


def simulate(pnl_oz: np.ndarray, stop: float, risk, brake, start: float = START):
    """pnl_oz: (paths, trades) result of each trade per ounce ($ at 0.01 lots), NaN = no trade (padding).
    Returns final equity, worst drawdown (fraction of the high), ruined flag, and the equity path (paths, trades+1)."""
    n, m = pnl_oz.shape
    eq = np.full(n, start); peak = eq.copy(); dd = np.zeros(n); dead = np.zeros(n, bool)
    path = np.empty((n, m + 1)); path[:, 0] = start
    for j in range(m):
        x = np.nan_to_num(pnl_oz[:, j])
        if risk is None:
            lots = np.full(n, 0.01)
        else:
            r = np.full(n, risk / 100.0)
            if brake is not None:
                r = np.where(eq <= peak * (1 - brake), r * 0.5, r)
            lots = np.maximum(np.floor(eq * r / (stop * 100.0) / 0.01 + 1e-9) * 0.01, 0.01)
        eq = np.where(dead, eq, eq + x * lots * 100.0)
        dead |= eq < RUIN
        peak = np.maximum(peak, eq)
        dd = np.maximum(dd, 1 - eq / peak)
        path[:, j + 1] = eq
    return eq, dd, dead, path


def month_blocks(df: pd.DataFrame) -> dict:
    df = df.sort_values("exit_time")
    key = df.exit_time.str[:7]
    return {k: g.net_pnl.to_numpy() for k, g in df.groupby(key)}


def bootstrap(blocks: dict, rng) -> np.ndarray:
    keys = sorted(blocks)
    draw = rng.integers(0, len(keys), size=(PATHS, HORIZON_MONTHS))
    seqs = [np.concatenate([blocks[keys[i]] for i in row]) for row in draw]
    out = np.full((PATHS, max(len(s) for s in seqs)), np.nan)
    for i, s in enumerate(seqs):
        out[i, :len(s)] = s
    return out


def risk_base(stop_name: str, df: pd.DataFrame) -> float:
    """Stop distance used for sizing. "no stop" is sized as if the stop were the worst loss seen in the build years."""
    d = STOPS[stop_name]
    return d if d else float(-df[df.year.isin(BUILD)].net_pnl.min())


def analyse():
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    df = pd.read_csv(OUT / "trades.csv.gz")
    df["win"] = df.net_pnl > 0
    print("== trade lists at 0.01 lots (net $ = $/oz) ==")
    net = df.groupby(["stop", "year"]).net_pnl.sum().unstack().round(0)
    g = df.groupby("stop")
    base = pd.DataFrame({"trades": g.size(), "win%": (100 * g.win.mean()).round(1), "net": g.net_pnl.sum().round(0),
                         "avg win": g.apply(lambda d: d[d.win].net_pnl.mean()).round(2),
                         "avg loss": g.apply(lambda d: d[~d.win].net_pnl.mean()).round(2),
                         "worst trade": g.net_pnl.min().round(2), "years +": (net > 0).sum(axis=1)})
    print(pd.concat([net, base], axis=1).loc[list(STOPS)].to_string(), "\n")

    rows, paths = [], {}
    for sname in STOPS:
        d = df[df.stop == sname]
        dist = risk_base(sname, df)
        boot = bootstrap(month_blocks(d[d.year.isin(BUILD)]), np.random.default_rng(SEED))
        full = d.sort_values("exit_time").net_pnl.to_numpy()[None, :]
        chk = d[d.year.isin(CHECK)].sort_values("exit_time").net_pnl.to_numpy()[None, :]
        x = d[d.year.isin(BUILD)].net_pnl.to_numpy() / dist          # result in units of the risk taken
        kelly = 100 * x.mean() / (x ** 2).mean()                       # growth-optimal risk % (small-edge formula)
        for mname, (risk, brake) in models().items():
            fe, dd, dead, _ = simulate(boot, dist, risk, brake)
            f_eq, f_dd, f_dead, f_path = simulate(full, dist, risk, brake)
            c_eq, c_dd, c_dead, _ = simulate(chk, dist, risk, brake)
            paths[(sname, mname)] = f_path[0]
            rows.append({"stop": sname, "sizing": mname, "sized on $/oz": round(dist, 1),
                         "boot median": round(float(np.median(fe))), "boot 5%": round(float(np.percentile(fe, 5))),
                         "boot lose%": round(100 * float((fe < START).mean()), 1),
                         "boot dd median%": round(100 * float(np.median(dd))),
                         "boot dd>=50%": round(100 * float((dd >= 0.5).mean()), 1),
                         "boot ruin%": round(100 * float(dead.mean()), 1),
                         "check 24-26": round(float(c_eq[0])), "check dd%": round(100 * float(c_dd[0])),
                         "full 18-26": round(float(f_eq[0])), "full dd%": round(100 * float(f_dd[0])),
                         "full ruined": bool(f_dead[0]), "kelly% (build)": round(kelly, 1)})
    res = pd.DataFrame(rows).set_index(["stop", "sizing"])
    print("== $1,000 account. boot = 2,000 five-year paths from 2018-2023 months; check = real 2024-2026 from $1,000; "
          "full = real 2018-2026 from $1,000 ==")
    print(res.to_string(), "\n")

    ok = res[(res["boot dd>=50%"] < 5.0)]
    pick = ok["boot median"].idxmax()
    passed = bool(res.loc[pick, "check 24-26"] > START and res.loc[pick, "check dd%"] < 50)
    print(f"selection on 2018-2023: {pick[0]} | {pick[1]}")
    print(f"check 2024-2026: {'PASS' if passed else 'FAIL'} (${res.loc[pick, 'check 24-26']:,.0f}, worst drawdown "
          f"{res.loc[pick, 'check dd%']:.0f}%)")
    print(res.loc[[pick]].T.to_string(), "\n")

    # year by year for the pick, the fixed-size comparison and the same stop at other risk levels
    d = df[df.stop == pick[0]].sort_values("exit_time").reset_index(drop=True)
    show = ["fixed 0.01 lots"] + [f"risk {r:g}%" for r in RISKS]
    yr_end = d.groupby("year").tail(1).index.to_numpy() + 1
    tab = pd.DataFrame({m: paths[(pick[0], m)][yr_end] for m in dict.fromkeys(show + [pick[1]])},
                       index=sorted(d.year.unique())).round(0)
    print(f"equity at the end of each year, {pick[0]}, real order of trades, $1,000 on 2018-01-01\n", tab.to_string())
    allb = bootstrap(month_blocks(d), np.random.default_rng(SEED))
    rows = []
    for mname, (risk, brake) in models().items():
        fe, dd, dead, _ = simulate(allb, STOPS[pick[0]], risk, brake)
        rows.append({"sizing": mname, "median": round(float(np.median(fe))), "5%": round(float(np.percentile(fe, 5))),
                     "95%": round(float(np.percentile(fe, 95))), "lose%": round(100 * float((fe < START).mean()), 1),
                     "dd median%": round(100 * float(np.median(dd))), "dd>=30%": round(100 * float((dd >= 0.3).mean()), 1),
                     "dd>=50%": round(100 * float((dd >= 0.5).mean()), 1), "ruin%": round(100 * float(dead.mean()), 1)})
    print(f"\nfor information, not used to select: 2,000 five-year paths drawn from all nine years, {pick[0]}\n",
          pd.DataFrame(rows).set_index("sizing").to_string())
    (OUT / "selection.json").write_text(json.dumps({"stop": pick[0], "sizing": pick[1], "check_2024_26": passed}, indent=2))
    pd.DataFrame({f"{a} | {b}": v for (a, b), v in paths.items() if a == pick[0]}).to_csv(OUT / "equity_paths.csv", index=False)


CONFIRM = {"risk 3%": {"risk_pct": 3.0}, "risk 10%": {"risk_pct": 10.0}}
CONFIRM_STOP = "stop $20"


def confirm_one(args):
    name, over, year, start = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(
        **base_params(lots=0.01, stop_dist=STOPS[CONFIRM_STOP], **over))
    s.risk = DailyRiskRules(**RULES)
    cfg = {**BROKER, "fixed_spread": 0.20, "slippage": 0.0, "daily_account": None, "initial_balance": start}
    broker = SimBroker(BrokerConfig(**cfg))
    res = Engine(s, broker, ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    return name, year, start, broker.balance, len(res.trades), max((t.lots for t in res.trades), default=0.0)


def confirm():
    """Run the equity-sized strategy in the engine, each year started from the simulated balance, and compare
    the year-end balance with the re-scaled trade list."""
    df = pd.read_csv(OUT / "trades.csv.gz")
    d = df[df.stop == CONFIRM_STOP].sort_values("exit_time").reset_index(drop=True)
    ends = d.groupby("year").tail(1).index.to_numpy() + 1
    starts = np.r_[0, ends[:-1]]
    jobs, want = [], {}
    for name, over in CONFIRM.items():
        risk, brake = models()[name]
        path = simulate(d.net_pnl.to_numpy()[None, :], STOPS[CONFIRM_STOP], risk, brake)[3][0]
        for y, a, b in zip(sorted(d.year.unique()), starts, ends):
            jobs.append((name, over, int(y), float(path[a]))); want[(name, int(y))] = float(path[b])
    with ProcessPoolExecutor(8) as pool:
        rows = [{"sizing": n, "year": y, "start": round(st, 2), "engine end": round(bal, 2),
                 "re-scaled end": round(want[(n, y)], 2), "diff": round(bal - want[(n, y)], 2), "trades": k, "max lots": ml}
                for n, y, st, bal, k, ml in pool.map(confirm_one, jobs)]
    print(pd.DataFrame(rows).to_string(index=False))


def stress():
    """How fragile are the 3% / 5% / 10% results on the real 2018-2026 list ($20 stop)?"""
    df = pd.read_csv(OUT / "trades.csv.gz")
    risks = (3.0, 5.0, 10.0)

    def lst(stop, years=None):
        d = df[df.stop == stop].sort_values("exit_time")
        return d[d.year.isin(years)] if years else d

    def fin(x, stop):
        out = {}
        for r in risks:
            e, dd, _, _ = simulate(np.asarray(x)[None, :], stop, r, None)
            out[f"{r:g}%"] = f"${e[0]:,.0f} (dd {100 * dd[0]:.0f}%)"
        return out

    d = lst(CONFIRM_STOP); x = d.net_pnl.to_numpy(); ys = sorted(d.year.unique())
    print("real order:", fin(x, 20.0))
    rng = np.random.default_rng(1)
    by = {y: d[d.year == y].net_pnl.to_numpy() for y in ys}
    arr = np.stack([np.concatenate([by[y] for y in rng.permutation(ys)]) for _ in range(3000)])
    for r in risks:
        e, dd, dead, _ = simulate(arr, 20.0, r, None)
        print(f"years in random order, {r:g}%: final 5/50/95% {np.percentile(e, [5, 50, 95]).round(0)}, "
              f"worst drawdown 5/50/95% {(100 * np.percentile(dd, [5, 50, 95])).round(0)}")
    grid = np.arange(0.5, 80.5, 0.5)
    for name, yrs in (("2018-2019", (2018, 2019)), ("2020-2022", (2020, 2021, 2022)), ("2018-2023", BUILD),
                      ("2024-2026", CHECK), ("all", ys)):
        xx = lst(CONFIRM_STOP, list(yrs)).net_pnl.to_numpy()
        g = [np.log(np.maximum(1 + r / 100 * xx / 20.0, 1e-9)).sum() for r in grid]
        print(f"growth-optimal risk on {name}: {grid[int(np.argmax(g))]:g}% (average trade ${xx.mean():+.2f})")
    for sname in ("stop $15", "stop $20", "stop $25", "stop $35"):
        print(f"{sname}:", fin(lst(sname).net_pnl.to_numpy(), STOPS[sname]))
    for h in (0.1, 0.2, 0.3, 0.5):
        print(f"every trade ${h:.1f}/oz worse:", fin(x - h, 20.0))
    print("2018-2024 only:", fin(lst(CONFIRM_STOP, list(range(2018, 2025))).net_pnl.to_numpy(), 20.0))
    m = d.groupby(d.exit_time.str[:7]).net_pnl.sum().sort_values(ascending=False)
    for k in (3, 6, 12):
        print(f"without the best {k} of {len(m)} months:",
              fin(d[~d.exit_time.str[:7].isin(m.index[:k])].net_pnl.to_numpy(), 20.0))


if __name__ == "__main__":
    {"run": run, "analyse": analyse, "confirm": confirm, "stress": stress}[sys.argv[1]]()
