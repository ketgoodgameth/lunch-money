"""Direction-filtered scalping: the reaction zone (TP $10 intraday) picks the side, quick scalps trade that side.

    .venv/bin/python bias_scalp.py run | analyse

The strategy's 2019-2026 trades (results/high_winrate, the saved version) are the signals; a scalp can only use a
signal from its entry time on, so nothing looks ahead. The scalps run as their own bot on their own $20/day account,
0.01 lots, $0.20 spread, 100 ms latency, no limit on trades per day, 30 s between scalps.

Grid (72 versions): entry market / pullback / 1m MACD x take profit $1 / $2 / $3 x stop $2 / $5 x max hold 2 / 10 min
x window "trade" (while the strategy trade is open) / "day" (until 20:00 UTC).

Selection rule, fixed before running: on 2019-2024 only; at least 300 trades; a profit in at least 4 of the 6
years; highest net. Checks: 2025-2026 net above zero, and the same version with the side flipped (the control)
must be worse over 2019-2026, or the direction is not what makes the money. The pick is then re-run with a $0.30
spread and with $0.05 slippage.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import pandas as pd

from common import BROKER, ROOT, cache_for, year_file

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
DISCOVERY = YEARS[:6]
OUT = ROOT / "results" / "bias_scalp"
SIGNALS = OUT / "signals.csv"
RULES = {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 0, "stop_after_wins": 0, "risk_per_trade": 20.0}
COSTS = {"$0.20 spread": {"fixed_spread": 0.20}, "$0.30 spread": {"fixed_spread": 0.30},
         "$0.20 + $0.05 slippage": {"fixed_spread": 0.20, "slippage": 0.05}}


def versions() -> dict:
    return {f"{e} tp{tp:g} sl{sl:g} hold{h:g} {w}": {"entry": e, "tp_usd": tp, "sl_usd": sl, "max_hold_min": h,
                                                      "window": w}
            for e, tp, sl, h, w in product(("market", "pullback", "m1_macd"), (1.0, 2.0, 3.0), (2.0, 5.0),
                                           (2.0, 10.0), ("trade", "day"))}


def export_signals():
    OUT.mkdir(parents=True, exist_ok=True)
    t = pd.read_csv(ROOT / "results" / "high_winrate" / "trades.csv")
    t = t[t.variant == "TP $10, intraday"]
    t[["year", "entry_time", "exit_time", "side", "net_pnl"]].to_csv(SIGNALS, index=False)
    print(f"{len(t)} strategy trades as signals, net {t.net_pnl.sum():+.2f}")


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
    s = load_strategy(str(ROOT / "strategies" / "bias_scalp.py"))(signals=str(SIGNALS), **params)
    s.risk = DailyRiskRules(**RULES)
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, "daily_account": 20.0, **COSTS[cost]})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    for col, val in (("cost", cost), ("year", year), ("version", name)):
        df.insert(0, col, val)
    return df


def pool_run(jobs, path):
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 24 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(path, index=False)


def run():
    export_signals()
    pool_run([(n, p, y, "$0.20 spread") for y in YEARS for n, p in versions().items()], OUT / "trades.csv.gz")


def summary(df):
    df = df.assign(win=df.net_pnl > 0)
    g = df.groupby("version")
    net = df.groupby(["version", "year"]).net_pnl.sum().unstack().reindex(columns=list(YEARS)).fillna(0).round(0)
    d = df[df.year.isin(DISCOVERY)]
    t = pd.DataFrame({"trades": g.size(), "per_day": (g.size() / g.entry_time.apply(lambda x: x.str[:10].nunique())).round(1),
                      "win_rate": (100 * g.win.mean()).round(1), "per_trade": g.net_pnl.mean().round(3),
                      "trades_2019_24": d.groupby("version").size(),
                      "net_2019_24": net[list(DISCOVERY)].sum(axis=1), "years_pos_2019_24": (net[list(DISCOVERY)] > 0).sum(axis=1),
                      "net_2025_26": net[[2025, 2026]].sum(axis=1), "net": net.sum(axis=1)})
    return t, net


def analyse():
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    df = pd.read_csv(OUT / "trades.csv.gz")
    t, net = summary(df)
    print("net $ by year\n", net.sort_values(list(DISCOVERY)[0]).to_string(), "\n")
    print(t.sort_values("net_2019_24", ascending=False).to_string(), "\n")
    ok = t[(t.trades_2019_24 >= 300) & (t.years_pos_2019_24 >= 4)]
    pick = ok.net_2019_24.idxmax() if len(ok) else None
    print("passing the rule:", list(ok.index) or "none", "\npick:", pick)
    best = {e: t[t.index.str.startswith(e + " ")].net_2019_24.idxmax() for e in ("market", "pullback", "m1_macd")}
    (OUT / "selection.json").write_text(json.dumps({"pick": pick, "best_per_entry": best}, indent=2))


def check():
    """The pick (or the best of each entry type if nothing passes) flipped, and under higher costs."""
    sel = json.loads((OUT / "selection.json").read_text())
    names = list(dict.fromkeys(([sel["pick"]] if sel["pick"] else []) + list(sel["best_per_entry"].values())))
    v = versions()
    jobs = [(n + " FLIPPED", {**v[n], "flip": True}, y, "$0.20 spread") for n in names for y in YEARS]
    stress = sel["pick"] or names[0]
    jobs += [(stress, v[stress], y, c) for y in YEARS for c in COSTS if c != "$0.20 spread"]
    pool_run(jobs, OUT / "check_trades.csv.gz")


def report():
    pd.set_option("display.width", 250)
    a = pd.read_csv(OUT / "trades.csv.gz")
    c = pd.read_csv(OUT / "check_trades.csv.gz")
    sel = json.loads((OUT / "selection.json").read_text())
    names = list(dict.fromkeys(([sel["pick"]] if sel["pick"] else []) + list(sel["best_per_entry"].values())))
    both = pd.concat([a[a.version.isin(names)], c])
    both["version"] = both.version + " | " + both.cost
    t, net = summary(both)
    print(net.join(t[["trades", "win_rate", "per_trade", "net_2019_24", "net_2025_26", "net"]]).to_string())


if __name__ == "__main__":
    {"run": run, "analyse": analyse, "check": check, "report": report}[sys.argv[1]]()
