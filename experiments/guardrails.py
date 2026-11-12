"""Drawdown guardrails for the compounding account: do pause and step-down rules help or just cost profit?

    .venv/bin/python guardrails.py

Uses the $20-stop trade list from portfolio_growth.py (no new backtests; a skipped or resized trade does not change
the trades after it). $1,000 account, risk 3% and 5% of the balance per trade. Guardrails:
  month stop N R     no more trades that calendar month once the month has lost N x the risk unit (N = 3, 4, 6)
  week stop N R      the same per calendar week (N = 2, 3)
  streak N           after N losing trades in a row, half risk until the next winning trade (N = 3, 4)
  brake X%           half risk while the balance is X% or more below its high (X = 10, 15, 20)
(R = a trade's result / the $20 stop, so a full stop-out is -1 R.)

Rule fixed before running: a guardrail is adopted only if, on 2,000 five-year paths drawn from 2018-2023 months,
it lowers the share of paths that fall 30% from a high AND keeps the median final equity within 2% of having no
guardrail, AND on the real 2024-2026 trades from $1,000 it ends within 5% of having no guardrail.
"""
import numpy as np
import pandas as pd

from common import ROOT
from portfolio_growth import BUILD, CHECK, HORIZON_MONTHS, OUT, PATHS, RUIN, SEED, START

STOP = 20.0
RISKS = (3.0, 5.0)
GUARDS = {"none": {}, **{f"month stop {n} R": {"month": n} for n in (3, 4, 6)},
          **{f"week stop {n} R": {"week": n} for n in (2, 3)},
          **{f"streak {n}": {"streak": n} for n in (3, 4)},
          **{f"brake {x}%": {"brake": x / 100} for x in (10, 15, 20)}}


def simulate(pnl, month, week, risk, month_stop=None, week_stop=None, streak=None, brake=None, start=START):
    """pnl/month/week: (paths, trades); NaN pnl = padding. Returns final equity, worst drawdown, trades skipped."""
    n, m = pnl.shape
    eq = np.full(n, start); peak = eq.copy(); dd = np.zeros(n); dead = np.zeros(n, bool)
    cur_m = np.full(n, -1); m_loss = np.zeros(n); cur_w = np.full(n, -1); w_loss = np.zeros(n)
    run = np.zeros(n); skipped = np.zeros(n)
    for j in range(m):
        x = pnl[:, j]
        live = ~np.isnan(x) & ~dead
        x = np.nan_to_num(x)
        new_m = month[:, j] != cur_m; m_loss = np.where(new_m, 0.0, m_loss); cur_m = month[:, j]
        new_w = week[:, j] != cur_w; w_loss = np.where(new_w, 0.0, w_loss); cur_w = week[:, j]
        take = live.copy()
        if month_stop is not None:
            take &= m_loss > -month_stop + 1e-9
        if week_stop is not None:
            take &= w_loss > -week_stop + 1e-9
        skipped += live & ~take
        r = np.full(n, risk / 100.0)
        if streak is not None:
            r = np.where(run >= streak, r * 0.5, r)
        if brake is not None:
            r = np.where(eq <= peak * (1 - brake), r * 0.5, r)
        lots = np.maximum(np.floor(eq * r / (STOP * 100.0) / 0.01 + 1e-9) * 0.01, 0.01)
        eq = np.where(take, eq + x * lots * 100.0, eq)
        R = x / STOP
        m_loss = np.where(take, m_loss + R, m_loss); w_loss = np.where(take, w_loss + R, w_loss)
        run = np.where(take, np.where(x > 0, 0.0, run + 1), run)
        dead |= eq < RUIN
        peak = np.maximum(peak, eq)
        dd = np.maximum(dd, 1 - eq / peak)
    return eq, dd, skipped


def arrays(d: pd.DataFrame):
    t = pd.to_datetime(d.entry_time, utc=True, format="ISO8601")
    month = (t.dt.year * 12 + t.dt.month).to_numpy()
    week = ((t.dt.as_unit("s").astype("int64") // 86400 + 3) // 7).to_numpy()
    return d.net_pnl.to_numpy(), month, week


def boot(d: pd.DataFrame, rng):
    """Five-year paths of whole months; each drawn month gets a fresh month number and its own week numbers."""
    pnl, month, week = arrays(d)
    keys = np.unique(month)
    idx = {k: np.flatnonzero(month == k) for k in keys}
    draw = rng.integers(0, len(keys), size=(PATHS, HORIZON_MONTHS))
    seqs = []
    for row in draw:
        p = np.concatenate([pnl[idx[keys[i]]] for i in row])
        mo = np.concatenate([np.full(len(idx[keys[i]]), k) for k, i in enumerate(row)])
        wk = np.concatenate([week[idx[keys[i]]] + 100_000 * k for k, i in enumerate(row)])
        seqs.append((p, mo, wk))
    L = max(len(p) for p, _, _ in seqs)
    P, M, W = np.full((PATHS, L), np.nan), np.full((PATHS, L), -1), np.full((PATHS, L), -1)
    for i, (p, mo, wk) in enumerate(seqs):
        P[i, :len(p)] = p; M[i, :len(p)] = mo; W[i, :len(p)] = wk
    return P, M, W


def main():
    pd.set_option("display.width", 250)
    df = pd.read_csv(OUT / "trades.csv.gz")
    d = df[df.stop == "stop $20"].sort_values("exit_time").reset_index(drop=True)
    b, c = d[d.year.isin(BUILD)], d[d.year.isin(CHECK)]
    one = lambda x: tuple(a[None, :] for a in arrays(x))
    B = boot(b, np.random.default_rng(SEED)); C = one(c); F = one(d)
    for risk in RISKS:
        rows = []
        for name, g in GUARDS.items():
            kw = {"month_stop": g.get("month"), "week_stop": g.get("week"), "streak": g.get("streak"), "brake": g.get("brake")}
            fe, dd, sk = simulate(*B, risk, **kw)
            ce, cd, _ = simulate(*C, risk, **kw)
            fu, fd, fs = simulate(*F, risk, **kw)
            rows.append({"guardrail": name, "boot median": round(float(np.median(fe))), "boot 5%": round(float(np.percentile(fe, 5))),
                         "boot dd>=30%": round(100 * float((dd >= 0.3).mean()), 1), "boot dd>=50%": round(100 * float((dd >= 0.5).mean()), 1),
                         "boot dd median%": round(100 * float(np.median(dd))),
                         "check 24-26": round(float(ce[0])), "check dd%": round(100 * float(cd[0])),
                         "full 18-26": round(float(fu[0])), "full dd%": round(100 * float(fd[0])), "trades skipped (full)": int(fs[0])})
        t = pd.DataFrame(rows).set_index("guardrail")
        base = t.loc["none"]
        t["adopt"] = [(n != "none") and (r["boot dd>=30%"] < base["boot dd>=30%"])
                      and (r["boot median"] >= 0.98 * base["boot median"]) and (r["check 24-26"] >= 0.95 * base["check 24-26"])
                      for n, r in t.iterrows()]
        print(f"== $1,000 account, risk {risk:g}% per trade, $20 stop ==\n", t.to_string(), "\n")


if __name__ == "__main__":
    main()
