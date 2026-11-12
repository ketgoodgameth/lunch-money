"""Shared runner: variants x cost models x quarters for one strategy file and one year of ticks."""
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

TICKS = ROOT / "ticks" / "dukascopy"
YEAR_FILES = {
    2015: "download/xauusd-tick-2015-01-01-2016-01-01.csv",
    2016: "download/xauusd-tick-2016-01-01-2017-01-01.csv",
    2017: "download/xauusd-tick-2017-01-01-2018-01-01.csv",
    2018: "download/xauusd-tick-2018-01-01-2019-01-01.csv",
    2019: "download/xauusd-tick-2019-01-01-2020-01-01.csv",
    2020: "download/xauusd-tick-2020-01-01-2021-01-01.csv",
    2021: "download/xauusd-tick-2021-01-01-2022-01-01.csv",
    2022: "download/xauusd-tick-2022-01-01-2023-01-01.csv",
    2023: "xauusd-tick-2023-01-01-2024-01-01.csv",
    2024: "xauusd-tick-2024-01-01-2025-01-01.csv",
    2025: "xauusd-tick-2025-01-01-2025-12-31.csv",
    2026: "download/xauusd-tick-2026-01-01-2026-11-01.csv",
}
COSTS = {
    "zero cost": {"zero_spread": True, "commission_per_lot": 0.0},
    "Standard $0.20": {"fixed_spread": 0.20, "commission_per_lot": 0.0},
    "Raw A $0.05+$3.5/side": {"fixed_spread": 0.05, "commission_per_lot": 3.5},
}
RULES = {"risk_per_trade": 10.0, "max_daily_loss": 20.0, "max_trades": 2, "day_start_hour": 0.0,
         "stop_after_wins": 1, "stop_after_losses": 0}
BROKER = {"commission_per_lot": 0.0, "slippage": 0.0, "latency_ms": 100, "leverage": float("inf"),
          "daily_account": 20.0}


def load_candidate(name: str = "reaction_zone_growth") -> dict:
    """A saved version from results/candidates: {"file", "params", "rules", "broker"} ready for an Engine run.
    reaction_zone_growth is the current version (entries, $20 stop, sizing, daily rules, cost assumptions)."""
    import json
    c = json.loads((ROOT / "results" / "candidates" / f"{name}.json").read_text())
    broker = {k: (float("inf") if v == "inf" else v) for k, v in c["broker"].items()}
    return {"file": ROOT / c["strategy_file"], "params": dict(c["params"]), "rules": dict(c["rules"]), "broker": broker}


def run_candidate(year: int, name: str = "reaction_zone_growth", **param_overrides):
    """One full-year backtest of a saved version. Returns (trades DataFrame, final balance)."""
    from xautest import BrokerConfig, Engine, SimBroker
    from xautest.cli import load_strategy
    from xautest.feeds.arrays import ArrayFeed, load_cache
    from xautest.report import trades_frame
    from xautest.risk import DailyRiskRules
    c = load_candidate(name)
    s = load_strategy(str(c["file"]))(**{**c["params"], **param_overrides})
    s.risk = DailyRiskRules(**c["rules"])
    broker = SimBroker(BrokerConfig(**c["broker"]))
    res = Engine(s, broker, ArrayFeed(load_cache(cache_for(year_file(year))))).run()
    return trades_frame(res), broker.balance


def year_file(year: int) -> Path:
    for cand in (TICKS / YEAR_FILES[year], TICKS / Path(YEAR_FILES[year]).name):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"no tick file for {year} under {TICKS}")


def cache_for(data: Path) -> Path:
    key = {"source": "csv", "data": str(data), "files": [str(data)], "fmt": "generic", "tz": None,
           "start": "None", "end": "None", "symbol": "XAUUSD", "scale": None}
    cache = cache_dir_for(ROOT / ".cache" / "ticks", key)
    if not (cache / "ok").exists():
        print(f"caching {data.name}...", flush=True)
        build_cache(CSVTickFeed(data), cache)
    return cache


def run_grid(strategy_file: str, variants: dict, year: int, costs: dict | None = None,
             window: tuple | None = None, workers: int = 8) -> pd.DataFrame:
    """Every variant x cost model, by quarter (or one custom window). Returns one row per variant x cost.
    A variant's params may include "__rules__": {...} to override the daily risk rules for that variant."""
    costs = costs or COSTS
    cache = cache_for(year_file(year))
    if window:
        windows = {"all": tuple(dt_to_ms(x) for x in window)}
    else:
        q = [datetime(year, m, 1, tzinfo=timezone.utc) for m in (1, 4, 7, 10)] + [datetime(year + 1, 1, 1, tzinfo=timezone.utc)]
        windows = {f"Q{i}": (dt_to_ms(a), dt_to_ms(b)) for i, (a, b) in enumerate(zip(q, q[1:]), 1)}
    jobs, labels, risk_of = [], [], {}
    for vname, params in variants.items():
        rules_over = params.get("__rules__")
        risk_of[vname] = (rules_over or {}).get("risk_per_trade", RULES["risk_per_trade"])
        clean = {k: v for k, v in params.items() if k != "__rules__"}
        for cname, cover in costs.items():
            for wname, w in windows.items():
                jobs.append((clean, w, cover, rules_over)); labels.append((vname, cname, wname))
    t0 = time.time()
    print(f"{year}: {len(jobs)} backtests on {workers} workers", flush=True)
    res = opt.run_jobs(jobs, cache, str(ROOT / "strategies" / strategy_file), {}, BROKER, RULES, workers,
                       progress=lambda i, n: print(f"  {i}/{n} ({time.time() - t0:.0f}s)", flush=True)
                       if i % 24 == 0 or i == n else None)
    agg = {}
    for (v, c, w), m in zip(labels, res):
        a = agg.setdefault((v, c), {"daily": {}, "pnl": [], "q": {}, "skipped": 0, "tight": 0,
                                    "long": 0.0, "short": 0.0, "long_n": 0})
        a["long"] += m.get("long_net", 0.0); a["short"] += m.get("short_net", 0.0); a["long_n"] += m.get("long_trades", 0)
        a["daily"].update(m["_daily"]); a["pnl"].extend(m["_pnl"]); a["q"][w] = m["net"]
        a["skipped"] += m.get("skipped_wide_stop", 0); a["tight"] += m.get("skipped_tight_stop", 0)
    rows = []
    for (v, c), a in agg.items():
        d = pd.Series(a["daily"]).sort_index(); p = np.array(a["pnl"]); sd = d.std(ddof=1); eq = d.cumsum()
        rows.append({"year": year, "variant": v, "costs": c, "trades": len(p), "net": round(p.sum(), 2),
                     **{k: round(x, 2) for k, x in a["q"].items()},
                     "q_positive": sum(x > 0 for x in a["q"].values()),
                     "win_rate": round((p > 0).mean() * 100, 1) if len(p) else float("nan"),
                     "avg_R": round(p.mean() / risk_of[v], 3) if len(p) else float("nan"),
                     "tstat": round(d.mean() / (sd / math.sqrt(len(d))), 2) if sd > 0 else float("nan"),
                     "max_dd": round(float((eq - eq.cummax()).min()), 2) if len(eq) else 0.0,
                     "skipped_tight": a["tight"], "skipped_wide": a["skipped"],
                     "long_trades": a["long_n"], "long_net": round(a["long"], 2), "short_net": round(a["short"], 2)})
    print(f"  done in {time.time() - t0:.0f}s", flush=True)
    return pd.DataFrame(rows)


def show(df: pd.DataFrame, costs: dict | None = None) -> None:
    costs = costs or COSTS
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 200)
    for col, title in (("net", "net P&L"), ("avg_R", "per-trade result (R)"), ("trades", "trades")):
        print(f"\n== {title} ==")
        print(df.pivot_table(index=["year", "variant"], columns="costs", values=col)[list(costs)].to_string())
