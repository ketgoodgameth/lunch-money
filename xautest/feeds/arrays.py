"""Ticks held as numpy arrays, cached on disk, for fast repeated runs (optimization).

Parsing a month of MT5 CSV takes seconds; loading the cached arrays takes milliseconds and is
shared between worker processes through memory mapping.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from ..models import Tick

COLUMNS = ("ts", "bid", "ask", "bid_vol", "ask_vol")


DTYPES = {"ts": np.int64, "bid": np.float64, "ask": np.float64, "bid_vol": np.float32, "ask_vol": np.float32}


def build_cache(source: Iterable[Tick], directory: str | os.PathLike) -> Path:
    """Write the ticks as .npy columns. Sources with ``iter_arrays`` (CSV) are copied chunk by chunk,
    so even tens of millions of ticks never become Python objects."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    if hasattr(source, "iter_arrays"):
        parts = {c: [] for c in COLUMNS}
        for chunk in source.iter_arrays():
            for c, arr in zip(COLUMNS, chunk):
                parts[c].append(np.asarray(arr, dtype=DTYPES[c]))
        n = 0
        for c in COLUMNS:
            arr = np.concatenate(parts[c]) if parts[c] else np.empty(0, DTYPES[c])
            parts[c] = None  # free memory as we go
            np.save(d / f"{c}.npy", arr)
            n = len(arr)
            del arr
    else:
        cols = {c: [] for c in COLUMNS}
        for t in source:
            cols["ts"].append(t.ts); cols["bid"].append(t.bid); cols["ask"].append(t.ask)
            cols["bid_vol"].append(t.bid_vol); cols["ask_vol"].append(t.ask_vol)
        for c in COLUMNS:
            np.save(d / f"{c}.npy", np.asarray(cols[c], dtype=DTYPES[c]))
        n = len(cols["ts"])
    (d / "ok").write_text(str(n))
    return d


def cache_dir_for(root: str | os.PathLike, key: dict) -> Path:
    """A cache folder keyed on the data source and how it is read (path, mtimes, format, time zone)."""
    files = key.get("files", [])
    stamp = [(str(f), os.path.getmtime(f), os.path.getsize(f)) for f in files]
    h = hashlib.sha1(json.dumps({**key, "files": stamp}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return Path(root) / h


def load_cache(directory: str | os.PathLike) -> dict[str, np.ndarray]:
    d = Path(directory)
    return {c: np.load(d / f"{c}.npy", mmap_mode="r") for c in COLUMNS}


class ArrayFeed:
    """Iterates ticks from arrays within [start_ms, end_ms)."""

    def __init__(self, arrays: dict[str, np.ndarray], start_ms: int | None = None, end_ms: int | None = None):
        ts = arrays["ts"]
        lo = 0 if start_ms is None else int(np.searchsorted(ts, start_ms, "left"))
        hi = len(ts) if end_ms is None else int(np.searchsorted(ts, end_ms, "left"))
        self.a, self.lo, self.hi = arrays, lo, hi

    def __len__(self) -> int:
        return self.hi - self.lo

    def __iter__(self) -> Iterator[Tick]:
        a = self.a
        # walk in blocks so a year of ticks never sits in memory as Python lists all at once
        for lo in range(self.lo, self.hi, 1_000_000):
            s = slice(lo, min(lo + 1_000_000, self.hi))
            for t, b, k, bv, av in zip(a["ts"][s].tolist(), a["bid"][s].tolist(), a["ask"][s].tolist(),
                                       a["bid_vol"][s].tolist(), a["ask_vol"][s].tolist()):
                yield Tick(t, b, k, bv, av)
