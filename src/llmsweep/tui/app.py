"""App-owned asynchronous workers and buffered benchmark event rendering."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import work
from textual.app import App
from textual.binding import Binding, BindingType
from textual.widgets import ProgressBar, RichLog, Static
from textual.worker import Worker, WorkerState

from llmsweep.config import Settings
from llmsweep.errors import LlmsweepError
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.plain import export_run
from llmsweep.providers.base import Provider
from llmsweep.providers.lmstudio import LMStudio
from llmsweep.results import ModelResult, RunResult, compare_runs
from llmsweep.runner import RunEvent, RunSession
from llmsweep.security import Redactor
from llmsweep.selection import filter_models, resolve_selection
from llmsweep.store import RunStore, load_run
from llmsweep.tui.screens.main import (
    DiffScreen,
    HelpScreen,
    LiveScreen,
    PathDialog,
    PickerScreen,
    ResultsScreen,
    TranscriptScreen,
)
from llmsweep.tui.widgets.chrome import StatusBar

THEMES = (
    "textual-dark",
    "textual-light",
    "nord",
    "gruvbox",
    "tokyo-night",
    "catppuccin-mocha",
    "dracula",
    "monokai",
    "solarized-light",
    "flexoki",
)
TONES = ("primary", "secondary", "accent", "success", "warning", "error", "foreground")


class SweepApp(App[int]):
    """Own workers for the full lifetime of a benchmark and its cleanup."""

    CSS_PATH = "theme.tcss"
    TITLE = "llmsweep"
    SUB_TITLE = "LM Studio benchmarks"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+x", "cancel_run", "Cancel run", priority=True),
        Binding("ctrl+q", "interrupt_hint", "", show=False, priority=True),
        Binding("ctrl+t", "cycle_theme", "Theme", show=False, priority=True),
        Binding("f1", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False, key_display="?"),
    ]

    def __init__(
        self,
        settings: Settings,
        *,
        provider: Provider | None = None,
        session: RunSession | None = None,
        saved_run: RunResult | None = None,
        comparison_runs: list[RunResult] | None = None,
        coverage_runs: list[RunResult] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.saved_run = saved_run
        self.comparison_runs = comparison_runs
        self.coverage_runs = coverage_runs
        self.provider = provider or (session.provider if session else None)
        self.session = session
        self.redact = self.provider.redact if self.provider else Redactor(settings.api_key)
        self.model_workers: dict[ModelRef, Worker[ModelResult]] = {}
        self.pending_events: list[RunEvent] = []
        self.live: LiveScreen | None = None
        self.started = time.monotonic()
        self.run_eta: float | None = None
        self.error: str | None = None
        self.stopping = False
        self.transitioning = False
        self.discovered = False
        self.status: dict[str, str] = {}

    def on_mount(self) -> None:
        self.apply_theme(str(self.settings.values["theme"]))
        self.set_class(self.settings.values["no_color"], "monochrome")
        self.set_interval(0.075, self.flush_events)
        if self.comparison_runs:
            from llmsweep.tui.screens.main import ComparisonScreen

            self.push_screen(ComparisonScreen(self.comparison_runs))
        elif self.coverage_runs:
            from llmsweep.tui.screens.main import CoverageScreen

            self.push_screen(CoverageScreen(self.coverage_runs))
        elif self.saved_run:
            self.push_screen(ResultsScreen(self.saved_run, self.settings.values["sort_by"]))
        else:
            self.push_screen(PickerScreen())
            self.call_after_refresh(self.discover_models)
    def apply_theme(self, name: str) -> None:
        if name not in self.available_themes:
            self.notify(f"Unknown theme {name!r}; using {THEMES[0]}", severity="warning")
            name = THEMES[0]
        self.theme = name
        self.update_status(theme=f"^t theme {name}")

    def action_cycle_theme(self) -> None:
        index = THEMES.index(self.theme) if self.theme in THEMES else -1
        self.apply_theme(THEMES[(index + 1) % len(THEMES)])
        self.notify(f"Theme: {self.theme}", timeout=2)

    def tone(self, name: str) -> str:
        """Return a Rich style for a theme color, or nothing when colors are disabled."""
        if self.has_class("monochrome") or name not in TONES:
            return ""
        return self.theme_variables.get(name, "")

    def context_label(self) -> str:
        if self.session is None and self.saved_run is not None:
            return f"run {self.saved_run.run_id}"
        return str(self.settings.values["base_url"])

    def update_status(self, **segments: str | None) -> None:
        for key, value in segments.items():
            if value is None:
                self.status.pop(key, None)
            else:
                self.status[key] = value
        for bar in self.screen.query(StatusBar):
            bar.show(self.status)

    @work(exit_on_error=False, exclusive=False, group="discovery", description="Discover LM Studio")
    async def discover_models(self) -> list[ModelInfo]:
        if self.provider is None:
            self.provider = LMStudio(
                self.settings.values["base_url"],
                self.settings.api_key,
                self.settings.values["timeout"],
            )
            self.redact = self.provider.redact
        if self.session is None:
            values = self.settings.values
            store = RunStore(
                Path(values["run_store"]) if values["run_store"] else None,
                Path(values["transcript_dir"]) if values["transcript_dir"] else None,
                self.redact,
            )
            baseline = load_run(Path(values["baseline"])) if values["baseline"] else None
            self.session = RunSession(
                self.provider,
                store,
                self.settings.run_options(),
                emit=self._emit_event,
                baseline=baseline,
                thresholds=self.settings.thresholds,
            )
        else:
            self.session.emit = self._emit_event
        return filter_models(
            await self.provider.list_models(),
            tools_only=self.settings.values["require_tool_use"],
            exclude=self.settings.values["exclude"],
        )

    def start_run(self, models: list[ModelInfo]) -> None:
        assert self.session is not None
        try:
            self.session.prepare(models)
        except LlmsweepError as exc:
            self.error = self.redact(str(exc))
            self.notify(self.error, severity="error")
            return
        self.started = time.monotonic()
        self.run_eta = self.session.store.estimate(
            [m.ref for m in models], self.session.options.settings()
        )
        self.live = LiveScreen(self.session.run.models)
        self.switch_screen(self.live)
        self.model_workers = {model.ref: self.model_worker(model.ref) for model in models}

    @work(exit_on_error=False, exclusive=False, group="models", description="Run benchmark model")
    async def model_worker(self, ref: ModelRef) -> ModelResult:
        assert self.session is not None
        return await self.session.run_model(ref)

    def _emit_event(self, event: RunEvent) -> None:
        self.pending_events.append(event)

    def flush_events(self) -> None:
        if not self.live or not self.live.cards:
            return
        events = list(self.pending_events)
        self.pending_events.clear()
        for event in events:
            card = self.live.cards.get(event.ref)
            if card:
                card.accept(event)
            if (
                event.kind == "tool"
                and self.settings.values["verbose"]
                and self.live.query("#verbose-log")
            ):
                self.live.query_one("#verbose-log", RichLog).write(Text(self.redact(event.text)))
        elapsed = time.monotonic() - self.started
        for card in self.live.cards.values():
            card.flush(elapsed)
        if not self.live.is_mounted or self.live not in self.screen_stack:
            return
        done = sum(m.status in ("completed", "error", "cancelled") for m in self.live.results)
        self.live.query_one(ProgressBar).update(progress=done)
        eta = (
            f" · estimated remaining {max(0, self.run_eta - elapsed):.0f}s"
            if self.run_eta is not None
            else ""
        )
        self.live.query_one("#run-clock", Static).update(f"Elapsed {elapsed:.1f}s{eta}")
        self.update_status(
            run=f"running {done}/{len(self.live.results)} done", clock=f"{elapsed:.0f}s"
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if worker.group == "discovery":
            if event.state == WorkerState.SUCCESS:
                self.discovered = True
                models = worker.result or []
                if isinstance(self.screen, PickerScreen):
                    self.screen.set_models(models)
                values = self.settings.values
                try:
                    if values["models"]:
                        self.start_run(resolve_selection(values["models"], models))
                    elif values["all"]:
                        if not models:
                            raise ValueError("no eligible chat models")
                        self.start_run(models)
                except (LlmsweepError, ValueError) as exc:
                    self.error = self.redact(str(exc))
                    self.notify(self.error, severity="error")
            elif event.state == WorkerState.ERROR:
                self.error = self.redact(str(worker.error))
                if isinstance(self.screen, PickerScreen):
                    self.screen.summary(self.error)
            return
        ref = next(
            (ref for ref, candidate in self.model_workers.items() if candidate is worker), None
        )
        if ref is None or self.session is None:
            if event.state == WorkerState.ERROR:
                self.error = self.redact(str(worker.error))
                self.notify(self.error, severity="error")
            return
        if event.state == WorkerState.ERROR:
            error = worker.error or RuntimeError("model worker failed")
            self.error = self.redact(str(error))
            self.session.fail_model(ref, error)
        elif event.state == WorkerState.CANCELLED:
            result = self.session.result_for(ref)
            result.status = "cancelled"
            self.session.persist()
        elif event.state == WorkerState.SUCCESS:
            if not isinstance(worker.result, ModelResult):
                self.session.fail_model(ref, RuntimeError("invalid model worker result"))
        if event.state not in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            return
        if (
            self.model_workers
            and all(w.is_finished for w in self.model_workers.values())
            and not self.stopping
            and not self.transitioning
        ):
            self.show_results()

    def show_results(self) -> None:
        if self.session is None:
            return
        self.flush_events()
        self.session.finish()
        self.saved_run = self.session.run
        self.switch_screen(ResultsScreen(self.saved_run, self.settings.values["sort_by"]))

    def action_cancel_run(self) -> None:
        if self.stopping or not any(not w.is_finished for w in self.model_workers.values()):
            return
        self.stopping = True
        self.stop_workers(False)

    async def action_quit(self) -> None:
        if self.stopping:
            self.notify("Waiting for model cleanup")
            return
        self.stopping = True
        self.stop_workers(True)

    @work(exit_on_error=False, exclusive=False, group="cleanup", description="Cancel and clean up")
    async def stop_workers(self, quitting: bool) -> None:
        active = [w for w in self.model_workers.values() if not w.is_finished]
        for worker in active:
            worker.cancel()
        await asyncio.gather(*(w.wait() for w in active), return_exceptions=True)
        if self.session and self.session.run.models:
            self.session.cancelled = bool(active)
            self.show_results()
        if quitting:
            if self.provider:
                await self.provider.close()
            code = (
                self.saved_run.exit_code(self.settings.values["fail_on_regression"])
                if self.saved_run
                else (2 if self.error else 0)
            )
            self.exit(code)
        self.stopping = False

    @work(exit_on_error=False, exclusive=False, group="rerun", description="Rerun selected model")
    async def rerun_model(self, ref: ModelRef) -> None:
        if self.session is None:
            self.notify("Reopened runs are read-only; start a new run to benchmark again")
            return
        self.transitioning = True
        try:
            worker = self.model_workers.get(ref)
            if worker and not worker.is_finished:
                worker.cancel()
                await asyncio.gather(worker.wait(), return_exceptions=True)
            old = self.session.result_for(ref)
            replacement = ModelResult(old.model, contended=old.contended)
            self.session.run.models[self.session.run.models.index(old)] = replacement
            self.session.requeue_model(ref)
            self.session.run.status = "running"
            self.session.cancelled = False
            self.flush_events()
            retained = (
                {key: card for key, card in self.live.cards.items() if key != ref}
                if self.live
                else {}
            )
            self.live = LiveScreen(self.session.run.models, retained)
            self.switch_screen(self.live)
            self.model_workers[ref] = self.model_worker(ref)
        finally:
            self.transitioning = False

    def action_interrupt_hint(self) -> None:
        self.notify("Press Ctrl+C to quit; Ctrl+X cancels the run")

    def binding_rows(self) -> list[tuple[str, str, str]]:
        """Describe every binding reachable from the current screen, global ones first."""
        screen_name = type(self.screen).__name__
        rows: list[tuple[int, str, str, str]] = []
        for active in self.active_bindings.values():
            binding = active.binding
            if not binding.description:
                continue
            if active.node is self:
                rank, scope = 0, "Global"
            elif active.node is self.screen:
                rank, scope = 1, screen_name
            else:
                rank, scope = 2, type(active.node).__name__
            rows.append((rank, self.get_key_display(binding), binding.description, scope))
        rows.sort(key=lambda row: row[0])
        return [(key, description, scope) for _, key, description, scope in rows]

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss()
            return
        self.push_screen(HelpScreen(self.binding_rows()))

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="View transcript")
    async def show_transcript(self, model: ModelResult) -> None:
        await self.push_screen_wait(TranscriptScreen(model))

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Compare baseline")
    async def show_diff(self) -> None:
        run = self.saved_run or (self.session.run if self.session else None)
        if run is None:
            return
        if not run.comparisons:
            path = await self.push_screen_wait(PathDialog("Baseline run directory or JSON file"))
            if not path:
                return
            baseline = load_run(Path(path))
            run.comparisons = compare_runs(run, baseline, *self.settings.thresholds)
            if self.session:
                self.session.baseline = baseline
                self.session.persist()
        await self.push_screen_wait(DiffScreen(run.comparisons))

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Compare runs")
    async def show_comparison(self) -> None:
        run = self.saved_run or (self.session.run if self.session else None)
        if run is None:
            return
        runs = [run]
        if self.settings.values.get("baseline"):
            path = Path(str(self.settings.values["baseline"]))
            exists = await asyncio.to_thread(path.exists)
            if exists:
                baseline = await asyncio.to_thread(load_run, path)
                runs.append(baseline)
        from llmsweep.tui.screens.main import ComparisonScreen

        await self.push_screen_wait(ComparisonScreen(runs))

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Export comparison")
    async def show_export_comparison(self, view: dict[str, Any]) -> None:
        directory = (
            self.session.store.directory(self.saved_run)
            if (self.session and self.saved_run)
            else Path.cwd()
        )
        value = await self.push_screen_wait(
            PathDialog(
                "Export comparison path (.json, .csv, or .md)", str(directory / "comparison.md")
            )
        )
        if not value:
            return
        path = Path(value)
        if path.suffix not in (".json", ".csv", ".md", ".markdown"):
            self.notify("Use a .json, .csv, or .md extension", severity="error")
            return
        from llmsweep.comparison import export_comparison

        export_comparison(
            view,
            json_path=path if path.suffix == ".json" else None,
            csv_path=path if path.suffix == ".csv" else None,
            markdown_path=path if path.suffix in (".md", ".markdown") else None,
            redact=self.redact,
        )
        self.notify(f"Exported {path}")

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Show coverage matrix")
    async def show_coverage(self) -> None:
        run = self.saved_run or (self.session.run if self.session else None)
        if run is None:
            return
        runs = [run]
        from llmsweep.tui.screens.main import CoverageScreen

        await self.push_screen_wait(CoverageScreen(runs))

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Show statistics")
    async def show_statistics(self) -> None:
        run = self.saved_run or (self.session.run if self.session else None)
        if run is None:
            return
        stats = run.statistics
        if not stats:
            import random

            from llmsweep.statistics import reliability_report

            samples = [
                {
                    "task_id": sample.task_id or sample.scenario,
                    "scenario": sample.scenario,
                    "status": sample.status,
                    "success": sample.success,
                    "total_s": sample.total_s,
                }
                for model in run.models
                for sample in model.samples
            ]
            stats = reliability_report(
                samples,
                adaptive=bool((run.repeat_policy or {}).get("adaptive")),
                rng=random.Random(run.run_id),
            )
            run.statistics = stats
        from llmsweep.tui.screens.main import StatisticsScreen

        await self.push_screen_wait(StatisticsScreen(stats))

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Export coverage")
    async def show_export_coverage(self, view: dict[str, Any]) -> None:
        directory = (
            self.session.store.directory(self.saved_run)
            if (self.session and self.saved_run)
            else Path.cwd()
        )
        value = await self.push_screen_wait(
            PathDialog(
                "Export coverage path (.json, .csv, or .md)", str(directory / "coverage.md")
            )
        )
        if not value:
            return
        path = Path(value)
        if path.suffix not in (".json", ".csv", ".md", ".markdown"):
            self.notify("Use a .json, .csv, or .md extension", severity="error")
            return
        from llmsweep.coverage import export_coverage

        export_coverage(
            view,
            json_path=path if path.suffix == ".json" else None,
            csv_path=path if path.suffix == ".csv" else None,
            markdown_path=path if path.suffix in (".md", ".markdown") else None,
            redact=self.redact,
        )
        self.notify(f"Exported {path}")

    @work(exit_on_error=False, exclusive=False, group="dialogs", description="Export results")
    async def show_export(self) -> None:
        run = self.saved_run or (self.session.run if self.session else None)
        if run is None:
            return
        directory = self.session.store.directory(run) if self.session else Path.cwd()
        value = await self.push_screen_wait(
            PathDialog("Export path (.json, .csv, or .md)", str(directory / "report.md"))
        )
        if not value:
            return
        path = Path(value)
        if path.suffix not in (".json", ".csv", ".md", ".markdown"):
            self.notify("Use a .json, .csv, or .md extension", severity="error")
            return
        export_run(
            run,
            json_path=path if path.suffix == ".json" else None,
            csv_path=path if path.suffix == ".csv" else None,
            markdown_path=path if path.suffix in (".md", ".markdown") else None,
            redact=self.redact,
            sort_by=self.settings.values["sort_by"],
        )
        self.notify(f"Exported {path}")

    async def on_unmount(self) -> None:
        if self.provider:
            await self.provider.close()
