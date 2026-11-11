"""Tick data from CSV files.

Formats:
  generic   header row with a time column (timestamp/time/ts/datetime) plus bid, ask and optional
            bid_vol, ask_vol. Time may be ISO-8601 or epoch seconds/milliseconds. This is also the
            format the live recorder writes, so recorded live sessions can be backtested.
  histdata  HistData.com ASCII ticks: ``20250102 170000123,2625.12,2625.45,0`` with no header.
            HistData timestamps are EST without daylight saving, so utc_offset_hours defaults to -5.
  mt5       MetaTrader 5 "Export ticks": tab-separated, <DATE> <TIME> <BID> <ASK> ... Rows that only
            update one side are forward-filled. MT5 uses broker server time, so set server_tz
            (usually "NY+7"; ``python -m xautest inspect`` detects it). See xautest/timezones.py.

``path`` may be a file, a directory (all *.csv / *.csv.gz / *.txt inside, sorted) or a glob.
"""
from __future__ import annotations

import glob
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from ..models import Tick, dt_to_ms
from ..timezones import parse_server_tz

CHUNK = 1_000_000
TIME_COLUMNS = ("timestamp", "time", "ts", "datetime", "date", "ts_ms")
# Alternative column names, e.g. dukascopy-node writes timestamp,askPrice,bidPrice[,askVolume,bidVolume]
ALIASES = {"bid": ("bid", "bidprice", "bid_price"), "ask": ("ask", "askprice", "ask_price"),
           "bid_vol": ("bid_vol", "bidvolume", "bid_volume"), "ask_vol": ("ask_vol", "askvolume", "ask_volume")}


def sniff(f: Path) -> tuple[str, str]:
    """Guess (encoding, separator). MT5 and Windows tools often write UTF-16 or UTF-8 with a BOM."""
    if f.suffix == ".gz":
        return "utf-8", ","
    head = f.open("rb").read(4096)
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16"
    elif head.startswith(b"\xef\xbb\xbf"):
        enc = "utf-8-sig"
    elif len(head) >= 4 and head[1:2] == b"\x00" and head[3:4] == b"\x00":
        enc = "utf-16-le"
    else:
        enc = "utf-8"
    first = head.decode(enc, errors="ignore").splitlines()[0] if head else ""
    sep = "\t" if "\t" in first else ";" if first.count(";") > first.count(",") else ","
    return enc, sep


def resolve_paths(path: str | os.PathLike) -> list[Path]:
    p = Path(path)
    if p.is_dir():
        files = [f for pat in ("*.csv", "*.csv.gz", "*.txt") for f in p.glob(pat)]
    elif p.exists():
        files = [p]
    else:
        files = [Path(f) for f in glob.glob(str(path))]
    if not files:
        raise FileNotFoundError(f"no tick files match {path}")
    return sorted(files)


def _to_ms(col: pd.Series) -> np.ndarray:
    if pd.api.types.is_numeric_dtype(col):
        v = col.to_numpy(dtype=np.float64)
        # Heuristic: epoch seconds are < 1e11 until the year 5138.
        return (v * 1000).astype(np.int64) if np.nanmedian(v) < 1e11 else v.astype(np.int64)
    return _dt_ms(pd.to_datetime(col, utc=True, format="mixed"))


_EPOCH = pd.Timestamp(0, tz="UTC")


def _dt_ms(t: pd.Series) -> np.ndarray:
    """UTC datetimes -> epoch ms, independent of pandas' datetime resolution (ns in 2.x, varies in 3.x)."""
    return ((t - _EPOCH) // pd.Timedelta(milliseconds=1)).to_numpy(np.int64)


class CSVTickFeed:
    def __init__(self, path: str | os.PathLike, fmt: str = "generic", utc_offset_hours: float | None = None,
                 start: datetime | None = None, end: datetime | None = None, server_tz: str | float | None = None):
        """``server_tz`` (NY+7, an IANA zone or a fixed offset) says what clock the file uses.
        ``utc_offset_hours`` is the older fixed-offset form of the same setting."""
        if fmt not in ("generic", "histdata", "mt5"):
            raise ValueError(f"unknown CSV format {fmt!r}")
        self.files = resolve_paths(path)
        self.fmt = fmt
        if server_tz is None:
            server_tz = utc_offset_hours if utc_offset_hours is not None else (-5.0 if fmt == "histdata" else 0.0)
        self.to_utc = parse_server_tz(server_tz)
        self.dropped = 0  # ticks inside a clock-change hour (should be none for real gold data)
        self.start_ms = dt_to_ms(start) if start else None
        self.end_ms = dt_to_ms(end) if end else None

    def __iter__(self) -> Iterator[Tick]:
        for ts, bid, ask, bv, av in self.iter_arrays():
            for t, b, a, x, y in zip(ts.tolist(), bid.tolist(), ask.tolist(), bv.tolist(), av.tolist()):
                yield Tick(t, b, a, x, y)

    def iter_arrays(self):
        """Yield (ts, bid, ask, bid_vol, ask_vol) numpy arrays per chunk: UTC, filtered, in order."""
        last_ts = None
        for f in self.files:
            for ts, bid, ask, bv, av in self._chunks(f):
                ts, ok = self.to_utc(ts)  # file clock -> UTC
                self.dropped += int((~ok).sum())
                mask = ok & np.isfinite(bid) & np.isfinite(ask)
                if self.start_ms is not None:
                    mask &= ts >= self.start_ms
                if self.end_ms is not None:
                    mask &= ts < self.end_ms
                if not mask.all():
                    ts, bid, ask, bv, av = ts[mask], bid[mask], ask[mask], bv[mask], av[mask]
                if len(ts) and last_ts is not None and ts[0] < last_ts:
                    raise ValueError(f"{f}: ticks are out of order (files overlap or are not sorted)")
                if len(ts):
                    last_ts = int(ts[-1])
                    yield ts, bid, ask, bv, av

    def _chunks(self, f: Path):
        enc, sep = sniff(f)
        if self.fmt == "generic":
            for df in pd.read_csv(f, chunksize=CHUNK, encoding=enc, sep=sep):
                cols = {c.lower().strip(): c for c in df.columns}
                for std, names in ALIASES.items():
                    found = next((cols[n] for n in names if n in cols), None)
                    if found is not None:
                        cols[std] = found
                tcol = next((cols[c] for c in TIME_COLUMNS if c in cols), None)
                if tcol is None or "bid" not in cols or "ask" not in cols:
                    raise ValueError(f"{f}: need a time column ({'/'.join(TIME_COLUMNS)}) plus bid and ask")
                n = len(df)
                yield (_to_ms(df[tcol]), df[cols["bid"]].to_numpy(np.float64), df[cols["ask"]].to_numpy(np.float64),
                       df[cols["bid_vol"]].to_numpy(np.float64) if "bid_vol" in cols else np.zeros(n),
                       df[cols["ask_vol"]].to_numpy(np.float64) if "ask_vol" in cols else np.zeros(n))
        elif self.fmt == "histdata":
            for df in pd.read_csv(f, header=None, names=["t", "bid", "ask", "vol"], chunksize=CHUNK,
                                  dtype={"t": str}, encoding=enc, sep=sep):
                t = pd.to_datetime(df["t"], format="%Y%m%d %H%M%S%f", utc=True)
                n = len(df)
                yield (_dt_ms(t), df["bid"].to_numpy(np.float64),
                       df["ask"].to_numpy(np.float64), np.zeros(n), np.zeros(n))
        else:  # mt5
            last_bid = last_ask = np.nan
            for df in pd.read_csv(f, sep=sep, chunksize=CHUNK, dtype=str, encoding=enc):
                df.columns = [c.strip().strip("<>").upper() for c in df.columns]
                stamp = df["DATE"].str.strip() + " " + df["TIME"].str.strip()
                try:
                    t = pd.to_datetime(stamp, format="%Y.%m.%d %H:%M:%S.%f", utc=True)
                except ValueError:  # some exports omit milliseconds
                    t = pd.to_datetime(stamp, format="mixed", utc=True)
                bid = pd.to_numeric(df["BID"], errors="coerce")
                ask = pd.to_numeric(df["ASK"], errors="coerce")
                bid.iloc[0] = last_bid if np.isnan(bid.iloc[0]) else bid.iloc[0]
                ask.iloc[0] = last_ask if np.isnan(ask.iloc[0]) else ask.iloc[0]
                bid, ask = bid.ffill(), ask.ffill()
                last_bid, last_ask = bid.iloc[-1], ask.iloc[-1]
                n = len(df)
                yield (_dt_ms(t), bid.to_numpy(np.float64),
                       ask.to_numpy(np.float64), np.zeros(n), np.zeros(n))
