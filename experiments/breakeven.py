"""Break-even stop for TP $10 intraday: once a trade is be_trigger $/oz in profit, the stop goes to entry + be_offset.

    .venv/bin/python breakeven.py run | analyse

Why: in loss_analysis.py, 23% of the -$20 stop-outs and 25% of the losing 20:00 closes were +$5 in profit first.
Entry filters were also tried there (leave-one-year-out) and none held up, so this changes the exit instead.

Same settings as high_winrate.py "TP $10, intraday" ($0.20 spread, $20/day account, 0.01 lots, max 2, stop after win).
Selection rule, fixed before running: pick on 2019-2024 only, highest total net; it must beat the baseline in at
least 4 of the 6 years. 2025-2026 is then the check: it must not be worse than the baseline there. A version that
passes is added to the 2018 lock before any 2018 data is downloaded.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from common import BROKER, ROOT, RULES as BASE_RULES, cache_for, year_file
from loss_analysis import base_params

YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)
DISCOVERY = (2019, 2020, 2021, 2022, 2023, 2024)
OUT = ROOT / "results" / "breakeven"
RULES = {"fixed_lots": 0.01, "max_daily_loss": 20.0, "max_trades": 2, "stop_after_wins": 1}
BROKER_OVER = {"fixed_spread": 0.20, "slippage": 0.0, "daily_account": 20.0}
VARIANTS = {"baseline": {}} | {f"BE at +${tr:g}, stop at entry{'' if off == 0 else f' +${off:g}'}":
                               {"be_trigger": tr, "be_offset": off} for tr in (3, 5, 7) for off in (0, 1)}


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
    s = load_strategy(str(ROOT / "strategies" / "reaction_zone.py"))(**base_params(**over))
    s.risk = DailyRiskRules(**{**BASE_RULES, **RULES})
    res = Engine(s, SimBroker(BrokerConfig(**{**BROKER, **BROKER_OVER})),
                 ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    df = trades_frame(res)
    df.insert(0, "year", year)
    df.insert(0, "variant", name)
    return df


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(n, o, y) for y in YEARS for n, o in VARIANTS.items()]
    print(f"{len(jobs)} full-year backtests on 8 workers", flush=True)
    with ProcessPoolExecutor(8) as pool:
        df = pd.concat(pool.map(run_one, jobs))
    df.to_csv(OUT / "trades.csv", index=False)


def analyse():
    df = pd.read_csv(OUT / "trades.csv")
    df["win"] = df.net_pnl > 0
    pd.set_option("display.width", 200)
    net = df.groupby(["variant", "year"]).net_pnl.sum().unstack().round(0)
    wr = (100 * df.groupby(["variant", "year"]).win.mean().unstack()).round(0)
    g = df.groupby("variant")
    tot = pd.DataFrame({
        "trades": g.size(), "win_rate": (100 * g.win.mean()).round(1),
        "avg_win": g.apply(lambda d: d[d.win].net_pnl.mean()).round(2),
        "avg_loss": g.apply(lambda d: d[~d.win].net_pnl.mean()).round(2),
        "stopouts": g.apply(lambda d: (d.net_pnl <= -19).sum()),
        "net_2019_24": net[list(DISCOVERY)].sum(1), "net_2025_26": net[[2025, 2026]].sum(1), "net": net.sum(1)})
    tot["breakeven_wr"] = (100 * -tot.avg_loss / (tot.avg_win - tot.avg_loss)).round(1)
    base = net.loc["baseline"]
    tot["beats_base_2019_24"] = (net[list(DISCOVERY)].gt(base[list(DISCOVERY)])).sum(1)
    print("net $ by year\n", net.to_string(), "\n\nwin rate % by year\n", wr.to_string(), "\n")
    print(tot.sort_values("net_2019_24", ascending=False).to_string(), "\n")
    ok = tot[(tot.index != "baseline") & (tot.beats_base_2019_24 >= 4)]
    if ok.empty:
        print("selection: none passes (no version beats the baseline in 4 of 6 discovery years)")
        return
    pick = ok.net_2019_24.idxmax()
    check = tot.net_2025_26[pick] >= tot.net_2025_26["baseline"]
    print(f"selection on 2019-2024: {pick}; 2025-2026 check {'PASS' if check else 'FAIL'} "
          f"({tot.net_2025_26[pick]:+.0f} vs baseline {tot.net_2025_26['baseline']:+.0f})")
    (OUT / "selection.json").write_text(json.dumps({"pick": pick, "params": VARIANTS[pick], "check_2025_26": bool(check)}))


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
