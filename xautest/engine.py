"""The event loop shared by backtests and live tests.

Per tick: broker fills/exits -> bar close (on_bar) -> on_tick. The only difference between a
backtest and a live test is where the ticks come from.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Iterable

from .bars import BarBuilder
from .broker import SimBroker
from .models import RunResult, Tick
from .strategy import Strategy

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, strategy: Strategy, broker: SimBroker, feed: Iterable[Tick], *, live: bool = False,
                 recorder=None, close_on_end: bool = True,
                 on_checkpoint: Callable[[RunResult], None] | None = None, checkpoint_every_s: float = 60.0):
        self.strategy, self.broker, self.feed = strategy, broker, feed
        self.live, self.recorder, self.close_on_end = live, recorder, close_on_end
        self.on_checkpoint, self.checkpoint_every_s = on_checkpoint, checkpoint_every_s
        self.bars = BarBuilder(strategy.timeframe) if strategy.timeframe else None

    def _dispatch(self) -> None:
        events, s = self.broker.events, self.strategy
        risk = s.risk
        while events:
            ev = events.pop(0)
            kind = ev[0]
            if kind == "fill":
                if risk is not None:
                    risk.filled(ev[1], ev[2])
                s.on_fill(ev[1], ev[2])
            elif kind == "close":
                if risk is not None:
                    risk.record(ev[1])
                s.on_trade(ev[1])
            elif kind == "reject":
                if risk is not None:
                    risk.rejected(ev[1])
                s.on_reject(ev[1], ev[2])
            elif kind in ("cancel", "expire") and risk is not None:
                risk.rejected(ev[1])

    def snapshot(self, stopped_reason: str = "running") -> RunResult:
        b = self.broker
        return RunResult(trades=list(b.trades), equity=list(b.equity_curve), initial_balance=b.cfg.initial_balance,
                         ticks_processed=self._n, first_ts=self._first, last_ts=b.tick.ts if b.tick else None,
                         stopped_reason=stopped_reason, rejections=b.rejections)

    def run(self) -> RunResult:
        s, broker, bars = self.strategy, self.broker, self.bars
        s.broker, s.live = broker, self.live
        self._n, self._first = 0, None
        reason = "end of data"
        next_cp = time.monotonic() + self.checkpoint_every_s
        started = False
        try:
            for raw in self.feed:
                tick = broker.adjust(raw)
                if self.recorder is not None:
                    self.recorder.write(raw)
                broker.on_tick(tick)
                if not started:
                    self._first = tick.ts
                    s.on_start()
                    started = True
                self._n += 1
                if broker.events:
                    self._dispatch()
                if bars is not None:
                    bar = bars.update(tick)
                    if bar is not None:
                        s.bars.append(bar)
                        s.on_bar(bar)
                        if broker.events:
                            self._dispatch()
                s.on_tick(tick)
                if broker.events:
                    self._dispatch()
                if self.on_checkpoint is not None and time.monotonic() >= next_cp:
                    self.on_checkpoint(self.snapshot())
                    next_cp = time.monotonic() + self.checkpoint_every_s
        except KeyboardInterrupt:
            reason = "stopped by user"
            log.info("stopping (Ctrl+C)")
        finally:
            if self.recorder is not None:
                self.recorder.close()
        if started:
            s.on_stop()
            if self.close_on_end and broker.positions:
                broker.flatten("end")  # realise P&L at the last known price
            self._dispatch()
        return self.snapshot(reason)
