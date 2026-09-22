"""Renderer-independent benchmark execution, cleanup, and persistence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from .errors import AuthenticationError, ConfigError, LlmsweepError
from .metrics import TurnRecorder
from .models import ModelInfo, ModelRef
from .providers.base import Lease, Provider
from .results import ModelResult, RunResult, SampleResult, compare_runs
from .scenarios import create_scenario
from .store import RunStore
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

    def settings(self) -> dict[str, Any]:
        # JSON normalization keeps settings comparable to reopened runs.
        return dict(json.loads(json.dumps(asdict(self))))


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

    def prepare(self, models: list[ModelInfo]) -> None:
        if self.options.parallel > 1 and any(not model.loaded for model in models):
            raise ConfigError("--parallel > 1 requires every selected model to be already loaded")
        self.run.models = [
            ModelResult(model, contended=self.options.parallel > 1) for model in models
        ]
        self.store.save(self.run)

    def result_for(self, ref: ModelRef) -> ModelResult:
        return next(model for model in self.run.models if model.model.ref == ref)

    def persist(self) -> None:
        if self.baseline:
            self.run.comparisons = compare_runs(self.run, self.baseline, *self.thresholds)
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
                for repeat in range(1, self.options.repeat + 1):
                    for name in self.options.scenarios:
                        if result.model.tool_use is False and name != "codegen":
                            result.samples.append(
                                SampleResult(
                                    name,
                                    repeat,
                                    status="skipped",
                                    output="model does not advertise tool capability",
                                )
                            )
                            self.persist()
                            continue
                        await self._sample(result, lease, name, repeat)
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

    async def _sample(self, result: ModelResult, lease: Lease, name: str, repeat: int) -> None:
        scenario = create_scenario(name, self.options.task)
        sample = SampleResult(name, repeat, expected_tools=list(scenario.expected_tools))
        sample.messages = [{"role": "user", "content": scenario.prompt}]
        result.samples.append(sample)
        started = self.clock()
        self.emit(RunEvent("scenario_start", result.model.ref, name))
        try:
            limit = 1 if name == "codegen" else self.options.max_turns or scenario.max_turns
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
                        self.options.max_tokens,
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
            sample.success, sample.output = await scenario.score(sample.answer, sample.tools_called)
            sample.status = "completed"
        except asyncio.CancelledError:
            sample.status = "cancelled"
            raise
        except Exception as exc:
            sample.status = "error"
            sample.error = self.provider.redact(str(exc))
            sample.success = None if self.options.task else False
            raise
        finally:
            scenario.close()
            sample.total_s = self.clock() - started
            self.persist()
            self.emit(RunEvent("sample_done", result.model.ref, name))
