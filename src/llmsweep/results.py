"""Serializable benchmark records, rollups, and comparable baseline verdicts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .metrics import TurnMetrics, pooled_throughput, regression_pct, summarize
from .models import ModelInfo, ModelRef


@dataclass
class SampleResult:
    """Raw transcript and measurements for one scenario repeat."""

    scenario: str
    repeat: int
    status: str = "running"
    success: bool | None = None
    answer: str = ""
    output: str = ""
    turns: list[TurnMetrics] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)
    tool_errors: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    total_s: float = 0
    error: str | None = None
    transcript: str | None = None

    @property
    def tok_s(self) -> float | None:
        return pooled_throughput(self.turns)

    @property
    def ttft_ms(self) -> float | None:
        return summarize([turn.ttft_ms for turn in self.turns]).mean

    def summary(self) -> dict[str, Any]:
        sources = sorted({t.token_source for t in self.turns})
        return {
            "tok_s": self.tok_s,
            "ttft_ms": self.ttft_ms,
            "output_tokens": sum(t.output_tokens for t in self.turns),
            "token_sources": sources,
            "token_source": "estimated"
            if "estimated" in sources
            else sources[0]
            if sources
            else None,
        }


@dataclass
class ModelResult:
    """Model lifecycle measurements and scenario repeat outcomes."""

    model: ModelInfo
    status: str = "pending"
    load_s: float | None = None
    load_status: str = "not attempted"
    warmup_s: float | None = None
    samples: list[SampleResult] = field(default_factory=list)
    total_s: float = 0
    error: str | None = None
    error_code: int = 1
    warnings: list[str] = field(default_factory=list)
    contended: bool = False

    def rollups(self) -> dict[str, Any]:
        result = {}
        for scenario in dict.fromkeys(s.scenario for s in self.samples):
            samples = [s for s in self.samples if s.scenario == scenario]
            complete = [s for s in samples if s.status == "completed"]
            scored = [s.success for s in samples if s.success is not None]
            result[scenario] = {
                "tok_s": asdict(summarize([s.tok_s for s in complete])),
                "ttft_ms": asdict(summarize([s.ttft_ms for s in complete])),
                "total_s": asdict(summarize([s.total_s for s in complete])),
                "success_rate": sum(scored) / len(scored) if scored else None,
                "token_sources": sorted({t.token_source for s in complete for t in s.turns}),
                "complete": bool(samples) and all(s.status == "completed" for s in samples),
            }
        return result

    def summary(self) -> dict[str, Any]:
        rollups = self.rollups()
        turns = [t for s in self.samples for t in s.turns]
        sources = sorted({t.token_source for t in turns})
        scored = [s.success for s in self.samples if s.success is not None]
        return {
            "provider": self.model.ref.provider,
            "id": self.model.ref.id,
            "tok_s": summarize([r["tok_s"]["mean"] for r in rollups.values()]).mean,
            "ttft_ms": summarize([s.ttft_ms for s in self.samples if s.status == "completed"]).mean,
            "token_source": "estimated"
            if "estimated" in sources
            else sources[0]
            if sources
            else None,
            "token_sources": sources,
            "output_tokens": sum(t.output_tokens for t in turns),
            "total_tokens": sum(t.total_tokens or 0 for t in turns)
            if turns and all(t.total_tokens is not None for t in turns)
            else None,
            "reasoning_tokens": sum(t.reasoning_tokens or 0 for t in turns)
            if turns and all(t.reasoning_tokens is not None for t in turns)
            else None,
            "success_rate": sum(scored) / len(scored) if scored else None,
            "success": all(scored) if scored else None,
            "turns": len(turns),
            "tools_called": list(dict.fromkeys(n for s in self.samples for n in s.tools_called)),
            "expected_tools": list(
                dict.fromkeys(n for s in self.samples for n in s.expected_tools)
            ),
            "tool_errors": [e for s in self.samples for e in s.tool_errors],
            "cost": None,
            "serving_provider": None,
            "scenarios": rollups,
        }


@dataclass
class RunResult:
    """Schema-versioned run document shared by both presentation modes."""

    run_id: str
    started_at: str
    settings: dict[str, Any]
    models: list[ModelResult] = field(default_factory=list)
    status: str = "running"
    schema_version: int = 1
    comparisons: list[dict[str, Any]] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        document = asdict(self)
        document["models"] = [
            asdict(model)
            | model.summary()
            | {"samples": [asdict(sample) | sample.summary() for sample in model.samples]}
            for model in self.models
        ]
        return document

    def exit_code(self, fail_on_regression: bool = False) -> int:
        errors = [m.error_code for m in self.models if m.error]
        if errors:
            return 4 if 4 in errors else 2 if 2 in errors else 1
        if self.status == "cancelled":
            return 1
        if fail_on_regression and any(c["verdict"] == "regression" for c in self.comparisons):
            return 3
        return 0


def restore_run(raw: dict[str, Any]) -> RunResult:
    models = []
    for row in raw["models"]:
        metadata = dict(row["model"])
        metadata["ref"] = ModelRef(**metadata["ref"])
        metadata["instances"] = tuple(metadata.get("instances", []))
        samples = []
        for sample in row.get("samples", []):
            values = dict(sample)
            for derived in ("tok_s", "ttft_ms", "output_tokens", "token_sources", "token_source"):
                values.pop(derived, None)
            values["turns"] = [TurnMetrics(**turn) for turn in values["turns"]]
            samples.append(SampleResult(**values))
        names = (
            "status",
            "load_s",
            "load_status",
            "warmup_s",
            "total_s",
            "error",
            "error_code",
            "warnings",
            "contended",
        )
        models.append(
            ModelResult(
                ModelInfo(**metadata), samples=samples, **{n: row[n] for n in names if n in row}
            )
        )
    return RunResult(
        raw["run_id"],
        raw["started_at"],
        raw["settings"],
        models,
        raw.get("status", "completed"),
        raw["schema_version"],
        raw.get("comparisons", []),
    )


def compare_runs(
    current: RunResult, baseline: RunResult, tok_threshold: float = 5, ttft_threshold: float = 5
) -> list[dict[str, Any]]:
    previous = {model.model.ref: model for model in baseline.models}
    comparisons = []
    keys = ("scenarios", "max_tokens", "max_turns", "task", "no_warmup", "benchmark_version")
    settings_match = all(current.settings.get(k) == baseline.settings.get(k) for k in keys)
    for model in current.models:
        prior = previous.get(model.model.ref)
        for scenario, now in model.rollups().items():
            before = prior.rollups().get(scenario) if prior else None
            reason = None
            if not before:
                reason = "no matching provider/model/scenario baseline"
            elif not settings_match:
                reason = "benchmark settings differ"
            elif model.contended or (prior and prior.contended):
                reason = "contended measurements"
            elif not now["complete"] or not before["complete"]:
                reason = "incomplete measurements"
            elif now["token_sources"] != before["token_sources"] or len(now["token_sources"]) != 1:
                reason = "token sources differ or are mixed"
            record: dict[str, Any] = {
                "model": model.model.ref.key,
                "scenario": scenario,
                "verdict": "not comparable",
                "reason": reason,
            }
            if reason is None and before:
                speed = regression_pct(
                    now["tok_s"]["mean"], before["tok_s"]["mean"], higher_better=True
                )
                ttft = regression_pct(
                    now["ttft_ms"]["mean"], before["ttft_ms"]["mean"], higher_better=False
                )
                regressed = (speed is not None and speed > tok_threshold) or (
                    ttft is not None and ttft > ttft_threshold
                )
                record.update(
                    tok_s_regression_pct=speed,
                    ttft_regression_pct=ttft,
                    verdict="regression" if regressed else "within threshold",
                )
                if speed is None and ttft is None:
                    record.update(verdict="not comparable", reason="no valid timing baseline")
            comparisons.append(record)
    return comparisons
