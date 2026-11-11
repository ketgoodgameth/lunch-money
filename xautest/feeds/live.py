"""Live tick sources, real-time replay and tick recording.

Live testing here means forward testing: real ticks arrive from a broker's price stream and
orders are filled by the same simulated broker the backtester uses. No real orders are sent.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from ..models import Tick

log = logging.getLogger(__name__)


def parse_rfc3339_ms(s: str) -> int:
    """'2026-11-10T14:03:22.123456789Z' -> epoch ms (handles nanosecond fractions)."""
    s = s.rstrip("Z")
    base, _, frac = s.partition(".")
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    ms = int((frac + "000")[:3]) if frac else 0
    return int(dt.timestamp()) * 1000 + ms


class OandaStreamFeed:
    """Streams real XAU/USD quotes from OANDA's v20 pricing stream.

    A free OANDA practice (demo) account is enough. Credentials come from the environment:
    OANDA_TOKEN and OANDA_ACCOUNT_ID. Reconnects automatically with backoff; heartbeats
    (every ~5 s) are used to detect a dead connection.
    """

    HOSTS = {"practice": "https://stream-fxpractice.oanda.com", "live": "https://stream-fxtrade.oanda.com"}

    def __init__(self, instrument: str = "XAU_USD", account_id: str | None = None, token: str | None = None,
                 env: str = "practice", max_reconnects: int | None = None):
        self.instrument = instrument
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID")
        self.token = token or os.environ.get("OANDA_TOKEN")
        if not self.account_id or not self.token:
            raise SystemExit("Set OANDA_TOKEN and OANDA_ACCOUNT_ID (an OANDA practice account works).")
        if env not in self.HOSTS:
            raise ValueError(f"env must be one of {list(self.HOSTS)}")
        self.url = f"{self.HOSTS[env]}/v3/accounts/{self.account_id}/pricing/stream"
        self.max_reconnects = max_reconnects
        self.status = "connecting"

    def __iter__(self) -> Iterator[Tick]:
        import requests

        attempts = 0
        last_ts = 0
        while True:
            try:
                with requests.get(self.url, params={"instruments": self.instrument},
                                  headers={"Authorization": f"Bearer {self.token}"},
                                  stream=True, timeout=(10, 30)) as r:
                    if r.status_code in (401, 403):
                        raise SystemExit(f"OANDA rejected the credentials ({r.status_code}): {r.text[:200]}")
                    r.raise_for_status()
                    self.status = "streaming"
                    attempts = 0
                    for line in r.iter_lines():
                        if not line:
                            continue
                        msg = json.loads(line)
                        if msg.get("type") != "PRICE" or not msg.get("tradeable", True):
                            continue
                        bids, asks = msg.get("bids") or [], msg.get("asks") or []
                        if not bids or not asks:
                            continue
                        ts = max(parse_rfc3339_ms(msg["time"]), last_ts)  # keep time monotonic
                        last_ts = ts
                        yield Tick(ts, float(bids[0]["price"]), float(asks[0]["price"]),
                                   float(bids[0].get("liquidity", 0)), float(asks[0].get("liquidity", 0)))
            except (requests.RequestException, ValueError) as e:
                attempts += 1
                self.status = "reconnecting"
                if self.max_reconnects is not None and attempts > self.max_reconnects:
                    raise
                delay = min(60.0, 2 ** min(attempts, 6)) * (0.5 + random.random() / 2)
                log.warning("price stream dropped (%s); reconnecting in %.1fs", e, delay)
                time.sleep(delay)


class RealtimeReplay:
    """Plays any tick source at wall-clock pace (``speed`` x real time).

    Lets you exercise the live pipeline (recording, periodic reports, Ctrl+C handling)
    offline, on real historical ticks.
    """

    def __init__(self, source: Iterable[Tick], speed: float = 1.0):
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self.source, self.speed = source, speed

    def __iter__(self) -> Iterator[Tick]:
        t0_wall = t0_tick = None
        for tick in self.source:
            if t0_wall is None:
                t0_wall, t0_tick = time.monotonic(), tick.ts
            due = t0_wall + (tick.ts - t0_tick) / 1000.0 / self.speed
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            yield tick


class TickRecorder:
    """Appends every tick to a plain CSV (generic format) so a live session can be re-run as a backtest.

    Plain CSV rather than gzip so a crash or Ctrl+C never leaves an unreadable file.
    """

    def __init__(self, directory: str | os.PathLike, symbol: str = "XAUUSD", flush_every: int = 50):
        Path(directory).mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.path = Path(directory) / f"{symbol}_ticks_{stamp}.csv"
        self._fh = open(self.path, "w", newline="")
        self._w = csv.writer(self._fh)
        self._w.writerow(["ts_ms", "bid", "ask", "bid_vol", "ask_vol"])
        self.flush_every, self._n = flush_every, 0

    def write(self, t: Tick) -> None:
        self._w.writerow([t.ts, repr(t.bid), repr(t.ask), t.bid_vol, t.ask_vol])
        self._n += 1
        if self._n % self.flush_every == 0:
            self._fh.flush()

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()
