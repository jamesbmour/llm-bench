from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Literal

from .streams import (
    ReasoningDelta,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    UsageEvent,
    is_output_delta,
)

TokenSource = Literal["server", "usage", "estimated"]


@dataclass(frozen=True)
class Stats:
    count: int
    mean: float | None
    median: float | None
    p95: float | None


def summarize(values: list[float | None]) -> Stats:
    valid = sorted(v for v in values if v is not None and math.isfinite(v))
    if not valid:
        return Stats(0, None, None, None)
    return Stats(
        len(valid),
        statistics.fmean(valid),
        statistics.median(valid),
        valid[math.ceil(0.95 * len(valid)) - 1],
    )


@dataclass(frozen=True)
class TurnMetrics:
    ttft_ms: float | None
    generation_s: float
    output_tokens: int
    token_source: TokenSource
    total_tokens: int | None = None
    reasoning_tokens: int | None = None

    @property
    def tok_s(self) -> float | None:
        return self.output_tokens / self.generation_s if self.generation_s > 0 else None


@dataclass
class TurnRecorder:
    started: float
    first: float | None = None
    last: float | None = None
    output_bytes: int = 0
    usage: UsageEvent = field(default_factory=UsageEvent)

    def observe(self, event: StreamEvent, now: float) -> None:
        if is_output_delta(event):
            if self.first is None:
                self.first = now
            self.last = now
        if isinstance(event, (TextDelta, ReasoningDelta)):
            self.output_bytes += len(event.text.encode("utf-8"))
        elif isinstance(event, ToolCallDelta):
            self.output_bytes += len(((event.name or "") + event.arguments).encode("utf-8"))
        elif isinstance(event, UsageEvent):
            previous = self.usage
            self.usage = UsageEvent(
                *(
                    new if new is not None else old
                    for new, old in zip(
                        (
                            event.prompt_tokens,
                            event.completion_tokens,
                            event.total_tokens,
                            event.reasoning_tokens,
                        ),
                        (
                            previous.prompt_tokens,
                            previous.completion_tokens,
                            previous.total_tokens,
                            previous.reasoning_tokens,
                        ),
                        strict=True,
                    )
                )
            )

    def finish(self) -> TurnMetrics:
        count = self.usage.completion_tokens
        return TurnMetrics(
            ttft_ms=(self.first - self.started) * 1000 if self.first is not None else None,
            generation_s=max(0, self.last - self.first)
            if self.last is not None and self.first is not None
            else 0,
            output_tokens=count if count is not None else math.ceil(self.output_bytes / 4),
            token_source="usage" if count is not None else "estimated",
            total_tokens=self.usage.total_tokens,
            reasoning_tokens=self.usage.reasoning_tokens,
        )


def pooled_throughput(turns: list[TurnMetrics]) -> float | None:
    if not turns or any(turn.generation_s <= 0 for turn in turns):
        return None
    return sum(t.output_tokens for t in turns) / sum(t.generation_s for t in turns)


def regression_pct(
    current: float | None, baseline: float | None, *, higher_better: bool
) -> float | None:
    if current is None or baseline is None or baseline <= 0:
        return None
    return (baseline - current if higher_better else current - baseline) / baseline * 100
