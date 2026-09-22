from __future__ import annotations

from llmsweep.metrics import TurnMetrics
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.results import ModelResult, RunResult, SampleResult, compare_runs


def run(provider: str, speed: int) -> RunResult:
    sample = SampleResult(
        "weather", 1, status="completed", success=True, turns=[TurnMetrics(100, 1, speed, "usage")]
    )
    return RunResult(
        "id", "now", {}, [ModelResult(ModelInfo(ModelRef(provider, "qwen3:8b")), samples=[sample])]
    )


def test_provider_identity_and_thresholds() -> None:
    current = run("lmstudio", 94)
    baseline = run("ollama", 100)
    assert compare_runs(current, baseline)[0]["verdict"] == "not comparable"
    baseline = run("lmstudio", 100)
    current.comparisons = compare_runs(current, baseline)
    assert current.exit_code(True) == 3
    current = run("lmstudio", 96)
    current.comparisons = compare_runs(current, baseline)
    assert current.exit_code(True) == 0


def test_model_mean_is_mean_of_scenario_means() -> None:
    result = run("lmstudio", 10).models[0]
    result.samples.append(
        SampleResult("codegen", 1, status="completed", turns=[TurnMetrics(1, 1, 100, "usage")])
    )
    result.samples.append(
        SampleResult("codegen", 2, status="completed", turns=[TurnMetrics(1, 1, 100, "usage")])
    )
    assert result.summary()["tok_s"] == 55
