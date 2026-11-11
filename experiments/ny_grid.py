"""Robustness map + walk-forward for the New York breakout (2022-2026, $0.20, quarter by quarter).

    .venv/bin/python ny_grid.py run        # 48 settings x 5 years x 4 quarters (long)
    .venv/bin/python ny_grid.py analyse    # heatmaps + walk-forward from the saved results
"""
import json
import sys

import numpy as np
import pandas as pd

from common import ROOT, run_grid

STARTS = ["12:30", "12:45", "13:00", "13:15", "13:30", "13:45", "14:00", "14:30"]
FRACS = [0.3, 0.4, 0.5, 0.6, 0.75, 1.0]
YEARS = (2022, 2023, 2024, 2025, 2026)
COSTS = {"$0.20": {"fixed_spread": 0.20, "slippage": 0.0}}
OUT = ROOT / "results" / "ny_grid"
CAND = ("13:30", 0.5)


def name(start, frac):
    return f"{start} | {frac:g}"


def run():
    base = json.loads((ROOT / "results" / "candidates" / "ny_breakout_half_stop.json").read_text())["params"]
    variants = {name(s, f): {**base, "range_start": s, "stop_frac": f} for s in STARTS for f in FRACS}
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for y in YEARS:
        frames.append(run_grid("opening_range.py", variants, y, costs=COSTS))
        pd.concat(frames).to_csv(OUT / "grid.csv", index=False)


def analyse():
    df = pd.read_csv(OUT / "grid.csv")
    df[["start", "frac"]] = df.variant.str.split(" \\| ", expand=True)
    df["frac"] = df.frac.astype(float)
    tot = df.groupby(["start", "frac"]).net.sum().unstack()[FRACS].loc[STARTS]
    pos = df.assign(p=df.net > 0).groupby(["start", "frac"]).p.sum().unstack()[FRACS].loc[STARTS]
    pd.set_option("display.width", 200)
    print("== 5-year net (rows: range start UTC, columns: stop position) ==")
    print(tot.round(0).to_string())
    print("\n== profitable years out of 5 ==")
    print(pos.to_string())
    print(f"\nsettings with a positive 5-year total: {(tot > 0).sum().sum()} of {tot.size}; "
          f"profitable in all 5 years: {(pos == 5).sum().sum()}")

    # walk-forward: each quarter, choose the setting with the best trailing-4-quarter net; trade it next quarter
    q = df.melt(id_vars=["year", "variant"], value_vars=["Q1", "Q2", "Q3", "Q4"], var_name="q", value_name="net_q")
    q["t"] = q.year * 4 + q.q.str[1].astype(int) - 1
    piv = q.pivot_table(index="t", columns="variant", values="net_q").sort_index()
    piv = piv.loc[piv.index[piv.index <= 2026 * 4 + 3]]
    wf, fixed, picks = [], [], []
    for i in range(4, len(piv)):
        trail = piv.iloc[i - 4:i].sum()
        pick = trail.idxmax()
        wf.append(piv.iloc[i][pick]); fixed.append(piv.iloc[i][name(*CAND)]); picks.append(pick)
    wf, fixed = np.array(wf), np.array(fixed)
    labels = [f"{t // 4} Q{t % 4 + 1}" for t in piv.index[4:]]
    print("\n== walk-forward (re-pick each quarter from the last 4 quarters) vs fixed 13:30 | 0.5 ==")
    print(pd.DataFrame({"quarter": labels, "picked": picks, "walk-forward": wf.round(0), "fixed": fixed.round(0)}).to_string(index=False))
    print(f"\ntotal 2023-2026: walk-forward ${wf.sum():.0f}, fixed candidate ${fixed.sum():.0f}; "
          f"quarters positive: walk-forward {(wf > 0).sum()}/{len(wf)}, fixed {(fixed > 0).sum()}/{len(fixed)}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, data, title, cmap, fmt in ((axes[0], tot, "5-year net at $0.20 (USD)", "RdYlGn", "{:.0f}"),
                                       (axes[1], pos, "profitable years (of 5)", "Greens", "{:.0f}")):
        lim = np.nanmax(np.abs(data.values)) if cmap == "RdYlGn" else None
        im = ax.imshow(data.values, cmap=cmap, vmin=-lim if lim else 0, vmax=lim if lim else 5, aspect="auto")
        ax.set_xticks(range(len(FRACS)), [f"{f:g}" for f in FRACS]); ax.set_yticks(range(len(STARTS)), STARTS)
        ax.set_xlabel("stop position (fraction of range)"); ax.set_ylabel("range start (UTC)"); ax.set_title(title)
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                ax.text(j, i, fmt.format(data.values[i, j]), ha="center", va="center", fontsize=8)
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout(); fig.savefig(OUT / "heatmap.png", dpi=110)
    print(f"\nheatmap: {OUT / 'heatmap.png'}")


if __name__ == "__main__":
    {"run": run, "analyse": analyse}[sys.argv[1]]()
