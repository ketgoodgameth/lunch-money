"""Walk-forward test of self-adjustment: does re-picking the settings on a schedule beat keeping them fixed?

    .venv/bin/python walk_forward.py run | analyse

Menu (20 settings the bot may switch between): rejection wick 25/30/35/40% x take profit $5/7.5/10/12.5/15.
Everything else is the saved reaction-zone version: intraday (flat 20:00 UTC), no stop, $20/day account, 0.01 lots,
max 2 trades, stop after a win, $0.20 spread. Each setting is run once over 2019-2026; because every day starts
with a fresh $20 and ends flat, a walk-forward path is just each period's trades taken from the setting chosen
for that period.

Self-adjustment rule: every R months, pick the setting with the highest net over the last L months and trade it
for the next R months. R = 1 / 3 / 6, L = 6 / 12 / 24 (9 versions). All are scored on 2021-2026, the first period
every version can trade.

Fixed before running:
  primary version   re-pick every 3 months on the last 12 months
  benchmarks        current fixed setting (wick 30%, TP $10; note it was chosen knowing these years),
                    the average of all 20 fixed settings (what a fixed setting picked blind would make),
                    the best fixed setting and the best pick every period (hindsight, upper bounds)
  verdict           self-adjustment adds something only if the primary version beats the current fixed setting
                    AND at least 5 of the 9 versions beat the average fixed setting
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file
from loss_analysis import base_params

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
OUT = ROOT / "results" / "walk_forward"
WICKS, TPS = (0.25, 0.30, 0.35, 0.40), (5.0, 7.5, 10.0, 12.5, 15.0)
CURRENT = "wick 30% | TP $10"
REPICK, LOOKBACK, PRIMARY = (1, 3, 6), (6, 12, 24), (3, 12)
SCORE_FROM = "2021-01"


def menu() -> dict:
    return {f"wick {w:.0%} | TP ${tp:g}": {"min_wick": w, "tp_usd": tp} for w, tp in product(WICKS, TPS)}


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
    s.risk = DailyRiskRules(**{**BASE_RULES, "fixed_lots": 0.01, "max_daily_loss": 20.0})
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, "fixed_spread": 0.20, "slippage": 0.0, "daily_account": 20.0})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)[["entry_time", "side", "net_pnl", "exit_type"]]
    df.insert(0, "year", year)
    df.insert(0, "setting", name)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(n, p, y) for y in YEARS for n, p in menu().items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    frames = []
    with ProcessPoolExecutor(8) as pool:
        for i, df in enumerate(pool.map(run_one, jobs), 1):
            frames.append(df)
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    pd.concat(frames).to_csv(OUT / "trades.csv.gz", index=False)


def monthly() -> pd.DataFrame:
    """Net per month (rows) and setting (columns)."""
    df = pd.read_csv(OUT / "trades.csv.gz")
    df["month"] = df.entry_time.str[:7]
    m = df.groupby(["month", "setting"]).net_pnl.sum().unstack().fillna(0.0)
    return m.reindex(pd.period_range(m.index[0], m.index[-1], freq="M").astype(str)).fillna(0.0)


def walk(m: pd.DataFrame, repick: int, lookback: int):
    """Returns (net per month, chosen setting per month) from SCORE_FROM on."""
    start = m.index.get_loc(SCORE_FROM)
    net, chosen, pick = {}, {}, None
    for i in range(start, len(m)):
        if (i - start) % repick == 0:
            pick = m.iloc[i - lookback:i].sum().idxmax()
        net[m.index[i]], chosen[m.index[i]] = m.iloc[i][pick], pick
    return pd.Series(net), pd.Series(chosen)


def describe(name, s: pd.Series, switches=None) -> dict:
    y = s.groupby(s.index.str[:4]).sum()
    eq = s.cumsum()
    return {"version": name, **{k: round(v) for k, v in y.items()}, "total": round(s.sum()),
            "years +": int((y > 0).sum()), "months +": f"{(s > 0).mean():.0%}",
            "worst drawdown": round(float((eq - eq.cummax()).min())), "switches": switches if switches is not None else ""}


def analyse():
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 100)
    m = monthly()
    full = m.groupby(m.index.str[:4]).sum().T
    full["total"] = full.sum(axis=1)
    print("every fixed setting, net $ by year (2019-2026)\n", full.round(0).sort_values("total", ascending=False).to_string(), "\n")
    sc = m.loc[SCORE_FROM:]
    rows = [describe(f"fixed: current ({CURRENT})", sc[CURRENT]),
            describe("fixed: average of all 20", sc.mean(axis=1)),
            describe(f"fixed: best with hindsight ({sc.sum().idxmax()})", sc[sc.sum().idxmax()]),
            describe("perfect pick every month (hindsight)", sc.max(axis=1))]
    wf = {}
    for r, l in product(REPICK, LOOKBACK):
        net, chosen = walk(m, r, l)
        wf[(r, l)] = net
        tag = " (primary)" if (r, l) == PRIMARY else ""
        rows.append(describe(f"re-pick every {r} mo on last {l} mo{tag}", net, int((chosen != chosen.shift()).sum() - 1)))
    t = pd.DataFrame(rows).set_index("version")
    print(f"scored on {SCORE_FROM} to {m.index[-1]}\n", t.to_string(), "\n")
    avg, cur = sc.mean(axis=1).sum(), sc[CURRENT].sum()
    beat_avg = sum(v.sum() > avg for v in wf.values())
    prim = wf[PRIMARY].sum()
    rank = (sc.sum() > prim).sum() + 1
    print(f"primary version ${prim:+.0f} vs current fixed ${cur:+.0f}: {'beats' if prim > cur else 'does not beat'} it; "
          f"it would rank {rank} of 20 among fixed settings")
    print(f"{beat_avg} of 9 walk-forward versions beat the average fixed setting (${avg:+.0f})")
    print("verdict:", "self-adjustment adds something" if prim > cur and beat_avg >= 5 else "self-adjustment does not add anything here")
    _, chosen = walk(m, *PRIMARY)
    print("\nprimary version's picks:\n", chosen[chosen != chosen.shift()].to_string())
    # does last period's best setting tend to stay good? rank correlation of settings, lookback vs next period
    for r, l in ((3, 12), (6, 24), (12, 24)):
        cs = []
        for i in range(l, len(m) - r + 1, r):
            cs.append(m.iloc[i - l:i].sum().rank().corr(m.iloc[i:i + r].sum().rank()))
        print(f"rank correlation, last {l} mo vs next {r} mo across settings: mean {pd.Series(cs).mean():+.2f} "
              f"({(pd.Series(cs) > 0).mean():.0%} of periods positive, {len(cs)} periods)")


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
