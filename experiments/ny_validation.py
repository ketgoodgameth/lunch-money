"""Is the New York breakout edge real? Placebos, ablations and a bootstrap, on 2022-2025 at $0.20.

    .venv/bin/python ny_validation.py placebo     # other range times, trend reversed, New York local time
    .venv/bin/python ny_validation.py bootstrap   # reshuffle trading days; trade shape; weekdays
"""
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from common import BROKER, COSTS, ROOT, RULES, cache_for, run_grid, year_file
from xautest import optimize as opt
from xautest.models import dt_to_ms

YEARS = (2022, 2023, 2024, 2025)
STD = {"Standard $0.20": COSTS["Standard $0.20"]}
OUT = ROOT / "results" / "flat_year"


def base():
    sel = json.loads((OUT / "selected.json").read_text())
    return sel["file"], sel["params"]


def placebo():
    f, p = base()
    variants = {"BASE 13:30 UTC (New York open)": p}
    for start in ("01:00", "04:00", "07:00", "10:00", "16:00"):
        variants[f"placebo: range at {start} UTC"] = {**p, "range_start": start}
    variants["placebo: trade AGAINST the 1h trend"] = {**p, "direction": "against"}
    variants["New York time 09:30 (stock open, DST-aware)"] = {**p, "range_start": "09:30", "range_tz": "America/New_York"}
    variants["New York time 08:30 (US data, DST-aware)"] = {**p, "range_start": "08:30", "range_tz": "America/New_York"}
    df = pd.concat([run_grid(f, variants, y, costs=STD) for y in YEARS])
    df.to_csv(OUT / "placebo.csv", index=False)
    t = df.pivot(index="variant", columns="year", values="net")[list(YEARS)].loc[list(variants)]
    n = df.groupby("variant").trades.sum()
    pd.set_option("display.width", 220)
    print(t.assign(total=t.sum(axis=1), trades=n, years_positive=(t > 0).sum(axis=1)).round(2).to_string())


def bootstrap(n_boot=20_000, seed=1):
    f, p = base()
    jobs, daily, pnl = [], {}, []
    for y in YEARS:
        cache = cache_for(year_file(y))
        w = (dt_to_ms(datetime(y, 1, 1, tzinfo=timezone.utc)), dt_to_ms(datetime(y + 1, 1, 1, tzinfo=timezone.utc)))
        clean = {k: v for k, v in p.items() if k != "__rules__"}
        res = opt.run_jobs([(clean, w, STD["Standard $0.20"], p.get("__rules__"))], cache,
                           str(ROOT / "strategies" / f), {}, BROKER, RULES, 1)
        daily.update(res[0]["_daily"]); pnl.extend(res[0]["_pnl"])
    d = pd.Series(daily).sort_index()
    trade_days = d[d != 0]
    pnl = np.array(pnl)
    rng = np.random.default_rng(seed)
    sims = rng.choice(d.values, size=(n_boot, len(d)), replace=True).sum(axis=1)
    print(f"4 years at $0.20: {len(pnl)} trades on {len(trade_days)} days, total ${pnl.sum():.2f}, "
          f"avg ${pnl.mean():.2f} per trade ({pnl.mean() / 20:+.3f}R)")
    print(f"bootstrap of {len(d)} market days x {n_boot:,}: P(total <= 0) = {(sims <= 0).mean():.1%}; "
          f"5th-95th percentile ${np.percentile(sims, 5):.0f} to ${np.percentile(sims, 95):.0f}")
    srt = np.sort(pnl)[::-1]
    print(f"trade shape: win rate {(pnl > 0).mean():.0%}; median trade ${np.median(pnl):.2f}; "
          f"best 10 trades ${srt[:10].sum():.0f}; total without best 10 ${srt[10:].sum():.0f}; "
          f"without best 20 ${srt[20:].sum():.0f}")
    r = pnl / 20
    bins = [-10, -1.01, -0.5, 0, 0.5, 1, 2, 3, 100]
    print("R distribution:", pd.Series(pd.cut(r, bins)).value_counts().sort_index().to_dict())
    wd = pd.to_datetime(trade_days.index * 86_400_000, unit="ms").day_name()
    print("by weekday ($):", trade_days.groupby(wd).sum().round(0).reindex(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]).to_dict())
    eq = d.cumsum()
    print(f"max drawdown ${float((eq - eq.cummax()).min()):.0f}; longest stretch without a new high: "
          f"{int(max(np.diff(np.flatnonzero(np.r_[True, eq.values[1:] >= np.maximum.accumulate(eq.values)[:-1], True]))))} market days")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"placebo": placebo, "bootstrap": bootstrap}[sys.argv[1]]()
