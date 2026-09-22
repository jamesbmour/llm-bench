"""Renderer-independent benchmark execution, cleanup, and persistence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .benchmarks.identity import attempt_id, config_fingerprint
from .benchmarks.presets import parse_task_filter
from .benchmarks.registry import tasks_for
from .benchmarks.runtime import open_spec, open_task
from .benchmarks.types import TaskSpec
from .comparison import compare_checked
from .errors import AuthenticationError, ConfigError, LlmsweepError, ResumeError
from .execution.policy import isolated_policy, require_isolation
from .metrics import TurnRecorder
from .models import ModelInfo, ModelRef
from .packs import load_pack
from .provenance import capture_provenance
from .providers.base import Lease, Provider
from .results import ModelResult, RunResult, SampleResult
from .statistics import reliability_report, wilson_interval
from .store import RunLock, RunStore
from .streams import ReasoningDelta, TextDelta, ToolCallAccumulator, ToolCallDelta


@dataclass(frozen=True)
class RunOptions:
    """Benchmark settings that affect execution and baseline comparability."""

    scenarios: tuple[str, ...] = ("weather", "agent-code", "codegen")
    repeat: int = 1
    max_tokens: int = 1024
    max_turns: int | None = None
    load_timeout: float = 600
    no_warmup: bool = False
    keep_loaded: bool = False
    parallel: int = 1
    task: str | None = None
    benchmark_version: int = 1
    preset: str | None = None
    pack: str | None = None
    profile: str | None = None
    repeat_mode: str = "fixed"
    min_repeats: int = 1
    max_repeats: int | None = None
    precision: float | None = None
    time_cap_s: float | None = None
    token_cap: int | None = None
    temperature: float | None = 0
    seed: int | None = None
    context_limit: int | None = None
    tasks: str | None = None

    def settings(self) -> dict[str, Any]:
        # JSON normalization keeps settings comparable to reopened runs.
        return dict(json.loads(json.dumps(asdict(self))))


def options_from_settings(settings: dict[str, Any]) -> RunOptions:
    """Rebuild options from a saved run so resume checks the original fingerprint."""
    data = dict(settings)
    data.pop("pack_digest", None)
    scenarios = data.get("scenarios")
    if isinstance(scenarios, list):
        data["scenarios"] = tuple(scenarios)
    names = {item.name for item in fields(RunOptions)}
    return RunOptions(**{key: value for key, value in data.items() if key in names})


@dataclass(frozen=True)
class RunEvent:
    """Presentation event emitted by the runner without UI dependencies."""

    kind: str
    ref: ModelRef
    scenario: str = ""
    text: str = ""
    turn: int = 0
    tool: str = ""
    at: float = 0
    tok_s: float | None = None


class RunSession:
    """Own execution, cancellation, instance cleanup, and saved run state."""

    def __init__(
        self,
        provider: Provider,
        store: RunStore,
        options: RunOptions,
        *,
        emit: Callable[[RunEvent], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        run: RunResult | None = None,
        baseline: RunResult | None = None,
        thresholds: tuple[float, float] = (5, 5),
    ) -> None:
        self.provider = provider
        self.store = store
        self.options = options
        self.emit = emit or (lambda event: None)
        self.clock = clock
        self.run = run or store.create(options.settings())
        self.baseline = baseline
        self.thresholds = thresholds
        self.semaphore = asyncio.Semaphore(options.parallel)
        self.tasks: dict[ModelRef, asyncio.Task[ModelResult]] = {}
        self.cancelled = False
        self._lock: RunLock | None = None
        self._catalog: dict[tuple[str, str], TaskSpec] = {}

    def _planned_tasks(self) -> tuple[TaskSpec, ...]:
        if self.options.pack:
            pack = load_pack(Path(self.options.pack))
            if pack.needs_isolation:
                require_isolation(isolated_policy(), f"pack {pack.pack_id}")
            self.run.settings = self.run.settings | {"pack_digest": pack.digest}
            return pack.tasks
        selected = parse_task_filter(self.options.tasks)
        planned = tasks_for(self.options.scenarios, selected)
        isolated = [task.suite_id for task in planned if task.execution == "isolated"]
        if isolated:
            require_isolation(isolated_policy(), ", ".join(sorted(set(isolated))))
        return planned

    def _lock_run(self) -> None:
        if self._lock is not None:
            return
        self._lock = RunLock(self.store.directory(self.run))
        self._lock.acquire()

    def prepare(self, models: list[ModelInfo]) -> None:
        if self.options.parallel > 1 and any(not model.loaded for model in models):
            raise ConfigError("--parallel > 1 requires every selected model to be already loaded")
        if self.run.schedule is not None:
            raise ResumeError("this run already has a schedule; use resume")
        planned = self._planned_tasks()
        self._catalog = {(task.suite_id, task.task_id): task for task in planned}
        self.run.schema_version = 2
        self.run.models = [
            ModelResult(model, contended=self.options.parallel > 1) for model in models
        ]
        manifest = [task.identity() | {"order": index} for index, task in enumerate(planned)]
        self.run.task_manifest = manifest
        repeats = (
            self.options.repeat if self.options.repeat_mode == "fixed" else self.options.min_repeats
        )
        schedule: list[dict[str, Any]] = []
        for model in models:
            for repeat in range(1, repeats + 1):
                for task in planned:
                    schedule.append(self._schedule_row(model.ref.key, task, repeat, 1))
        self.run.schedule = schedule
        self.run.fingerprint = config_fingerprint(self.options.settings(), manifest)
        self.run.repeat_policy = {
            "mode": self.options.repeat_mode,
            "requested_repeats": repeats,
            "max_repeats": self.options.max_repeats,
            "precision_target": self.options.precision,
            "time_cap_s": self.options.time_cap_s,
            "token_cap": self.options.token_cap,
            "stopped": None,
            "adaptive": self.options.repeat_mode == "exploratory",
        }
        server_version = getattr(self.provider, "version", None)
        self.run.provenance = capture_provenance(
            provider=models[0].ref.provider if models else "lmstudio",
            server_version=server_version if isinstance(server_version, str) else None,
            policy="workflow"
            if not any(task.execution == "isolated" for task in planned)
            else "isolated",
            requested={
                "temperature": self.options.temperature,
                "seed": self.options.seed,
                "max_tokens": self.options.max_tokens,
                "context_limit": self.options.context_limit,
            },
            benchmark_versions={task.suite_id: task.evaluator_version for task in planned},
        )
        self._lock_run()
        self.persist()

    def _schedule_row(
        self, model_key: str, task: TaskSpec, repeat: int, attempt: int
    ) -> dict[str, Any]:
        return {
            "model": model_key,
            "suite_id": task.suite_id,
            "task_id": task.task_id,
            "repeat": repeat,
            "attempt": attempt,
            "attempt_id": attempt_id(self.run.run_id, task.task_id, repeat, attempt),
            "status": "pending",
            "content_digest": task.content_digest,
            "pack_id": task.pack_id,
            "pack_version": task.pack_version,
            "requires_tools": task.requires_tools,
            "execution": task.execution,
            "max_turns": task.max_turns,
            "max_tokens": task.max_tokens,
            "task_seconds": task.task_seconds,
            "single_turn": task.single_turn,
        }

    def result_for(self, ref: ModelRef) -> ModelResult:
        return next(model for model in self.run.models if model.model.ref == ref)

    def requeue_model(self, ref: ModelRef) -> None:
        """Put one model's schedule rows back to pending for an in-session rerun."""
        for item in self.run.schedule or []:
            if item["model"] == ref.key:
                item["status"] = "pending"

    def persist(self) -> None:
        if self.baseline:
            self.run.comparisons = compare_checked(self.run, self.baseline, *self.thresholds)
        samples = [
            {
                "task_id": sample.task_id or sample.scenario,
                "scenario": sample.scenario,
                "status": sample.status,
                "success": sample.success,
                "total_s": sample.total_s,
            }
            for model in self.run.models
            for sample in model.samples
        ]
        self.run.statistics = reliability_report(
            samples,
            adaptive=bool(self.run.repeat_policy.get("adaptive")),
            rng=random.Random(self.run.run_id),
        )
        self.store.save(self.run)

    def fail_model(self, ref: ModelRef, error: BaseException) -> ModelResult:
        result = self.result_for(ref)
        result.status = "error"
        result.error = self.provider.redact(str(error)) or type(error).__name__
        result.error_code = 4 if isinstance(error, AuthenticationError) else 1
        self.persist()
        self.emit(RunEvent("model_done", ref))
        return result

    async def run_all(self, models: list[ModelInfo]) -> RunResult:
        self.prepare(models)
        self.tasks = {model.ref: asyncio.create_task(self.run_model(model.ref)) for model in models}
        try:
            outcomes = await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            for ref, outcome in zip(self.tasks, outcomes, strict=True):
                if isinstance(outcome, BaseException) and not isinstance(
                    outcome, asyncio.CancelledError
                ):
                    self.fail_model(ref, outcome)
        except asyncio.CancelledError:
            await self.cancel()
        finally:
            self.finish()
        return self.run

    async def cancel(self) -> None:
        self.cancelled = True
        for task in self.tasks.values():
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.finish()

    def finish(self) -> None:
        cancelled = self.cancelled or any(m.status == "cancelled" for m in self.run.models)
        self.run.status = "cancelled" if cancelled else "completed"
        for model in self.run.models:
            if model.status == "pending" and cancelled:
                model.status = "cancelled"
        self.persist()
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    async def run_model(self, ref: ModelRef) -> ModelResult:
        result = self.result_for(ref)
        started = self.clock()
        lease: Lease | None = None
        acquired: asyncio.Task[Lease] | None = None
        try:
            async with self.semaphore:
                result.status = "running"
                self.emit(RunEvent("model_start", ref))
                acquired = asyncio.create_task(
                    self.provider.acquire(
                        result.model,
                        self.options.load_timeout,
                        allow_load=self.options.parallel == 1,
                    )
                )
                lease = await asyncio.shield(acquired)
                result.load_s, result.load_status = lease.load_s, lease.note
                result.observed = {
                    "quantization": result.model.quantization,
                    "revision": None,
                    "temperature": None,
                    "seed": None,
                    "context_limit": None,
                }
                if result.model.tool_use is None:
                    result.warnings.append(
                        "tool capability unknown; attempting requested scenarios"
                    )
                if not self.options.no_warmup:
                    start = self.clock()
                    async for _ in self.provider.chat(
                        lease.chat_id,
                        [{"role": "user", "content": "Reply with exactly: ok"}],
                        [],
                        8,
                    ):
                        pass
                    result.warmup_s = self.clock() - start
                await self._run_scheduled(result, lease)
                result.status = "completed"
        except asyncio.CancelledError:
            result.status = "cancelled"
            if acquired and lease is None:
                # Wait for our outstanding load response so cancellation cannot orphan its instance.
                with contextlib.suppress(LlmsweepError, TimeoutError):
                    lease = await asyncio.shield(acquired)
            raise
        except (LlmsweepError, OSError) as exc:
            result.status = "error"
            result.error = self.provider.redact(str(exc))
            result.error_code = (
                4
                if isinstance(exc, AuthenticationError)
                else 2
                if isinstance(exc, ConfigError)
                else 1
            )
        finally:
            if lease and not self.options.keep_loaded:
                cleanup = asyncio.create_task(self.provider.release(lease))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                except LlmsweepError as exc:
                    message = self.provider.redact(f"cleanup failed: {exc}")
                    result.error = f"{result.error}; {message}" if result.error else message
                    if result.status != "cancelled":
                        result.status = "error"
            result.total_s = self.clock() - started
            self.persist()
            self.emit(RunEvent("model_done", ref))
        return result

    async def _run_scheduled(self, result: ModelResult, lease: Lease) -> None:
        while True:
            pending = [
                item
                for item in self.run.schedule or []
                if item["model"] == result.model.ref.key and item["status"] == "pending"
            ]
            if not pending:
                break
            for item in pending:
                if result.model.tool_use is False and item["requires_tools"]:
                    item["status"] = "skipped"
                    result.samples.append(
                        SampleResult(
                            item["suite_id"],
                            item["repeat"],
                            status="skipped",
                            output="model does not advertise tool capability",
                            task_id=item["task_id"],
                            suite_id=item["suite_id"],
                            attempt_id=item["attempt_id"],
                            attempt=item["attempt"],
                            content_digest=item["content_digest"],
                            pack_id=item["pack_id"],
                            pack_version=item["pack_version"],
                        )
                    )
                    self.persist()
                    continue
                await self._sample(result, lease, item)
            if not self._extend_exploratory(result):
                break

    def _extend_exploratory(self, result: ModelResult) -> bool:
        policy = self.run.repeat_policy
        if not policy.get("adaptive"):
            policy["stopped"] = "fixed"
            return False
        repeats = max(
            (
                item["repeat"]
                for item in self.run.schedule or []
                if item["model"] == result.model.ref.key
            ),
            default=0,
        )
        limit = policy.get("max_repeats") or repeats
        if repeats >= limit:
            policy["stopped"] = "max_repeats"
            return False
        elapsed = sum(sample.total_s for sample in result.samples)
        if policy.get("time_cap_s") is not None and elapsed >= float(policy["time_cap_s"]):
            policy["stopped"] = "time_cap"
            return False
        tokens = sum(turn.output_tokens for sample in result.samples for turn in sample.turns)
        if policy.get("token_cap") is not None and tokens >= int(policy["token_cap"]):
            policy["stopped"] = "token_cap"
            return False
        widths: list[float] = []
        by_task: dict[str, list[bool]] = {}
        for sample in result.samples:
            if sample.success is None or sample.status != "completed":
                continue
            by_task.setdefault(sample.task_id or sample.scenario, []).append(bool(sample.success))
        target = policy.get("precision_target")
        for outcomes in by_task.values():
            interval = wilson_interval(sum(outcomes), len(outcomes))
            if interval is None:
                return False
            widths.append(interval[1] - interval[0])
        if target is not None and widths and max(widths) <= float(target):
            policy["stopped"] = "precision"
            return False
        if target is None:
            policy["stopped"] = "max_repeats"
            return False
        nxt = repeats + 1
        for item in list(self.run.schedule or []):
            if item["model"] != result.model.ref.key or item["repeat"] != 1:
                continue
            task = self._catalog[(item["suite_id"], item["task_id"])]
            row = self._schedule_row(result.model.ref.key, task, nxt, 1)
            assert self.run.schedule is not None
            self.run.schedule.append(row)
        policy["stopped"] = None
        return True

    async def _sample(self, result: ModelResult, lease: Lease, item: dict[str, Any]) -> None:
        name = str(item["suite_id"])
        repeat = int(item["repeat"])
        spec = self._catalog.get((name, str(item["task_id"])))
        scenario = (
            open_task(name, str(item["task_id"]), custom=self.options.task)
            if spec is None
            else open_spec(spec, custom=self.options.task)
        )
        sample = SampleResult(
            name,
            repeat,
            expected_tools=list(scenario.expected_tools),
            pack_id=str(item.get("pack_id") or ""),
            pack_version=str(item.get("pack_version") or ""),
            suite_id=name,
            task_id=str(item["task_id"]),
            content_digest=item.get("content_digest"),
            attempt_id=str(item["attempt_id"]),
            attempt=int(item["attempt"]),
            target_kind="model",
            diagnostic=self.run.diagnostic,
        )
        sample.messages = [{"role": "user", "content": scenario.prompt}]
        result.samples.append(sample)
        item["status"] = "running"
        started = self.clock()
        self.emit(RunEvent("scenario_start", result.model.ref, name))
        try:
            limit = 1 if item.get("single_turn") else self.options.max_turns or scenario.max_turns
            tokens = (
                int(item["max_tokens"])
                if self.options.max_tokens == 1024
                else self.options.max_tokens
            )
            async with asyncio.timeout(float(item.get("task_seconds") or 300)):
                await self._turns(result, lease, scenario, sample, name, limit, tokens)
            if hasattr(scenario, "score_detail"):
                success, output, category, assertions, _metrics = await scenario.score_detail(
                    sample.answer, sample.tools_called
                )
                sample.assertions = list(assertions)
            else:
                success, output = await scenario.score(sample.answer, sample.tools_called)
                category = None if success is not False else "incorrect"
            if success is not True and any(
                "tool policy" in err or "not allowed" in err or "escapes" in err
                for err in sample.tool_errors
            ):
                category = "tool_policy"
            sample.success, sample.output, sample.failure_category = success, output, category
            sample.status = "completed"
            item["status"] = "completed"
        except asyncio.CancelledError:
            sample.status = "cancelled"
            item["status"] = "cancelled"
            raise
        except TimeoutError:
            sample.status = "completed"
            sample.success = False
            sample.failure_category = "budget"
            sample.output = "task budget exhausted"
            item["status"] = "completed"
        except Exception as exc:
            sample.status = "error"
            sample.error = self.provider.redact(str(exc))
            sample.failure_category = (
                "evaluator" if type(scenario).__name__ == "TaskScenario" else "provider"
            )
            sample.success = None if self.options.task else False
            item["status"] = "error"
            if type(scenario).__name__ != "TaskScenario":
                raise
        finally:
            self._capture_evidence(sample, scenario)
            scenario.close()
            sample.total_s = self.clock() - started
            self.persist()
            self.emit(RunEvent("sample_done", result.model.ref, name))

    async def _turns(
        self,
        result: ModelResult,
        lease: Lease,
        scenario: Any,
        sample: SampleResult,
        name: str,
        limit: int,
        tokens: int,
    ) -> None:
        for turn in range(1, limit + 1):
            recorder = TurnRecorder(self.clock())
            text: list[str] = []
            reasoning: list[str] = []
            calls = ToolCallAccumulator()
            self.emit(RunEvent("turn", result.model.ref, name, turn=turn))
            try:
                async for event in self.provider.chat(
                    lease.chat_id,
                    sample.messages,
                    scenario.tools,
                    tokens,
                ):
                    observed_at = self.clock()
                    recorder.observe(event, observed_at)
                    if isinstance(event, TextDelta):
                        text.append(event.text)
                        self.emit(
                            RunEvent(
                                "text",
                                result.model.ref,
                                name,
                                event.text,
                                turn,
                                at=observed_at,
                                tok_s=recorder.finish().tok_s,
                            )
                        )
                    elif isinstance(event, ReasoningDelta):
                        reasoning.append(event.text)
                        self.emit(
                            RunEvent(
                                "reasoning",
                                result.model.ref,
                                name,
                                event.text,
                                turn,
                                at=observed_at,
                                tok_s=recorder.finish().tok_s,
                            )
                        )
                    elif isinstance(event, ToolCallDelta):
                        calls.add(event)
            finally:
                sample.turns.append(recorder.finish())
                sample.answer = "".join(text)
                message: dict[str, Any] = {"role": "assistant", "content": sample.answer}
                if reasoning:
                    message["reasoning_content"] = "".join(reasoning)
                assembled = calls.finish(turn=turn)
                if assembled:
                    message["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in assembled
                    ]
                sample.messages.append(message)
            if not assembled:
                break
            for call in assembled:
                if call.name not in sample.tools_called:
                    sample.tools_called.append(call.name)
                try:
                    arguments = call.parsed_arguments()
                    output = await scenario.call(call.name, arguments)
                except (ValueError, TypeError) as exc:
                    output = {"error": f"invalid tool arguments: {exc}"}
                if "error" in output:
                    sample.tool_errors.append(str(output["error"]))
                sample.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )
                self.emit(
                    RunEvent(
                        "tool",
                        result.model.ref,
                        name,
                        self.provider.redact(
                            json.dumps({"arguments": call.arguments, "result": output})
                        ),
                        turn,
                        call.name,
                    )
                )

    def validate_resume(self, models: list[ModelInfo]) -> None:
        if not self.run.schedule or not self.run.fingerprint or not self.run.task_manifest:
            raise ResumeError("legacy or incomplete runs stay viewable but cannot be resumed")
        current = config_fingerprint(self.options.settings(), self.run.task_manifest)
        if current != self.run.fingerprint:
            raise ResumeError("configuration or fixture fingerprint does not match this run")
        from .benchmarks.registry import find_task

        for item in self.run.schedule:
            if item.get("pack_id") not in (None, "", "builtin"):
                continue
            task = find_task(str(item["suite_id"]), str(item["task_id"]))
            if task.content_digest != item.get("content_digest"):
                raise ResumeError(f"task fingerprint changed for {item['task_id']}")
        wanted = {model.ref.key for model in models}
        saved = {model.model.ref.key for model in self.run.models}
        if wanted != saved:
            raise ResumeError("resume targets do not match the saved run")

    def reopen_interrupted(self) -> None:
        schedule = self.run.schedule or []
        for item in schedule:
            if item["status"] in {"completed", "skipped", "error", "pending"}:
                continue
            for model in self.run.models:
                for sample in model.samples:
                    if sample.attempt_id == item["attempt_id"] and sample.status in {
                        "running",
                        "cancelled",
                        "interrupted",
                    }:
                        sample.status = "interrupted"
            item["attempt"] = int(item["attempt"]) + 1
            item["attempt_id"] = attempt_id(
                self.run.run_id, str(item["task_id"]), int(item["repeat"]), int(item["attempt"])
            )
            item["status"] = "pending"

    async def resume_all(self, models: list[ModelInfo]) -> RunResult:
        self.validate_resume(models)
        self._catalog = {(task.suite_id, task.task_id): task for task in self._planned_tasks()}
        self.reopen_interrupted()
        self._lock_run()
        self.tasks = {model.ref: asyncio.create_task(self.run_model(model.ref)) for model in models}
        try:
            outcomes = await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            for ref, outcome in zip(self.tasks, outcomes, strict=True):
                if isinstance(outcome, BaseException) and not isinstance(
                    outcome, asyncio.CancelledError
                ):
                    self.fail_model(ref, outcome)
        except asyncio.CancelledError:
            await self.cancel()
        finally:
            self.finish()
        return self.run

    def _capture_evidence(self, sample: SampleResult, scenario: Any) -> None:
        blobs: dict[str, str] = {}
        if sample.output:
            blobs["output.txt"] = sample.output[-8000:]
        if sample.tool_errors:
            blobs["tool_errors.txt"] = "\n".join(sample.tool_errors)[-8000:]
        if sample.assertions:
            blobs["assertions.json"] = json.dumps(sample.assertions)
        diff = getattr(scenario, "diff_text", None)
        if diff is not None:
            text = diff()
            if text:
                blobs["diff.txt"] = text
        if blobs:
            sample.artifact_blobs = blobs
