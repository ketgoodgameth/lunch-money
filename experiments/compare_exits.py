"""Compare exit methods for the v2 liquidity-sweep entries over a full year, quarter by quarter.

Fixed for every run: daily risk rules ($10 per trade, $20 daily loss cap, 2 trades, stop after a win),
unlimited leverage, no commission, no slippage, 100 ms order latency. Varied: exit method and spread.

    .venv/bin/python experiments/compare_exits.py ~/.aws/download/xauusd-tick-2025-01-01-2025-12-31.csv
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

METHODS = {
    "fixed $10 + BE (v2)": {"exit": "fixed"},
    "1:2 scalper": {"exit": "rr", "rr": 2.0},
    "1:10 sniper": {"exit": "rr", "rr": 10.0},
    "trail 1R": {"exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 1.0},
    "trail 0.5R": {"exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 0.5},
}
SPREADS = {"zero spread": {"zero_spread": True}, "spread $0.17": {"fixed_spread": 0.17}}
RISK = {"risk_per_trade": 10.0, "max_daily_loss": 20.0, "max_trades": 2, "stop_after_win": True, "day_start_hour": 0.0}
# $20 Exness account per day: stop-out at $0, negative balance protection writes off anything beyond.
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
        for mname, params in METHODS.items():
            for qi, w in enumerate(quarters, 1):
                jobs.append((params, w, sover))
                labels.append((sname, mname, qi))
    t0 = time.time()
    print(f"{len(jobs)} backtests on {workers} workers...", flush=True)
    res = opt.run_jobs(jobs, cache, str(ROOT / "strategies" / "liquidity_sweep_exits.py"), {}, BROKER, RISK, workers,
                       progress=lambda i, n: print(f"  {i}/{n} done ({time.time() - t0:.0f}s)", flush=True))
    rows, curves = [], {}
    for (sname, mname, qi), m in zip(labels, res):
        rows.append({"spread": sname, "method": mname, "quarter": f"Q{qi}", **{k: v for k, v in m.items() if not k.startswith("_")}})
        key2 = (sname, mname)
        c = curves.setdefault(key2, {"daily": {}, "pnl": []})
        c["daily"].update(m["_daily"]); c["pnl"].extend(m["_pnl"])
    per_q = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    per_q.to_csv(out / "by_quarter.csv", index=False)

    summary = []
    for (sname, mname), c in curves.items():
        daily = pd.Series(c["daily"]).sort_index()
        pnl = np.array(c["pnl"])
        eq = daily.cumsum()
        sd = daily.std(ddof=1)
        qnet = per_q[(per_q.spread == sname) & (per_q.method == mname)].set_index("quarter").net
        summary.append({
            "spread": sname, "method": mname, "trades": len(pnl), "net": round(pnl.sum(), 2),
            **{q: round(v, 2) for q, v in qnet.items()},
            "win_rate": round((pnl > 0).mean() * 100, 1), "avg_R": round(pnl.mean() / RISK["risk_per_trade"], 3),
            "sharpe": round(daily.mean() / sd * math.sqrt(252), 2) if sd > 0 else float("nan"),
            "tstat": round(daily.mean() / (sd / math.sqrt(len(daily))), 2) if sd > 0 else float("nan"),
            "max_dd": round(float((eq - eq.cummax()).min()), 2), "worst_day": round(daily.min(), 2),
            "best_trade": round(pnl.max(), 2) if len(pnl) else 0,
            "net_without_best_5": round(np.sort(pnl)[:-5].sum(), 2) if len(pnl) > 5 else float("nan"),
            "nbp_written_off": round(per_q[(per_q.spread == sname) & (per_q.method == mname)].nbp_written_off.sum(), 2),
            "avg_hold_h": round(per_q[(per_q.spread == sname) & (per_q.method == mname)].avg_hold_h.mean(), 1),
        })
        curves[(sname, mname)]["eq"] = eq
    summ = pd.DataFrame(summary)
    summ.to_csv(out / "summary.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(summ.to_string(index=False))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    for ax, sname in zip(axes, SPREADS):
        for mname in METHODS:
            eq = curves[(sname, mname)]["eq"]
            ax.step(pd.to_datetime(eq.index * 86_400_000, unit="ms"), eq.values, where="post", label=mname, lw=1.3)
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_title(f"2025, {sname}: cumulative P&L (USD)")
        ax.grid(alpha=0.25)
    axes[0].legend(frameon=False)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out / "equity_by_method.png", dpi=110)
    print(f"\nfinished in {time.time() - t0:.0f}s; results in {out}")


if __name__ == "__main__":
    main(sys.argv[1], ROOT / "results" / (sys.argv[2] if len(sys.argv) > 2 else "exits_2025_daily_account"))
