"""New entry ideas under the settled daily rules, 2025 by quarter.

Rules for every run: $10 risk per trade, stop after the first win, max 2 trades, -$20 daily cap,
$20/day account (stop-out at $0, negative balance protection), unlimited leverage, no commission or
slippage, 100 ms latency. Spread: zero and $0.17.

    .venv/bin/python experiments/compare_entries.py ~/.aws/download/xauusd-tick-2025-01-01-2025-12-31.csv
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

TRAIL = {"exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 0.5}
RR2 = {"exit": "rr", "rr": 2.0}
CANDIDATES = [
    ("MTF spec (as written)", "liquidity_sweep_mtf.py", {}),
    ("MTF spec, no 1H MACD filter", "liquidity_sweep_mtf.py", {"h1_macd_filter": False}),
    ("sweep 5m + trail 0.5R", "liquidity_sweep_exits.py", {"tf": "5min", **TRAIL}),
    ("sweep 5m + 1:2", "liquidity_sweep_exits.py", {"tf": "5min", **RR2}),
    ("sweep 15m + trail 0.5R", "liquidity_sweep_exits.py", {"tf": "15min", **TRAIL}),
    ("sweep 15m + 1:2", "liquidity_sweep_exits.py", {"tf": "15min", **RR2}),
]
SPREADS = {"zero spread": {"zero_spread": True}, "spread $0.17": {"fixed_spread": 0.17}}
RISK = {"risk_per_trade": 10.0, "max_daily_loss": 20.0, "max_trades": 2, "day_start_hour": 0.0,
        "stop_after_wins": 1, "stop_after_losses": 0}
BROKER = {"commission_per_lot": 0.0, "slippage": 0.0, "latency_ms": 100, "leverage": float("inf"),
          "daily_account": 20.0}
# Reference from experiments/compare_rules.py (same rules): v2 sweeps on 1m candles, trail 0.5R exit.
REFERENCE = {"zero spread": -17.22, "spread $0.17": -270.87}


def main(data: str, out: Path, workers: int = 8) -> None:
    key = {"source": "csv", "data": data, "files": [data], "fmt": "generic", "tz": None, "start": "None",
           "end": "None", "symbol": "XAUUSD", "scale": None}
    cache = cache_dir_for(ROOT / ".cache" / "ticks", key)
    if not (cache / "ok").exists():
        build_cache(CSVTickFeed(data), cache)
    q = [datetime(2025, m, 1, tzinfo=timezone.utc) for m in (1, 4, 7, 10)] + [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    quarters = [(dt_to_ms(a), dt_to_ms(b)) for a, b in zip(q, q[1:])]
    t0 = time.time()
    agg = {}
    for strategy_file in dict.fromkeys(c[1] for c in CANDIDATES):
        jobs, labels = [], []
        for name, f, params in CANDIDATES:
            if f != strategy_file:
                continue
            for sname, sover in SPREADS.items():
                for qi, w in enumerate(quarters, 1):
                    jobs.append((params, w, sover))
                    labels.append((name, sname, qi))
        print(f"{strategy_file}: {len(jobs)} backtests...", flush=True)
        res = opt.run_jobs(jobs, cache, str(ROOT / "strategies" / strategy_file), {}, BROKER, RISK, workers,
                           progress=lambda i, n: print(f"  {i}/{n} ({time.time() - t0:.0f}s)", flush=True)
                           if i % 8 == 0 or i == n else None)
        for (name, sname, qi), m in zip(labels, res):
            a = agg.setdefault((name, sname), {"daily": {}, "pnl": [], "q": {}, "skipped": 0})
            a["daily"].update(m["_daily"]); a["pnl"].extend(m["_pnl"]); a["q"][f"Q{qi}"] = m["net"]
            a["skipped"] += m.get("skipped_wide_stop", 0)

    rows = []
    for (name, sname), a in agg.items():
        daily = pd.Series(a["daily"]).sort_index()
        pnl = np.array(a["pnl"])
        eq = daily.cumsum()
        sd = daily.std(ddof=1)
        traded = int((daily != 0).sum())
        rows.append({
            "entry": name, "spread": sname, "trades": len(pnl), "days_traded": traded,
            "net": round(pnl.sum(), 2), **{k: round(v, 2) for k, v in a["q"].items()},
            "q_positive": sum(v > 0 for v in a["q"].values()),
            "win_rate": round((pnl > 0).mean() * 100, 1) if len(pnl) else float("nan"),
            "avg_R": round(pnl.mean() / RISK["risk_per_trade"], 3) if len(pnl) else float("nan"),
            "tstat": round(daily.mean() / (sd / math.sqrt(len(daily))), 2) if sd > 0 else float("nan"),
            "max_dd": round(float((eq - eq.cummax()).min()), 2) if len(eq) else 0.0,
            "skipped_wide_stop": a["skipped"],
            "vs_1m_reference": round(pnl.sum() - REFERENCE[sname], 2),
        })
        a["eq"] = eq
    summ = pd.DataFrame(rows).sort_values(["spread", "net"], ascending=[True, False])
    out.mkdir(parents=True, exist_ok=True)
    summ.to_csv(out / "summary.csv", index=False)
    pd.set_option("display.width", 260); pd.set_option("display.max_columns", 30)
    print("\nreference (v2 sweeps on 1m candles, trail 0.5R, same rules):",
          ", ".join(f"{k} {v:+.2f}" for k, v in REFERENCE.items()))
    print(summ.to_string(index=False))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), sharey=True)
    for ax, sname in zip(axes, SPREADS):
        for name, _, _ in CANDIDATES:
            eq = agg[(name, sname)]["eq"]
            if len(eq):
                ax.step(pd.to_datetime(eq.index * 86_400_000, unit="ms"), eq.values, where="post",
                        label=f"{name} ({eq.iloc[-1]:+.0f})", lw=1.2)
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_title(f"2025, {sname}: new entries (stop after 1st win, $10 risk)")
        ax.grid(alpha=0.25)
        ax.legend(frameon=False, fontsize=8)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out / "entries_equity.png", dpi=110)
    print(f"\nfinished in {time.time() - t0:.0f}s; results in {out}")


if __name__ == "__main__":
    main(sys.argv[1], ROOT / "results" / "entries_2025")
