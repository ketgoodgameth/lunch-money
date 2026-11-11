"""Broker server time -> UTC.

MT5 exports use the broker's server clock. Common settings:
  NY+7            New York time + 7 h: UTC+2 in winter, UTC+3 in summer, switching on US DST dates.
                  Each trading day then starts at 00:00 server time. Most MT5 brokers use this.
  Europe/Athens   Eastern European time: also +2/+3, but switching on EU DST dates
                  (differs from NY+7 for 2-3 weeks each March and about a week each autumn).
  +2, -5, ...     a fixed offset with no daylight saving.

``detect_server_tz`` infers the setting from when gold's daily break (17:00 New York) appears.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

NEW_YORK = "America/New_York"
_EPOCH = pd.Timestamp(0, tz="UTC")
_MS_H = 3_600_000

Converter = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]  # local ms -> (utc ms, valid mask)


def _fixed(hours: float) -> Converter:
    off = int(round(hours * _MS_H))
    return lambda ms: (ms - off, np.ones(len(ms), bool))


def _zone(name: str, shift_hours: float = 0.0) -> Converter:
    shift = pd.Timedelta(hours=shift_hours)

    def convert(ms: np.ndarray):
        if len(ms) == 0:
            return ms, np.ones(0, bool)
        t = pd.DatetimeIndex(pd.to_datetime(ms, unit="ms")) - shift
        # Clock-change hours fall on Sunday mornings/evenings when gold is closed; anything that does
        # land in one is marked invalid rather than guessed.
        t = t.tz_localize(name, ambiguous="NaT", nonexistent="NaT")
        valid = ~t.isna()
        out = np.zeros(len(ms), np.int64)
        out[valid] = (t[valid].tz_convert("UTC") - _EPOCH) // pd.Timedelta(milliseconds=1)
        return out, np.asarray(valid)

    return convert


def parse_server_tz(spec: str | float | int | None) -> Converter:
    """'NY+7', an IANA zone like 'Europe/Athens', or a fixed offset like 2, '+3', '-5'."""
    if spec is None or spec == "" or spec == 0:
        return _fixed(0.0)
    if isinstance(spec, (int, float)):
        return _fixed(float(spec))
    s = str(spec).strip()
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        return _fixed(float(s))
    m = re.fullmatch(r"(?i)ny([+-]\d+(\.\d+)?)", s)
    if m:
        return _zone(NEW_YORK, float(m.group(1)))
    try:
        pd.Timestamp("2025-01-01").tz_localize(s)
    except Exception as e:
        raise ValueError(f"unknown server time zone {spec!r}; use NY+7, an IANA name like Europe/Athens, "
                         f"or a fixed offset like +2") from e
    return _zone(s)


CANDIDATES = ["NY+7", "Europe/Athens", "Europe/Berlin", "Europe/London", "America/New_York"] + \
             [f"{h:+g}" for h in np.arange(-12, 14.5, 0.5)]


@dataclass
class Detection:
    best: list[str]          # settings that fit equally well, preferred first
    score: float             # share of daily breaks within tolerance of 17:00 New York
    breaks: int              # number of daily breaks found
    break_local: str         # most common time of the last tick before the break, in file time
    resume_local: str        # most common time trading resumes, in file time


def daily_breaks(local_ms: np.ndarray, min_gap_min: float = 40, max_gap_min: float = 180):
    """(last tick before, first tick after) for gaps that look like gold's daily 1-hour break, file clock."""
    if len(local_ms) < 2:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    d = np.diff(local_ms)
    idx = np.nonzero((d >= min_gap_min * 60_000) & (d <= max_gap_min * 60_000))[0]
    return local_ms[idx], local_ms[idx + 1]


def detect_server_tz(local_ms: np.ndarray, tolerance_min: float = 20.0) -> Detection | None:
    starts, ends = daily_breaks(np.asarray(local_ms, np.int64))
    if len(starts) < 2:
        return None
    scores = {}
    for cand in CANDIDATES:
        utc, valid = parse_server_tz(cand)(starts)
        if not valid.any():
            continue
        ny = pd.DatetimeIndex(pd.to_datetime(utc[valid], unit="ms", utc=True)).tz_convert(NEW_YORK)
        minutes = ny.hour * 60 + ny.minute + ny.second / 60
        err = np.abs(((minutes - 17 * 60) + 720) % 1440 - 720)
        scores[cand] = float((err <= tolerance_min).sum()) / len(starts)
    top = max(scores.values())
    best = [c for c in CANDIDATES if scores.get(c) == top]
    def common(ms):
        return pd.Series(pd.to_datetime(ms, unit="ms").strftime("%H:%M")).mode().iloc[0]
    return Detection(best, top, len(starts), common(starts), common(ends))
