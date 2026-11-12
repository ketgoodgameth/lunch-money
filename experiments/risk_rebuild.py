"""Risk management rebuilt on the new rules: lose at most $50 a day, any number of trades, any size, and on a
red day try to finish at break-even.

    .venv/bin/python risk_rebuild.py run | analyse

Entries are the saved reaction zone (wick 30%, TP $10/oz, intraday, flat 20:00 UTC, $0.20 spread). The day's cap
is a $50 daily account; a winning trade still ends the day (extra trades after a win lost money in more_trades.py),
but there is no limit on trades after losses or scratches.

Grid (48 versions):
  size        0.01 / 0.02 / 0.03 / 0.05 lots (with no stop the $50 runs out after a $50 / $25 / $16.7 / $10 move)
  structure   "no stop"                 the $50 account is the only stop (the old design, bigger account)
              "stop $25"                each trade can lose at most $25 (never more than what is left), so a day
                                        has room for a second trade; the second trade is a normal one
              "stop $25, recover by target"  after a loss: same size, take profit where the day is back to zero
              "stop $25, recover by size"    after a loss: normal $10/oz target, size raised so it wins the loss
                                        back (kept small enough that what is left covers a $5/oz move)
  scratch     off / 5 / 10: once a trade has been that many $/oz under water, its take profit moves to break-even

Fixed before running:
  selection   on 2019-2024 only: a profit in at least 4 of the 6 years, then the highest net / worst drawdown
              (so a bigger size does not win just by being bigger)
  check       2025-2026: net above zero and net / drawdown not below the baseline's
              (baseline = 0.01 lots, no stop, scratch off)
  if it passes, it is added to the 2018 lock before 2018 is run.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file
from loss_analysis import base_params

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
DISCOVERY = YEARS[:6]
OUT = ROOT / "results" / "risk_rebuild"
CAP = 50.0
LOTS = (0.01, 0.02, 0.03, 0.05)
STRUCT = {"no stop": {},
          "stop $25": {"trade_stop_usd": 25.0},
          "stop $25, recover by target": {"trade_stop_usd": 25.0, "recover": "target"},
          "stop $25, recover by size": {"trade_stop_usd": 25.0, "recover": "size"}}
SCRATCH = {"scratch off": {}, "scratch 5": {"scratch_after": 5.0}, "scratch 10": {"scratch_after": 10.0}}
BASELINE = "0.01 lots | no stop | scratch off"
RULES = {**BASE_RULES, "use_strategy_lots": True, "max_daily_loss": CAP, "max_trades": 0, "stop_after_wins": 1}


def versions() -> dict:
    return {f"{l:g} lots | {a} | {b}": {"lots": l, "day_cap": CAP, **STRUCT[a], **SCRATCH[b]}
            for l, a, b in product(LOTS, STRUCT, SCRATCH)}


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
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**base_params(**params))
    s.risk = DailyRiskRules(**RULES)
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, "fixed_spread": 0.20, "slippage": 0.0, "daily_account": CAP})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)[["entry_time", "exit_time", "side", "lots", "net_pnl", "exit_type", "nbp_credit"]]
    df.insert(0, "year", year)
    df.insert(0, "version", name)
    return df


def run(years=YEARS, path=None):
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(n, p, y) for y in years for n, p in versions().items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 24 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(path or OUT / "trades.csv.gz", index=False)


def drawdown(pnl: pd.Series) -> float:
    eq = pnl.cumsum()
    return float((eq - eq.cummax()).min())


def score(df: pd.DataFrame) -> pd.DataFrame:
    """One row per version for the trades given."""
    df = df.sort_values("exit_time")
    rows = {}
    for name, d in df.groupby("version"):
        day = d.groupby(d.entry_time.str[:10]).net_pnl.sum()
        yr = d.groupby("year").net_pnl.sum()
        dd = drawdown(d.net_pnl)
        rows[name] = {"trades": len(d), "per_day": round(len(d) / len(day), 2),
                      "win%": round(100 * (d.net_pnl > 0).mean(), 1), "net": round(d.net_pnl.sum()),
                      "drawdown": round(dd), "net/dd": round(d.net_pnl.sum() / max(-dd, 1e-9), 2),
                      "years+": int((yr > 0).sum()), "red days%": round(100 * (day < -1).mean()),
                      "flat days%": round(100 * (day.abs() <= 1).mean()),
                      "days at -$50": int((day <= -CAP + 0.5).sum()), "worst day": round(day.min())}
    return pd.DataFrame(rows).T


def analyse():
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 100)
    df = pd.read_csv(OUT / "trades.csv.gz")
    net = df.groupby(["version", "year"]).net_pnl.sum().unstack().round(0)
    disc, chk, full = score(df[df.year.isin(DISCOVERY)]), score(df[df.year.isin((2025, 2026))]), score(df)
    t = disc.add_suffix(" 19-24")[["trades 19-24", "per_day 19-24", "win% 19-24", "net 19-24", "drawdown 19-24",
                                   "net/dd 19-24", "years+ 19-24"]]
    t = t.join(chk.add_suffix(" 25-26")[["net 25-26", "drawdown 25-26", "net/dd 25-26"]])
    t = t.join(full[["net", "drawdown", "red days%", "flat days%", "days at -$50", "worst day"]])
    print("net $ by year\n", net.to_string(), "\n")
    print(t.sort_values("net/dd 19-24", ascending=False).to_string(), "\n")
    ok = t[(t.index != BASELINE) & (t["years+ 19-24"] >= 4)]
    if ok.empty:
        print("selection: none passes")
        sel = {"pick": None}
    else:
        pick = ok["net/dd 19-24"].astype(float).idxmax()
        passed = bool(t.loc[pick, "net 25-26"] > 0 and t.loc[pick, "net/dd 25-26"] >= t.loc[BASELINE, "net/dd 25-26"])
        print(f"selection on 2019-2024: {pick}\n2025-2026 check: {'PASS' if passed else 'FAIL'} "
              f"(net {t.loc[pick, 'net 25-26']:+.0f}, net/drawdown {t.loc[pick, 'net/dd 25-26']} vs baseline "
              f"{t.loc[BASELINE, 'net/dd 25-26']})")
        sel = {"pick": pick, "check_2025_26": passed}
    (OUT / "selection.json").write_text(json.dumps(sel, indent=2))
    key = df.version.str.split(" | ", regex=False, expand=True)
    for i, label in enumerate(("size", "structure", "scratch")):
        g = full.assign(k=key.groupby(df.version).first()[i]).groupby("k")[["net", "drawdown", "net/dd", "red days%",
                                                                           "flat days%", "days at -$50"]].mean().round(2)
        print(f"\naverage by {label} (2019-2026)\n", g.to_string())


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
