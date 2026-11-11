"""Reduce costs for 1m sweep + 1H trend: minimum stop x entry order type x account cost model.

Rules: $10 risk, stop after the first win, max 2 trades, -$20 cap, $20/day account, unlimited leverage,
100 ms latency, trail 0.5R exit. Raw-account costs are ASSUMPTIONS to check against Exness's specs.

    .venv/bin/python experiments/compare_costs.py DATA.csv YEAR [only_label]
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

BASE = {"tf": "1min", "trend": "h1_supertrend", "exit": "trail", "trail_start_r": 1.0, "trail_dist_r": 0.5}
MIN_STOPS = [0.0, 5.0, 8.0]
ENTRIES = ["market", "limit"]
COSTS = {
    "Standard $0.20": {"fixed_spread": 0.20, "commission_per_lot": 0.0},
    "Raw A $0.05+$3.5/side": {"fixed_spread": 0.05, "commission_per_lot": 3.5},
    "Raw B $0.10+$3.5/side": {"fixed_spread": 0.10, "commission_per_lot": 3.5},
    "Raw best $0.00+$3.5/side": {"zero_spread": True, "commission_per_lot": 3.5},
    "zero cost": {"zero_spread": True, "commission_per_lot": 0.0},
}
RISK = {"risk_per_trade": 10.0, "max_daily_loss": 20.0, "max_trades": 2, "day_start_hour": 0.0,
        "stop_after_wins": 1, "stop_after_losses": 0}
BROKER = {"commission_per_lot": 0.0, "slippage": 0.0, "latency_ms": 100, "leverage": float("inf"),
          "daily_account": 20.0}


def label(min_stop, entry):
    return f"min stop {'none' if not min_stop else f'${min_stop:g}'}, {entry} entry"


def main(data: str, year: int, only: str = "", workers: int = 8) -> pd.DataFrame:
    key = {"source": "csv", "data": data, "files": [data], "fmt": "generic", "tz": None, "start": "None",
           "end": "None", "symbol": "XAUUSD", "scale": None}
    cache = cache_dir_for(ROOT / ".cache" / "ticks", key)
    if not (cache / "ok").exists():
        print("caching ticks...", flush=True)
        build_cache(CSVTickFeed(data), cache)
    q = [datetime(year, m, 1, tzinfo=timezone.utc) for m in (1, 4, 7, 10)] + [datetime(year + 1, 1, 1, tzinfo=timezone.utc)]
    quarters = [(dt_to_ms(a), dt_to_ms(b)) for a, b in zip(q, q[1:])]
    jobs, labels = [], []
    for ms in MIN_STOPS:
        for en in ENTRIES:
            lab = label(ms, en)
            if only and only != lab:
                continue
            for cname, cover in COSTS.items():
                for qi, w in enumerate(quarters, 1):
                    jobs.append(({**BASE, "min_stop": ms, "entry": en}, w, cover))
                    labels.append((lab, cname, qi))
    t0 = time.time()
    print(f"{len(jobs)} backtests on {workers} workers ({year})...", flush=True)
    res = opt.run_jobs(jobs, cache, str(ROOT / "strategies" / "liquidity_sweep_exits.py"), {}, BROKER, RISK, workers,
                       progress=lambda i, n: print(f"  {i}/{n} ({time.time() - t0:.0f}s)", flush=True)
                       if i % 16 == 0 or i == n else None)
    agg = {}
    for (lab, cname, qi), m in zip(labels, res):
        a = agg.setdefault((lab, cname), {"daily": {}, "pnl": [], "q": {}, "skipped": 0})
        a["daily"].update(m["_daily"]); a["pnl"].extend(m["_pnl"]); a["q"][f"Q{qi}"] = m["net"]
        a["skipped"] += m.get("skipped_wide_stop", 0)
    rows = []
    for (lab, cname), a in agg.items():
        d = pd.Series(a["daily"]).sort_index(); p = np.array(a["pnl"]); sd = d.std(ddof=1); eq = d.cumsum()
        rows.append({"version": lab, "costs": cname, "trades": len(p), "net": round(p.sum(), 2),
                     **{k: round(v, 2) for k, v in a["q"].items()},
                     "q_positive": sum(v > 0 for v in a["q"].values()),
                     "win_rate": round((p > 0).mean() * 100, 1) if len(p) else float("nan"),
                     "avg_R": round(p.mean() / 10, 3) if len(p) else float("nan"),
                     "tstat": round(d.mean() / (sd / math.sqrt(len(d))), 2) if sd > 0 else float("nan"),
                     "max_dd": round(float((eq - eq.cummax()).min()), 2) if len(eq) else 0.0,
                     "skipped_wide_stop": a["skipped"]})
    df = pd.DataFrame(rows)
    out = ROOT / "results" / f"costs_{year}{'_' + only.replace(' ', '_').replace('$', '').replace(',', '') if only else ''}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "summary.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 60)
    print(f"\n== {year}: net P&L (rows: version, columns: account costs) ==")
    print(df.pivot(index="version", columns="costs", values="net")[list(COSTS)].to_string())
    print(f"\n== {year}: per-trade result in R ==")
    print(df.pivot(index="version", columns="costs", values="avg_R")[list(COSTS)].to_string())
    print("\n== all ==")
    print(df.sort_values(["costs", "net"], ascending=[True, False]).to_string(index=False))
    print(f"\nfinished in {time.time() - t0:.0f}s; results in {out}")
    return df


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "")
