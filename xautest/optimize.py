"""Parameter search with an out-of-sample check.

Every parameter combination is backtested on a TRAIN period and on a later TEST period it was not
chosen on. Risk rules and broker costs are fixed for all runs. Reported:

  * the best combinations by train score, with their test score next to them
  * how well train rank predicts test rank across ALL combinations (Spearman correlation).
    Near zero or negative means the search is fitting noise, whatever the best train score says.
  * each parameter value's average score, to look for broad plateaus instead of one lucky peak
"""
from __future__ import annotations

import itertools
import math
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .broker import BrokerConfig, SimBroker
from .engine import Engine
from .feeds.arrays import ArrayFeed, load_cache
from .risk import DAY_MS, DailyRiskRules

OBJECTIVES = ("sharpe", "avg_day", "net", "profit_factor")
_W: dict = {}


def _init(cache_dir, strategy_spec, fixed_params, broker_cfg, risk_cfg):
    import logging
    logging.getLogger("strategy").setLevel(logging.ERROR)  # hundreds of parallel runs: keep the log readable
    from .cli import load_strategy
    _W.update(arrays=load_cache(cache_dir), cls=load_strategy(strategy_spec), fixed=fixed_params,
              broker=broker_cfg, risk=risk_cfg)


def _evaluate(job):
    params, windows = job
    return params, [evaluate(params, lo, hi) for lo, hi in windows]


def _evaluate_job(job):
    """One (params, window, broker overrides) backtest; lets one pool mix broker settings."""
    params, (lo, hi), broker_over, *rest = job
    return evaluate(params, lo, hi, broker_over, rest[0] if rest else None)


def evaluate(params: dict, start_ms: int | None, end_ms: int | None, broker_over: dict | None = None,
             risk_over: dict | None = None) -> dict:
    w = _W
    s = w["cls"](**{**w["fixed"], **params})
    if w["risk"] is not None:
        s.risk = DailyRiskRules(**{**w["risk"], **(risk_over or {})})
    feed = ArrayFeed(w["arrays"], start_ms, end_ms)
    res = Engine(s, SimBroker(BrokerConfig(**{**w["broker"], **(broker_over or {})})), feed).run()
    ts = w["arrays"]["ts"][feed.lo:feed.hi]
    m = metrics(res, market_days=np.unique(np.asarray(ts) // DAY_MS))
    if s.risk is not None:
        m["skipped_wide_stop"] = s.risk.stats["skipped_stop_too_wide"]
        m["skipped_tight_stop"] = s.risk.stats["skipped_stop_too_tight"]
    return m


def metrics(res, market_days: np.ndarray) -> dict:
    """Daily statistics count every day the market traded, including days with no trades (P&L 0).
    Trades are booked to the UTC day they were opened, like the daily risk rules."""
    trades = res.trades
    daily = pd.Series(0.0, index=market_days)
    for t in trades:
        d = t.entry_ts // DAY_MS
        daily[d] = daily.get(d, 0.0) + t.net_pnl
    pnl = np.array([t.net_pnl for t in trades])
    gross_win, gross_loss = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    eq = np.array([e.equity for e in res.equity]) if res.equity else np.array([res.initial_balance])
    peak = np.maximum.accumulate(eq)
    n, mean, sd = len(daily), daily.mean(), daily.std(ddof=1) if len(daily) > 1 else 0.0
    return {
        "trades": len(trades),
        "net": round(float(pnl.sum()), 2),
        "days": n,
        "avg_day": round(float(mean), 3),
        "sharpe": round(float(mean / sd * math.sqrt(252)), 3) if sd > 0 else float("nan"),
        "tstat": round(float(mean / (sd / math.sqrt(n))), 3) if sd > 0 else float("nan"),
        "win_rate": round(float((pnl > 0).mean() * 100), 1) if len(pnl) else float("nan"),
        "profit_factor": round(float(gross_win / gross_loss), 3) if gross_loss > 0 else float("nan"),
        "max_dd": round(float((eq - peak).min()), 2),
        "worst_day": round(float(daily.min()), 2),
        "long_trades": sum(1 for t in trades if t.side > 0),
        "long_net": round(float(sum(t.net_pnl for t in trades if t.side > 0)), 2),
        "short_net": round(float(sum(t.net_pnl for t in trades if t.side < 0)), 2),
        "nbp_written_off": round(float(sum(t.nbp_credit for t in trades)), 2),
        "avg_hold_h": round(float(np.mean([(t.exit_ts - t.entry_ts) / 3.6e6 for t in trades])), 2) if trades else 0.0,
        "_daily": {int(k): float(v) for k, v in daily.items()},
        "_pnl": pnl.tolist(),
    }


def grid(spec: dict[str, list]) -> list[dict]:
    keys = list(spec)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(spec[k] for k in keys))]


def run(combos: list[dict], windows: list[tuple[int | None, int | None]], cache_dir, strategy_spec: str,
        fixed_params: dict, broker_cfg: dict, risk_cfg: dict | None, workers: int | None = None,
        progress=None) -> list[tuple[dict, list[dict]]]:
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    jobs = [(c, windows) for c in combos]
    out = []
    with ProcessPoolExecutor(workers, initializer=_init,
                             initargs=(str(cache_dir), strategy_spec, fixed_params, broker_cfg, risk_cfg)) as pool:
        for i, r in enumerate(pool.map(_evaluate, jobs, chunksize=1), 1):
            out.append(r)
            if progress:
                progress(i, len(jobs))
    return out


def run_jobs(jobs: list, cache_dir, strategy_spec: str, fixed_params: dict, broker_cfg: dict,
             risk_cfg: dict | None, workers: int | None = None, progress=None) -> list[dict]:
    """Run (params, (start_ms, end_ms), broker_overrides) jobs in parallel; results in job order."""
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    out = []
    with ProcessPoolExecutor(workers, initializer=_init,
                             initargs=(str(cache_dir), strategy_spec, fixed_params, broker_cfg, risk_cfg)) as pool:
        for i, r in enumerate(pool.map(_evaluate_job, jobs, chunksize=1), 1):
            out.append(r)
            if progress:
                progress(i, len(jobs))
    return out


def to_frame(results, window_names: list[str]) -> pd.DataFrame:
    rows = []
    for params, mets in results:
        row = dict(params)
        for name, m in zip(window_names, mets):
            row.update({f"{name}_{k}": v for k, v in m.items() if not k.startswith("_")})
        rows.append(row)
    return pd.DataFrame(rows)


def spearman(a: pd.Series, b: pd.Series) -> float:
    ok = a.notna() & b.notna()
    if ok.sum() < 3:
        return float("nan")
    return float(a[ok].rank().corr(b[ok].rank()))
