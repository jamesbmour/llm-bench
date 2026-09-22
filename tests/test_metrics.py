from __future__ import annotations

import pytest

from llmsweep.metrics import TurnRecorder, pooled_throughput, regression_pct, summarize
from llmsweep.streams import ReasoningDelta, TextDelta, UsageEvent


def test_generation_only_and_reasoning_ttft() -> None:
    recorder = TurnRecorder(0)
    recorder.observe(ReasoningDelta("think"), 10)
    recorder.observe(TextDelta("answer"), 12)
    recorder.observe(UsageEvent(5, 100, 105, 10), 20)
    result = recorder.finish()
    assert result.ttft_ms == 10000
    assert result.generation_s == 2
    assert result.tok_s == 50
    assert result.reasoning_tokens == 10
    assert result.token_source == "usage"
    assert pooled_throughput([result, result]) == 50


def test_estimate_and_statistics() -> None:
    recorder = TurnRecorder(0)
    recorder.observe(TextDelta("hello"), 1)
    assert recorder.finish().output_tokens == 2
    assert recorder.finish().tok_s is None
    stats = summarize([1, 2, 3, 4, 100, None])
    assert stats.mean == 22 and stats.median == 3 and stats.p95 == 100
    assert summarize([]).mean is None
    assert regression_pct(94, 100, higher_better=True) == pytest.approx(6)
    assert regression_pct(1, 0, higher_better=True) is None
