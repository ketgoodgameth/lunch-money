"""Patterns in a real trade history: which kinds of trades lose, and how they line up with the reaction-zone strategy.

    .venv/bin/python trade_log.py PATH_TO_CSV            # first: tags each trade with the market state at entry
    .venv/bin/python trade_history_patterns.py PATH_TO_CSV

Every pattern is shown for the whole history and for each half (split at the middle date), so a pattern that only
shows up in one half is easy to spot. "per 0.01" is profit scaled to 0.01 lots ($/oz), so trade size does not hide
skill. Strategy = reaction zone, TP $10 intraday (results/high_winrate). Output is git-ignored (account data).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "trade_log"
STRAT = ROOT / "results" / "high_winrate" / "tp10_intraday_2025_2026.csv"


def features(path):
    raw = pd.read_csv(path)
    raw = raw[raw.symbol.str.upper().str.startswith("XAU")].copy()
    e = pd.read_csv(OUT / "trades_enriched.csv")
    e["open_time"] = pd.to_datetime(e.open_time, utc=True, format="ISO8601")
    e["close_time"] = pd.to_datetime(e.close_time, utc=True, format="ISO8601")
    raw["open_time"] = pd.to_datetime(raw.opening_time_utc, utc=True)
    raw["side"] = np.where(raw.type == "buy", 1, -1)
    raw["open_price"] = raw.opening_price
    # attach ticket / tp / close reason to the enriched rows: same open time, side, lots and price (n-th of a kind)
    for x in (e, raw):
        x["k_t"] = x.open_time.dt.strftime("%Y%m%d%H%M%S")
        x["k_p"] = x.open_price.round(3)
        x["k_l"] = x.lots.round(2)
        x["k_n"] = x.groupby(["k_t", "side", "k_l", "k_p"]).cumcount()
    key = ["k_t", "side", "k_l", "k_p", "k_n"]
    e = e.merge(raw[key + ["ticket", "take_profit", "close_reason"]], on=key, how="left")
    if e.ticket.isna().any():
        raise SystemExit(f"{e.ticket.isna().sum()} trades did not match the original file")
    e = e.sort_values(["open_time", "ticket"]).reset_index(drop=True)
    d = e
    d["per001"] = d.profit / (d.lots / 0.01)
    d["win"] = d.profit > 0
    d["day"] = d.open_time.dt.strftime("%Y-%m-%d")
    d["hour"] = d.open_time.dt.hour
    d["session"] = pd.cut(d.hour, [-1, 6, 11, 16, 23], labels=["Asia 00-07", "London 07-12", "New York 12-17",
                                                              "Late 17-24"])
    d["weekday"] = d.open_time.dt.day_name().str[:3]
    d["side_s"] = np.where(d.side > 0, "BUY", "SELL")
    d["hold_s"] = (d.close_time - d.open_time).dt.total_seconds()
    d["lots_s"] = d.lots.map(lambda x: f"{x:g}")
    d["tp_set"] = np.where(d.take_profit.notna(), "TP set", "no TP")
    o, c = d.open_time.values, d.close_time.values
    # positions of yours already open when this one opened (stacking), and same side
    open_now = [((o[:i] <= o[i]) & (c[:i] > o[i])) for i in range(len(d))]
    d["open_before"] = [int(m.sum()) for m in open_now]
    d["same_side_open"] = [int((m & (d.side.values[:i] == d.side.values[i])).sum()) for i, m in enumerate(open_now)]
    d["opened_same_minute"] = d.groupby([d.open_time.dt.floor("min"), "side"]).ticket.transform("size")
    # what had happened before this entry (only trades already closed)
    prev_res, since, day_pnl, nth, prev_lots = [], [], [], [], []
    cs = d.sort_values("close_time")
    ct, cp, cday = cs.close_time.values, cs.profit.values, cs.day.values
    for i, r in d.iterrows():
        k = np.searchsorted(ct, np.datetime64(r.open_time.tz_convert(None)), "right")
        prev_res.append(cp[k - 1] if k else np.nan)
        since.append((r.open_time.tz_convert(None) - pd.Timestamp(ct[k - 1])).total_seconds() / 60 if k else np.nan)
        day_pnl.append(cp[:k][cday[:k] == r.day].sum())
        prev_lots.append(d.lots[i - 1] if i else np.nan)
    d["nth_today"] = d.groupby("day").cumcount() + 1
    d["prev_result"] = pd.cut(pd.Series(prev_res), [-1e9, -10, 0, 1e9], labels=["after a loss < -$10",
                                                                                 "after a small loss", "after a win"])
    d["min_since_close"] = pd.cut(pd.Series(since), [-1, 1, 5, 30, 1e9], labels=["< 1 min", "1-5 min", "5-30 min",
                                                                              "> 30 min"])
    d["day_so_far"] = pd.cut(pd.Series(day_pnl), [-1e9, -20, -0.001, 1e9], labels=["day < -$20", "day -$20..0",
                                                                                   "day >= 0"])
    d["nth_bucket"] = pd.cut(d.nth_today, [0, 3, 10, 20, 1e9], labels=["1-3", "4-10", "11-20", "21+"])
    d["lots_change"] = np.select([d.lots > pd.Series(prev_lots), d.lots < pd.Series(prev_lots)], ["bigger", "smaller"],
                                 "same")
    d["stacking"] = pd.cut(d.same_side_open, [-1, 0, 2, 1e9], labels=["alone", "1-2 already open", "3+ already open"])
    s = d.side
    d["h1_trend"] = np.where(d.h1_supertrend == s, "with 1H trend", np.where(d.h1_supertrend == 0, "n/a",
                                                                           "against 1H trend"))
    d["h1_macd"] = np.where(np.sign(d.h1_macd_hist) == s, "1H MACD agrees", "1H MACD against")
    d["m5_trend"] = np.where(d.m5_supertrend == s, "with 5m trend", "against 5m trend")
    d["m5_macd"] = np.where(np.sign(d.m5_macd_hist) == s, "5m MACD agrees", "5m MACD against")
    d["rsi15"] = pd.cut(np.where(s > 0, d.m15_rsi, 100 - d.m15_rsi), [-1, 30, 45, 55, 70, 101],
                        labels=["RSI stretched your way (<30)", "30-45", "45-55", "55-70",
                                "chasing (RSI >70 in your direction)"])
    d["swept"] = np.where(np.where(s < 0, d.m15_swept_high, d.m15_swept_low) == True, "15m swept a level", "no sweep")
    d["half"] = np.where(d.open_time < d.open_time.min() + (d.open_time.max() - d.open_time.min()) / 2, "H1", "H2")
    return d


def strategy_overlap(d):
    st = pd.read_csv(STRAT)
    st["entry"] = pd.to_datetime(st.entry_time, utc=True, format="ISO8601")
    st["exit"] = pd.to_datetime(st.exit_time, utc=True, format="ISO8601")
    st["sd"] = np.where(st.side == "BUY", 1, -1)
    st["day"] = st.entry.dt.strftime("%Y-%m-%d")
    pos = []
    for _, r in d.iterrows():
        m = st[(st.entry <= r.open_time) & (st.exit > r.open_time)]
        pos.append("strategy not in a trade" if m.empty else
                   "same side as strategy" if m.sd.iloc[0] == r.side else "against the strategy")
    d["vs_strategy_now"] = pos
    day_side = st.groupby("day").sd.first()
    d["vs_strategy_day"] = [("no strategy trade that day" if r.day not in day_side.index else
                             "with the strategy's side that day" if day_side[r.day] == r.side else
                             "against the strategy's side that day") for _, r in d.iterrows()]
    # no look-ahead: only trades taken after the strategy's entry that day can know its side
    first = st.groupby("day").agg(t=("entry", "min"), sd=("sd", "first"))
    sig = []
    for _, r in d.iterrows():
        if r.day not in first.index:
            sig.append("no strategy trade that day")
        elif r.open_time < first.t[r.day]:
            sig.append("before the strategy's entry")
        else:
            sig.append("after its entry, same side" if first.sd[r.day] == r.side else "after its entry, other side")
    d["vs_strategy_signal"] = sig
    late = d.open_time > st.exit.max()
    d.loc[late, ["vs_strategy_now", "vs_strategy_day", "vs_strategy_signal"]] = "after strategy data ends"
    return d, st


def table(d, col):
    g = d.groupby(col, observed=True)
    t = pd.DataFrame({"trades": g.size(), "win%": (100 * g.win.mean()).round(0), "net $": g.profit.sum().round(0),
                      "per 0.01": g.per001.mean().round(2), "losses < -$20": g.profit.apply(lambda x: (x < -20).sum()),
                      "$ in them": g.profit.apply(lambda x: x[x < -20].sum()).round(0)})
    for h in ("H1", "H2"):
        t[f"net {h}"] = d[d.half == h].groupby(col, observed=True).profit.sum().round(0)
        t[f"per 0.01 {h}"] = d[d.half == h].groupby(col, observed=True).per001.mean().round(2)
    return t


def main(path):
    d = features(path)
    d, st = strategy_overlap(d)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    mid = d.open_time.min() + (d.open_time.max() - d.open_time.min()) / 2
    print(f"{len(d)} XAU trades {d.open_time.min():%Y-%m-%d} to {d.open_time.max():%Y-%m-%d}; halves split at "
          f"{mid:%Y-%m-%d}. Net ${d.profit.sum():.0f}, win {d.win.mean():.1%}, per 0.01 lot ${d.per001.mean():.2f}")
    big = d[d.profit < -20]
    print(f"losses below -$20: {len(big)} trades, ${big.profit.sum():.0f}; everything else: ${d.profit[d.profit >= -20].sum():.0f}\n")
    for col in ("session", "hour", "weekday", "side_s", "lots_s", "lots_change", "tp_set", "stacking",
                "opened_same_minute", "prev_result", "min_since_close", "day_so_far", "nth_bucket", "h1_trend",
                "h1_macd", "m5_trend", "m5_macd", "rsi15", "swept", "vs_strategy_now", "vs_strategy_day", "vs_strategy_signal"):
        print(f"== {col}\n{table(d, col).to_string()}\n")
    big_losses(d)
    filters(d)
    d.to_csv(OUT / "trades_patterns.csv", index=False)


def big_losses(d):
    b, rest = d[d.profit < -20], d[d.profit >= -20]
    print("== the losses below -$20 compared with all other trades")
    rows = {"trades": (len(b), len(rest)),
            "median hold": (f"{b.hold_s.median() / 60:.0f} min", f"{rest.hold_s.median() / 60:.1f} min"),
            "held over 30 min": (f"{(b.hold_s > 1800).mean():.0%}", f"{(rest.hold_s > 1800).mean():.0%}"),
            "TP set": (f"{b.take_profit.notna().mean():.0%}", f"{rest.take_profit.notna().mean():.0%}"),
            "closed by stop-out": (f"{(b.close_reason == 'so').mean():.0%}", f"{(rest.close_reason == 'so').mean():.0%}"),
            "lots 0.05+": (f"{(b.lots >= 0.05).mean():.0%}", f"{(rest.lots >= 0.05).mean():.0%}"),
            "median worst move $/oz": (f"{b.mae_per_oz.median():.1f}", f"{rest.mae_per_oz.median():.2f}"),
            "sells": (f"{(b.side < 0).mean():.0%}", f"{(rest.side < 0).mean():.0%}")}
    print(pd.DataFrame(rows, index=["losses < -$20", "all others"]).T.to_string(), "\n")
    hb = pd.cut(d.hold_s, [-1, 60, 300, 1800, 3600 * 4, 1e9], labels=["< 1 min", "1-5 min", "5-30 min", "30 min-4 h", "> 4 h"])
    print("== by hold time (an outcome, not an entry filter)\n", table(d.assign(hold=hb), "hold").to_string(), "\n")
    print("== TP set x hold time\n", d.assign(hold=hb).pivot_table(index="hold", columns="tp_set", values="profit",
                                                                  aggfunc=["count", "sum"], observed=True).round(0).to_string(), "\n")


def filters(d):
    """Skip rules a trader (or a filter) could apply at entry; net of what is left, per half."""
    rules = {
        "against the strategy's side, after its entry": d.vs_strategy_signal == "after its entry, other side",
        "day already down $0-20": d.day_so_far == "day -$20..0",
        "right after a small loss": d.prev_result == "after a small loss",
        "chasing (15m RSI > 70 your way)": d.rsi15 == "chasing (RSI >70 in your direction)",
        "Wednesday": d.weekday == "Wed",
        "within 1 min of the last close": d.min_since_close == "< 1 min",
    }
    rows = []
    for name, m in list(rules.items()) + [("all of the above", np.logical_or.reduce(list(rules.values())))]:
        keep = d[~m]
        rows.append({"skip": name, "skipped": int(m.sum()), "skipped net": round(d.profit[m].sum()),
                     "kept net": round(keep.profit.sum()), "kept H1": round(keep.profit[keep.half == "H1"].sum()),
                     "kept H2": round(keep.profit[keep.half == "H2"].sum()),
                     "skipped per 0.01 H1": round(d.per001[m & (d.half == "H1")].mean(), 2),
                     "skipped per 0.01 H2": round(d.per001[m & (d.half == "H2")].mean(), 2)})
    base = {"skip": "nothing (actual)", "skipped": 0, "skipped net": 0, "kept net": round(d.profit.sum()),
            "kept H1": round(d.profit[d.half == "H1"].sum()), "kept H2": round(d.profit[d.half == "H2"].sum())}
    print("== skip rules\n", pd.DataFrame([base] + rows).set_index("skip").to_string(), "\n")


if __name__ == "__main__":
    main(sys.argv[1])
