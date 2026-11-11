"""Dukascopy historical tick data (.bi5 files).

Dukascopy ships ticks as LZMA-compressed arrays of 20-byte big-endian records:

    uint32 ms offset | uint32 ask | uint32 bid | float32 ask volume | float32 bid volume

Prices are integers that must be divided by the instrument's price scale. For XAUUSD the
scale is 1000 (2650.123 is stored as 2650123); ``check_prices`` guards against a wrong value.

Two on-disk layouts are understood, both with a ZERO-BASED month folder (January = 00):

    <root>/XAUUSD/2025/00/15_ticks.bi5          daily file, ms since 00:00 UTC (S3 bucket layout)
    <root>/XAUUSD/2025/00/15/14h_ticks.bi5      hourly file, ms since the hour (classic datafeed layout)

Getting the files: Dukascopy throttles scripted downloads from datafeed.dukascopy.com and points
automated clients at a requester-pays S3 bucket instead (``download_s3`` below; needs your own AWS
credentials and costs a few cents per instrument-year). You can also drop files you obtained any
other permitted way into ``<root>`` using either layout.
"""
from __future__ import annotations

import logging
import lzma
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from ..models import Tick, dt_to_ms

log = logging.getLogger(__name__)

RECORD = np.dtype([("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"), ("ask_vol", ">f4"), ("bid_vol", ">f4")])

PRICE_SCALE = {"XAUUSD": 1000, "XAGUSD": 1000}
PLAUSIBLE_RANGE = {"XAUUSD": (100.0, 20000.0), "XAGUSD": (1.0, 500.0)}

DEFAULT_S3_BUCKET = "cfg-public-proper-wallaby"
DEFAULT_S3_REGION = "eu-west-1"

_EMPTY = np.empty(0, RECORD)


def decode_bi5(raw: bytes) -> np.ndarray:
    """Decompress a .bi5 payload into a structured array of RECORD."""
    if not raw:
        return _EMPTY
    try:
        data = lzma.decompress(raw)  # FORMAT_AUTO handles the LZMA "alone" header Dukascopy uses
    except lzma.LZMAError:
        # Fall back to a header-less LZMA1 stream with the standard parameters.
        filters = [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 23, "lc": 3, "lp": 0, "pb": 2}]
        data = lzma.decompress(raw, format=lzma.FORMAT_RAW, filters=filters)
    if len(data) % RECORD.itemsize:
        raise ValueError(f"decoded size {len(data)} is not a multiple of {RECORD.itemsize}")
    return np.frombuffer(data, RECORD)


def encode_bi5(records: np.ndarray) -> bytes:
    """Inverse of decode_bi5 (used by tests and for writing fixtures)."""
    return lzma.compress(records.astype(RECORD).tobytes(), format=lzma.FORMAT_ALONE)


def records_to_arrays(rec: np.ndarray, base_ms: int, scale: float):
    ts = rec["ms"].astype(np.int64) + base_ms
    bid = rec["bid"].astype(np.float64) / scale
    ask = rec["ask"].astype(np.float64) / scale
    return ts, bid, ask, rec["bid_vol"].astype(np.float64), rec["ask_vol"].astype(np.float64)


def check_prices(symbol: str, bid: np.ndarray, scale: float, source: str) -> None:
    lo, hi = PLAUSIBLE_RANGE.get(symbol.upper(), (0.0, float("inf")))
    if len(bid) == 0:
        return
    med = float(np.median(bid))
    if not lo <= med <= hi:
        raise ValueError(
            f"{source}: median {symbol} price decoded as {med:,.4f} with price scale {scale}, outside the "
            f"plausible range {lo}-{hi}. The scale is probably wrong; pass --price-scale (try "
            f"{scale * 10:g} or {scale / 10:g})."
        )


def _month_dir(root: Path, symbol: str, d: date) -> Path:
    return root / symbol / f"{d.year:04d}" / f"{d.month - 1:02d}"


def daily_path(root: Path, symbol: str, d: date) -> Path:
    return _month_dir(root, symbol, d) / f"{d.day:02d}_ticks.bi5"


def hourly_path(root: Path, symbol: str, d: date, hour: int) -> Path:
    return _month_dir(root, symbol, d) / f"{d.day:02d}" / f"{hour:02d}h_ticks.bi5"


def _day_start_ms(d: date) -> int:
    return dt_to_ms(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))


def load_day(root: Path, symbol: str, d: date, scale: float):
    """Return (ts, bid, ask, bid_vol, ask_vol) arrays for one UTC day, or None if no file exists."""
    base = _day_start_ms(d)
    p = daily_path(root, symbol, d)
    if p.exists():
        rec = decode_bi5(p.read_bytes())
        if len(rec) > 1 and np.any(np.diff(rec["ms"].astype(np.int64)) < 0):
            log.warning("%s: timestamps go backwards; file may use per-hour offsets and will be mis-timed", p)
        return records_to_arrays(rec, base, scale)
    parts = []
    found = False
    for h in range(24):
        hp = hourly_path(root, symbol, d, h)
        if hp.exists():
            found = True
            rec = decode_bi5(hp.read_bytes())
            if len(rec):
                parts.append(records_to_arrays(rec, base + h * 3_600_000, scale))
    if not found:
        return None
    if not parts:
        return tuple(np.empty(0) for _ in range(5))
    return tuple(np.concatenate(cols) for cols in zip(*parts))


def _days(start: datetime, end: datetime) -> Iterator[date]:
    d = start.astimezone(timezone.utc).date()
    last = (end.astimezone(timezone.utc) - timedelta(milliseconds=1)).date()
    while d <= last:
        yield d
        d += timedelta(days=1)


class DukascopyFeed:
    """Iterates real historical ticks from local Dukascopy files in [start, end)."""

    def __init__(self, root: str | os.PathLike, start: datetime, end: datetime, symbol: str = "XAUUSD",
                 price_scale: float | None = None):
        self.root = Path(root)
        self.symbol = symbol.upper()
        self.start, self.end = start, end
        self.scale = price_scale or PRICE_SCALE.get(self.symbol, 1000)
        self.missing_days: list[date] = []

    def __iter__(self) -> Iterator[Tick]:
        start_ms, end_ms = dt_to_ms(self.start), dt_to_ms(self.end)
        checked = False
        any_file = False
        for d in _days(self.start, self.end):
            arrays = load_day(self.root, self.symbol, d, self.scale)
            if arrays is None:
                if d.weekday() != 5:  # Saturdays never have gold ticks
                    self.missing_days.append(d)
                continue
            any_file = True
            ts, bid, ask, bvol, avol = arrays
            if not checked and len(bid):
                check_prices(self.symbol, bid, self.scale, f"{self.symbol} {d}")
                checked = True
            mask = (ts >= start_ms) & (ts < end_ms)
            if not mask.all():
                ts, bid, ask, bvol, avol = ts[mask], bid[mask], ask[mask], bvol[mask], avol[mask]
            for t, b, a, bv, av in zip(ts.tolist(), bid.tolist(), ask.tolist(), bvol.tolist(), avol.tolist()):
                yield Tick(t, b, a, bv, av)
        if not any_file:
            raise FileNotFoundError(
                f"No Dukascopy files for {self.symbol} between {self.start:%Y-%m-%d} and {self.end:%Y-%m-%d} "
                f"under {self.root}. Expected e.g. {daily_path(self.root, self.symbol, self.start.date())} "
                f"(month folder is zero-based)."
            )
        if self.missing_days:
            log.warning("%d weekday(s) had no tick file (holidays are normal): %s%s", len(self.missing_days),
                        ", ".join(map(str, self.missing_days[:5])), " ..." if len(self.missing_days) > 5 else "")


def download_s3(dest: str | os.PathLike, start: datetime, end: datetime, symbol: str = "XAUUSD",
                bucket: str = DEFAULT_S3_BUCKET, region: str = DEFAULT_S3_REGION, workers: int = 8,
                overwrite: bool = False) -> dict:
    """Fetch daily .bi5 files from Dukascopy's requester-pays S3 bucket into ``dest``.

    Requires ``pip install boto3`` and AWS credentials in the usual places (env vars,
    ~/.aws/credentials or an AWS profile). Your AWS account pays the (small) transfer cost.
    """
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError
    except ImportError as e:  # pragma: no cover - depends on optional dependency
        raise SystemExit("download_s3 needs boto3: pip install boto3") from e

    s3 = boto3.client("s3", region_name=region,
                      config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, max_pool_connections=workers))
    root = Path(dest)
    stats = {"downloaded": 0, "cached": 0, "missing": 0, "bytes": 0}

    def fetch(d: date):
        local = daily_path(root, symbol, d)
        if local.exists() and not overwrite:
            return "cached", 0
        key = f"{symbol}/{d.year:04d}/{d.month - 1:02d}/{d.day:02d}_ticks.bi5"
        try:
            obj = s3.get_object(Bucket=bucket, Key=key, RequestPayer="requester")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return "missing", 0
            raise
        body = obj["Body"].read()
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(local)
        return "downloaded", len(body)

    days = [d for d in _days(start, end) if d.weekday() != 5]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for d, (status, n) in zip(days, pool.map(fetch, days)):
            stats[status] += 1
            stats["bytes"] += n
            log.info("%s %s %s", symbol, d, status)
    return stats
