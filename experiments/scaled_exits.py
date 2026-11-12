"""Does the reaction zone still work when its target and stop move with the market instead of being fixed dollars?

    .venv/bin/python scaled_exits.py run       # 9 versions x 2018-2026 -> results/scaled_exits/trades.csv.gz
    .venv/bin/python scaled_exits.py analyse

Why: the fixed $10 target / $20 stop made 78% of its 2018-2026 profit in 2024-2026, when gold's daily range was
several times larger than in 2018-2019 (the target was reached by 10% of trades in 2018 and 74% in 2026). If the
entries have an edge of their own, it should show when both exits are a fixed share of the 14-day average daily
range (ATR), so that every year faces the same relative target and stop.

Versions: take profit 0.15 / 0.25 / 0.40 x ATR; stop 0.30 / 0.50 / 0.80 x ATR. Everything else as the saved
strategy (wick 30%, flat 20:00 UTC, max 2 trades a day, a win ends the day), 0.01 lots, $0.20 spread, one
continuous account. Baseline: fixed $10 target, $20 stop (results/portfolio_growth), cut to the same dates
(the ATR needs 14 daily bars at the start of each year).

Results are measured in R = profit / amount risked at the stop, the unit that matters once size is a % of the
balance. Selection, fixed before running:
  build 2018-2023: R positive in at least 4 of the 6 years; highest total R.
  check 2024-2026: total R positive and at least 2 of the 3 years positive.
  adopt over the baseline only if the check passes AND, on a $1,000 account at 3% risk, the median of 2,000
  five-year paths drawn from 2018-2023 months is higher than the baseline's with fewer than 5% of paths
  falling 50% from a high.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import numpy as np
import pandas as pd

from common import BROKER, ROOT, cache_for, year_file
from loss_analysis import base_params
from portfolio_growth import BUILD, CHECK, HORIZON_MONTHS, PATHS, RUIN, RULES, SEED, START, YEARS

OUT = ROOT / "results" / "scaled_exits"
TPS, STOPS = (0.15, 0.25, 0.40), (0.30, 0.50, 0.80)
BASE = "fixed: TP $10, stop $20"
RISK = 3.0


def versions() -> dict:
    return {f"TP {a:g} ATR, stop {b:g} ATR": {"tp_atr": a, "stop_atr": b} for a, b in product(TPS, STOPS)}


def run_one(args):
    name, over, year = args
    sys.path.insert(0, str(ROOT))
    import logging
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    logging.getLogger("strategy").setLevel(logging.ERROR)
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**base_params(lots=0.01, **over))
    s.risk = DailyRiskRules(**RULES)
    cfg = {**BROKER, "fixed_spread": 0.20, "slippage": 0.0, "daily_account": None, "initial_balance": 1e7}
    res = Engine(s, SimBroker(BrokerConfig(**cfg)), ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)[["entry_time", "exit_time", "side", "entry_price", "risk_usd", "net_pnl", "exit_type"]]
    df.insert(0, "year", year)
    df.insert(0, "version", name)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(n, o, y) for y in YEARS for n, o in versions().items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 9 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(OUT / "trades.csv.gz", index=False)


def load() -> pd.DataFrame:
    df = pd.read_csv(OUT / "trades.csv.gz")
    b = pd.read_csv(ROOT / "results" / "portfolio_growth" / "trades.csv.gz")
    b = b[b.stop == "stop $20"].drop(columns=["stop", "lots"]).assign(version=BASE, risk_usd=20.0)
    first = df.groupby("year").entry_time.min()                    # the ATR is ready from here each year
    b = b[b.entry_time >= b.year.map(first)]
    df = pd.concat([df, b], ignore_index=True)
    df["R"] = df.net_pnl / df.risk_usd
    df["win"] = df.net_pnl > 0
    return df.sort_values(["version", "exit_time"]).reset_index(drop=True)


def grow(r: np.ndarray, stop: np.ndarray, risk: float = RISK, start: float = START):
    """(paths, trades) R results and stop distances -> final equity and worst drawdown; lots rounded down to 0.01,
    minimum 0.01, as in portfolio_growth.simulate but with a stop distance per trade."""
    n, m = r.shape
    eq = np.full(n, start); peak = eq.copy(); dd = np.zeros(n); dead = np.zeros(n, bool)
    for j in range(m):
        x, d = np.nan_to_num(r[:, j]), np.nan_to_num(stop[:, j], nan=1.0)
        d = np.where(d > 0, d, 1.0)
        lots = np.maximum(np.floor(eq * risk / 100.0 / (d * 100.0) / 0.01 + 1e-9) * 0.01, 0.01)
        eq = np.where(dead, eq, eq + x * d * lots * 100.0)
        dead |= eq < RUIN
        peak = np.maximum(peak, eq)
        dd = np.maximum(dd, 1 - eq / peak)
    return eq, dd


def boot(d: pd.DataFrame, rng):
    key = d.exit_time.str[:7]
    blocks = {k: (g.R.to_numpy(), g.risk_usd.to_numpy()) for k, g in d.groupby(key)}
    keys = sorted(blocks)
    draw = rng.integers(0, len(keys), size=(PATHS, HORIZON_MONTHS))
    seqs = [(np.concatenate([blocks[keys[i]][0] for i in row]), np.concatenate([blocks[keys[i]][1] for i in row]))
            for row in draw]
    m = max(len(a) for a, _ in seqs)
    r, s = np.full((PATHS, m), np.nan), np.full((PATHS, m), np.nan)
    for i, (a, b) in enumerate(seqs):
        r[i, :len(a)] = a; s[i, :len(b)] = b
    return r, s


def analyse():
    pd.set_option("display.width", 260); pd.set_option("display.max_rows", 200)
    df = load()
    order = [BASE] + list(versions())
    by = df.groupby(["version", "year"])
    R = by.R.sum().unstack().round(1).loc[order]
    print("total R by year (R = profit / amount risked at the stop)\n", R.to_string(), "\n")
    print("win rate % by year\n", (100 * by.win.mean()).unstack().round(0).loc[order].to_string(), "\n")
    print("net $ at 0.01 lots by year\n", by.net_pnl.sum().unstack().round(0).loc[order].to_string(), "\n")
    rows = []
    for v in order:
        d = df[df.version == v]
        b, c = d[d.year.isin(BUILD)], d[d.year.isin(CHECK)]
        t = lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
        br, bs = boot(b, np.random.default_rng(SEED))
        fe, dd = grow(br, bs)
        f_eq, f_dd = grow(d.R.to_numpy()[None, :], d.risk_usd.to_numpy()[None, :])
        c_eq, c_dd = grow(c.R.to_numpy()[None, :], c.risk_usd.to_numpy()[None, :])
        rows.append({"version": v, "trades": len(d), "R 18-23": round(b.R.sum(), 1), "years+ 18-23": int((R.loc[v, list(BUILD)] > 0).sum()),
                     "t 18-23": round(t(b.R), 2), "R 24-26": round(c.R.sum(), 1), "years+ 24-26": int((R.loc[v, list(CHECK)] > 0).sum()),
                     "t 24-26": round(t(c.R), 2), "R all": round(d.R.sum(), 1), "t all": round(t(d.R), 2),
                     "years+ all": int((R.loc[v] > 0).sum()),
                     "share of R from 24-26 %": round(100 * c.R.sum() / d.R.sum()) if d.R.sum() > 0 else np.nan,
                     "avg stop $": round(d.risk_usd.mean(), 1),
                     "boot median": round(float(np.median(fe))), "boot 5%": round(float(np.percentile(fe, 5))),
                     "boot dd>=50%": round(100 * float((dd >= 0.5).mean()), 1),
                     "check $1000": round(float(c_eq[0])), "full $1000": round(float(f_eq[0])), "full dd%": round(100 * float(f_dd[0]))})
    res = pd.DataFrame(rows).set_index("version")
    print(f"R-based summary and a $1,000 account at {RISK:g}% risk per trade\n", res.to_string(), "\n")
    cand = res[(res.index != BASE) & (res["years+ 18-23"] >= 4)]
    if cand.empty:
        print("selection: no scaled version is positive in 4 of the 6 build years")
        sel = {"pick": None}
    else:
        pick = cand["R 18-23"].idxmax()
        check = bool(res.loc[pick, "R 24-26"] > 0 and res.loc[pick, "years+ 24-26"] >= 2)
        better = bool(res.loc[pick, "boot median"] > res.loc[BASE, "boot median"] and res.loc[pick, "boot dd>=50%"] < 5.0)
        print(f"selection on 2018-2023: {pick}")
        print(f"check 2024-2026: {'PASS' if check else 'FAIL'} (R {res.loc[pick, 'R 24-26']:+.1f}, {res.loc[pick, 'years+ 24-26']} of 3 years)")
        print(f"beats the baseline on the $1,000 build paths: {'YES' if better else 'NO'} "
              f"(median ${res.loc[pick, 'boot median']:,} vs ${res.loc[BASE, 'boot median']:,}; "
              f"50% drawdown in {res.loc[pick, 'boot dd>=50%']}% of paths)")
        print("verdict:", "ADOPT" if check and better else "keep the fixed $10 / $20 exits")
        sel = {"pick": pick, "check_2024_26": check, "beats_baseline": better}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(json.dumps(sel, indent=2))


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
