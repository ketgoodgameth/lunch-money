"""Performance metrics and result files (trades.csv, equity.csv, summary.json, equity.png)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .models import RunResult, ms_to_dt


def trades_frame(result: RunResult) -> pd.DataFrame:
    return pd.DataFrame([t.as_dict() for t in result.trades])


def equity_frame(result: RunResult) -> pd.DataFrame:
    if not result.equity:
        return pd.DataFrame(columns=["time", "balance", "equity"])
    df = pd.DataFrame({"ts": [e.ts for e in result.equity], "balance": [e.balance for e in result.equity],
                       "equity": [e.equity for e in result.equity]})
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["time", "balance", "equity"]]


def summarize(result: RunResult) -> dict:
    init = result.initial_balance
    tr = trades_frame(result)
    eq = equity_frame(result)
    s: dict = {
        "period_start": ms_to_dt(result.first_ts).isoformat() if result.first_ts else None,
        "period_end": ms_to_dt(result.last_ts).isoformat() if result.last_ts else None,
        "ticks": result.ticks_processed,
        "stopped": result.stopped_reason,
        "initial_balance": init,
        "trades": len(tr),
        "rejected_orders": result.rejections,
    }
    net = float(tr["net_pnl"].sum()) if len(tr) else 0.0
    final_equity = float(eq["equity"].iloc[-1]) if len(eq) else init
    s.update(net_profit=round(net, 2), final_equity=round(final_equity, 2),
             return_pct=round(100 * (final_equity / init - 1), 3))
    if len(eq):
        peak = eq["equity"].cummax()
        dd = eq["equity"] - peak
        s["max_drawdown"] = round(float(dd.min()), 2)
        s["max_drawdown_pct"] = round(float((dd / peak).min() * 100), 3)
        daily = eq.set_index("time")["equity"].resample("1D").last().dropna()
        rets = daily.pct_change().dropna()
        rets = rets[rets != 0] if len(rets) > 5 else rets  # drop flat weekend/holiday days
        if len(rets) >= 2 and rets.std() > 0:
            s["sharpe_daily_annualized"] = round(float(rets.mean() / rets.std() * math.sqrt(252)), 3)
            downside = rets[rets < 0]
            if len(downside) >= 2 and downside.std() > 0:
                s["sortino"] = round(float(rets.mean() / downside.std() * math.sqrt(252)), 3)
    if len(tr):
        wins, losses = tr[tr["net_pnl"] > 0], tr[tr["net_pnl"] <= 0]
        gross_win, gross_loss = wins["net_pnl"].sum(), -losses["net_pnl"].sum()
        s.update(
            win_rate_pct=round(100 * len(wins) / len(tr), 2),
            profit_factor=round(float(gross_win / gross_loss), 3) if gross_loss > 0 else None,
            avg_trade=round(float(tr["net_pnl"].mean()), 2),
            avg_win=round(float(wins["net_pnl"].mean()), 2) if len(wins) else 0.0,
            avg_loss=round(float(losses["net_pnl"].mean()), 2) if len(losses) else 0.0,
            largest_win=round(float(tr["net_pnl"].max()), 2),
            largest_loss=round(float(tr["net_pnl"].min()), 2),
            long_trades=int((tr["side"] == "BUY").sum()),
            short_trades=int((tr["side"] == "SELL").sum()),
            avg_duration_min=round(float(tr["duration_s"].mean() / 60), 2),
            total_commission=round(float(tr["commission"].sum()), 2),
            total_swap=round(float(tr["swap"].sum()), 2),
            exits={k: int(v) for k, v in tr["reason"].value_counts().items()},
            max_consecutive_losses=int(_max_run(tr["net_pnl"].to_numpy() <= 0)),
        )
    return s


def _max_run(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def format_summary(s: dict, title: str = "") -> str:
    lines = [f"== {title} ==" if title else "== results =="]
    width = max(len(k) for k in s)
    for k, v in s.items():
        lines.append(f"  {k:<{width}}  {v}")
    return "\n".join(lines)


def write_report(result: RunResult, out_dir: str | Path, title: str = "", meta: dict | None = None,
                 plot: bool = True) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = summarize(result)
    trades_frame(result).to_csv(out / "trades.csv", index=False)
    eq = equity_frame(result)
    eq.to_csv(out / "equity.csv", index=False)
    (out / "summary.json").write_text(json.dumps({"title": title, **(meta or {}), "results": s}, indent=2,
                                                  default=str))
    if plot and len(eq) > 1:
        _plot(eq, out / "equity.png", title)
    return s


def _plot(eq: pd.DataFrame, path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    a1.plot(eq["time"], eq["equity"], drawstyle="steps-post", lw=1.2, color="#1f5fa8", label="equity")
    a1.plot(eq["time"], eq["balance"], drawstyle="steps-post", lw=0.8, color="#888", label="balance")
    a1.set_ylabel("USD")
    a1.legend(loc="upper left", frameon=False)
    a1.set_title(title or "Equity")
    a1.grid(alpha=0.25)
    dd = eq["equity"] - eq["equity"].cummax()
    a2.fill_between(eq["time"], dd, 0, step="post", color="#c0392b", alpha=0.35, lw=0)
    a2.set_ylabel("drawdown")
    a2.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
