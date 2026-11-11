"""Daily stop rules x entry filters for the v2 entries with the trail-0.5R exit, 2025, by quarter.

Fixed: $20/day Exness account (stop-out at $0, negative balance protection), $10 risk per trade,
-$20 daily loss cap, unlimited leverage, no commission/slippage, 100 ms latency.

    .venv/bin/python experiments/compare_rules.py ~/.aws/download/xauusd-tick-2025-01-01-2025-12-31.csv
"""
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xautest import optimize as opt                      # noqa: E402
from xautest.feeds import CSVTickFeed                    # noqa: E402
from xautest.feeds.arrays import build_cache, cache_dir_for  # noqa: E402
from xautest.models import dt_to_ms                      # noqa: E402

EXIT = {"exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 0.5}
RULES = {
    "R1 stop after 1st win, max 2": {"max_trades": 2, "stop_after_wins": 1, "stop_after_losses": 0},
    "R2 trade until -$20": {"max_trades": 0, "stop_after_wins": 0, "stop_after_losses": 0},
    "R3 stop after 2 wins in a row": {"max_trades": 0, "stop_after_wins": 2, "stop_after_losses": 0},
    "R4 stop after 2 losses in a row": {"max_trades": 0, "stop_after_wins": 0, "stop_after_losses": 2},
    "R5 stop after 2 wins or 2 losses": {"max_trades": 0, "stop_after_wins": 2, "stop_after_losses": 2},
}
FILTERS = {
    "none": {},
    "hours 07-20": {"hours": "7-20"},
    "1H trend": {"trend": "h1_supertrend"},
    "hours + trend": {"hours": "7-20", "trend": "h1_supertrend"},
}
SPREADS = {"zero spread": {"zero_spread": True}, "spread $0.17": {"fixed_spread": 0.17}}
RISK = {"risk_per_trade": 10.0, "max_daily_loss": 20.0, "max_trades": 2, "day_start_hour": 0.0,
        "stop_after_wins": 1, "stop_after_losses": 0}
BROKER = {"commission_per_lot": 0.0, "slippage": 0.0, "latency_ms": 100, "leverage": float("inf"),
          "daily_account": 20.0}


def main(data: str, out: Path, workers: int = 8) -> None:
    key = {"source": "csv", "data": data, "files": [data], "fmt": "generic", "tz": None, "start": "None",
           "end": "None", "symbol": "XAUUSD", "scale": None}
    cache = cache_dir_for(ROOT / ".cache" / "ticks", key)
    if not (cache / "ok").exists():
        build_cache(CSVTickFeed(data), cache)
    q = [datetime(2025, m, 1, tzinfo=timezone.utc) for m in (1, 4, 7, 10)] + [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    quarters = [(dt_to_ms(a), dt_to_ms(b)) for a, b in zip(q, q[1:])]
    jobs, labels = [], []
    for sname, sover in SPREADS.items():
        for rname, rover in RULES.items():
            for fname, fparams in FILTERS.items():
                for qi, w in enumerate(quarters, 1):
                    jobs.append(({**EXIT, **fparams}, w, sover, rover))
                    labels.append((sname, rname, fname, qi))
    t0 = time.time()
    print(f"{len(jobs)} backtests on {workers} workers...", flush=True)
    res = opt.run_jobs(jobs, cache, str(ROOT / "strategies" / "liquidity_sweep_exits.py"), {}, BROKER, RISK, workers,
                       progress=lambda i, n: print(f"  {i}/{n} done ({time.time() - t0:.0f}s)", flush=True)
                       if i % 20 == 0 or i == n else None)
    rows, agg = [], {}
    for (sname, rname, fname, qi), m in zip(labels, res):
        rows.append({"spread": sname, "rule": rname, "filter": fname, "quarter": f"Q{qi}",
                     **{k: v for k, v in m.items() if not k.startswith("_")}})
        a = agg.setdefault((sname, rname, fname), {"daily": {}, "pnl": [], "q": {}})
        a["daily"].update(m["_daily"]); a["pnl"].extend(m["_pnl"]); a["q"][f"Q{qi}"] = m["net"]
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "by_quarter.csv", index=False)

    summary = []
    for (sname, rname, fname), a in agg.items():
        daily = pd.Series(a["daily"]).sort_index()
        pnl = np.array(a["pnl"])
        eq = daily.cumsum()
        sd = daily.std(ddof=1)
        traded_days = int((daily != 0).sum())
        summary.append({
            "spread": sname, "rule": rname, "filter": fname, "trades": len(pnl),
            "trades_per_traded_day": round(len(pnl) / traded_days, 2) if traded_days else 0,
            "net": round(pnl.sum(), 2), **{k: round(v, 2) for k, v in a["q"].items()},
            "q_positive": sum(v > 0 for v in a["q"].values()),
            "win_rate": round((pnl > 0).mean() * 100, 1) if len(pnl) else float("nan"),
            "avg_R": round(pnl.mean() / RISK["risk_per_trade"], 3) if len(pnl) else float("nan"),
            "tstat": round(daily.mean() / (sd / math.sqrt(len(daily))), 2) if sd > 0 else float("nan"),
            "max_dd": round(float((eq - eq.cummax()).min()), 2), "worst_day": round(daily.min(), 2),
            "best_day": round(daily.max(), 2),
        })
        a["eq"] = eq
    summ = pd.DataFrame(summary).sort_values(["spread", "net"], ascending=[True, False])
    summ.to_csv(out / "summary.csv", index=False)
    pd.set_option("display.width", 260); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 100)
    for sname in SPREADS:
        print(f"\n== {sname}: net P&L 2025 (rows: daily rule, columns: entry filter) ==")
        print(summ[summ.spread == sname].pivot(index="rule", columns="filter", values="net")[list(FILTERS)].to_string())
    print("\n== all configurations ==")
    print(summ.drop(columns=["spread"]).assign(spread=summ.spread).to_string(index=False))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), sharey=True)
    for ax, sname in zip(axes, SPREADS):
        top = summ[summ.spread == sname].head(6)
        for _, r in top.iterrows():
            eq = agg[(sname, r.rule, r["filter"])]["eq"]
            ax.step(pd.to_datetime(eq.index * 86_400_000, unit="ms"), eq.values, where="post",
                    label=f"{r.rule.split()[0]} + {r['filter']} ({r.net:+.0f})", lw=1.2)
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_title(f"2025, {sname}: 6 best rule+filter combinations")
        ax.grid(alpha=0.25)
        ax.legend(frameon=False, fontsize=8)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out / "best_equity.png", dpi=110)
    print(f"\nfinished in {time.time() - t0:.0f}s; results in {out}")


if __name__ == "__main__":
    main(sys.argv[1], ROOT / "results" / "rules_filters_2025")
