"""Terminal navigation and presentation without benchmark execution logic."""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, ProgressBar, RichLog, Static

from llmsweep.errors import SelectionError
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.plain import display, ordered_models
from llmsweep.results import ModelResult, RunResult
from llmsweep.selection import filter_models, resolve_selection
from llmsweep.store import canonical_json
from llmsweep.tui.widgets.model_card import ModelCard

if TYPE_CHECKING:
    from llmsweep.tui.app import SweepApp


class SweepScreen(Screen[None]):
    """Base screen with a typed reference to the application controller."""

    @property
    def controller(self) -> SweepApp:
        return cast("SweepApp", self.app)


class PickerScreen(SweepScreen):
    """Select eligible models using the same resolver as command-line runs."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("slash", "search", "Search"),
        Binding("space", "select_model", "Select"),
        Binding("t", "tools", "Tools only"),
        Binding("e", "strict", "Known chat types"),
        Binding("ctrl+r", "run", "Run selected"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.models: list[ModelInfo] = []
        self.eligible: list[ModelInfo] = []
        self.selected: set[ModelRef] = set()
        self.visible_models: list[ModelInfo] = []
        self.tools_only = False
        self.strict_types = False

    def compose(self) -> ComposeResult:
        yield Static("llmsweep / local model benchmarks", classes="app-header")
        yield Static("LM STUDIO  /  MODEL SWEEP", classes="page-title")
        yield Input(placeholder="Search models (/)", id="model-search")
        yield DataTable(id="model-picker", cursor_type="row")
        yield Input(placeholder="Model IDs or indices: 1-3,5", id="model-selection")
        yield Static("Connecting to LM Studio…", id="selection-summary", markup=False)
        yield Button("Run selected", id="run-selected", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "Selected", "#", "Model", "B params", "Tools", "Loaded"
        )
        self.refresh_models()

    def set_models(self, models: list[ModelInfo]) -> None:
        self.models = models
        self.refresh_models()

    def refresh_models(self) -> None:
        self.eligible = filter_models(
            self.models, tools_only=self.tools_only, strict=self.strict_types
        )
        self.selected.intersection_update(m.ref for m in self.eligible)
        query = self.query_one("#model-search", Input).value.casefold()
        table = self.query_one(DataTable)
        table.clear()
        self.visible_models = []
        for index, model in enumerate(self.eligible, 1):
            if query not in model.ref.id.casefold():
                continue
            self.visible_models.append(model)
            table.add_row(
                "✓" if model.ref in self.selected else "",
                str(index),
                Text(model.ref.id),
                display(model.params_b),
                display(model.tool_use),
                "yes" if model.loaded else "no",
                key=model.ref.key,
            )
        self.summary()

    def summary(self, error: str | None = None) -> None:
        refs = [m.ref for m in self.eligible if m.ref in self.selected]
        text = error or (
            "Selected: " + ", ".join(ref.id for ref in refs)
            if refs
            else "Select models with Space or enter indices; Ctrl+R starts"
        )
        session = self.controller.session
        if refs and session:
            eta = session.store.estimate(refs, session.options.settings())
            if eta is not None:
                text += f" | previous-run ETA: {eta:.0f}s"
        self.query_one("#selection-summary", Static).update(Text(text))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model-search":
            self.refresh_models()
        elif event.input.id == "model-selection":
            try:
                self.selected = (
                    {m.ref for m in resolve_selection(event.value, self.eligible)}
                    if event.value.strip()
                    else set()
                )
                self.refresh_models()
            except SelectionError as exc:
                self.selected.clear()
                self.summary(str(exc))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_select_model()

    def action_select_model(self) -> None:
        table = self.query_one(DataTable)
        if self.visible_models and table.cursor_row < len(self.visible_models):
            ref = self.visible_models[table.cursor_row].ref
            if ref in self.selected:
                self.selected.remove(ref)
            else:
                self.selected.add(ref)
            cursor = table.cursor_row
            self.refresh_models()
            table.move_cursor(row=cursor)

    def action_search(self) -> None:
        self.query_one("#model-search", Input).focus()

    def action_tools(self) -> None:
        self.tools_only = not self.tools_only
        self.refresh_models()

    def action_strict(self) -> None:
        self.strict_types = not self.strict_types
        self.refresh_models()

    def action_run(self) -> None:
        models = [model for model in self.eligible if model.ref in self.selected]
        if not models:
            self.summary("Select at least one model")
            return
        self.controller.start_run(models)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-selected":
            self.action_run()


class LiveScreen(SweepScreen):
    """Display buffered progress for all active models."""

    ESCAPE_TO_MINIMIZE = False
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "app.cancel_run", "Cancel")]

    def __init__(
        self, results: list[ModelResult], previous: dict[ModelRef, ModelCard] | None = None
    ) -> None:
        super().__init__()
        self.results = results
        self.cards: dict[ModelRef, ModelCard] = {}
        self.previous = previous or {}

    def compose(self) -> ComposeResult:
        yield Static("llmsweep / local model benchmarks", classes="app-header")
        yield Static("BENCHMARK IN PROGRESS", classes="page-title")
        yield Static("Elapsed 0s", id="run-clock", markup=False)
        yield ProgressBar(total=len(self.results), show_eta=False, id="run-progress")
        with VerticalScroll(id="live-scroll"), Container(id="card-grid"):
            for result in self.results:
                card = ModelCard(result, self.controller.redact)
                previous = self.previous.get(result.model.ref)
                if previous:
                    card.output_text = previous.output_text
                    card.tool_rows = previous.timeline_history.copy()
                    card.timeline_history = previous.timeline_history.copy()
                    card.turn, card.scenario = previous.turn, previous.scenario
                    card.speeds.extend(previous.speeds)
                    card.live_speed = previous.live_speed
                self.cards[result.model.ref] = card
                yield card
        yield RichLog(id="verbose-log", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#verbose-log").display = self.controller.settings.values["verbose"]
        self.set_class(self.size.width < 110, "narrow")

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < 110, "narrow")


class ResultsScreen(SweepScreen):
    """Sort and inspect persisted model outcomes."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "sort", "Sort"),
        Binding("enter", "transcript", "Transcript"),
        Binding("d", "diff", "Baseline diff"),
        Binding("e", "export", "Export"),
        Binding("r", "rerun", "Rerun model"),
    ]
    SORTS = ("order", "tok_s", "ttft", "total", "load", "model")

    def __init__(self, run: RunResult, sort_by: str = "order") -> None:
        super().__init__()
        self.run = run
        self.sort_by = sort_by
        self.rows: list[ModelResult] = []

    def compose(self) -> ComposeResult:
        yield Static("llmsweep / local model benchmarks", classes="app-header")
        yield Static("RESULTS  /  LM STUDIO", classes="page-title")
        yield Static("Client-observed tok/s · mean of scenario means", id="results-description")
        yield DataTable(id="results-table", cursor_type="row")
        yield Static("", id="results-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "Model", "Status", "Pass rate", "tok/s", "TTFT ms", "Load s", "Source", "Error"
        )
        self.refresh_table()

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        self.rows = ordered_models(self.run, self.sort_by)
        for model in self.rows:
            summary = model.summary()
            table.add_row(
                *[
                    Text(display(v))
                    for v in [
                        model.model.ref.key,
                        model.status,
                        summary["success_rate"],
                        summary["tok_s"],
                        summary["ttft_ms"],
                        model.load_s,
                        summary["token_source"],
                        self.controller.redact(model.error or ""),
                    ]
                ],
                key=model.model.ref.key,
            )
        self.query_one("#results-description", Static).update(
            f"Client-observed tok/s · mean of scenario means · sort: {self.sort_by}"
        )
        self.show_details()

    def selected_model(self) -> ModelResult | None:
        row = self.query_one(DataTable).cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def show_details(self) -> None:
        model = self.selected_model()
        lines = []
        if model:
            for scenario, stats in model.rollups().items():
                for name in ("tok_s", "ttft_ms"):
                    value = stats[name]
                    lines.append(
                        f"{scenario} {name}: mean {display(value['mean'])}"
                        f" · median {display(value['median'])} · p95 {display(value['p95'])}"
                    )
            lines.extend(model.warnings)
            if model.contended:
                lines.append("Contended parallel measurement; no automatic performance verdict")
        self.query_one("#results-detail", Static).update(Text("\n".join(lines)))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_details()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_transcript()

    def action_sort(self) -> None:
        self.sort_by = self.SORTS[(self.SORTS.index(self.sort_by) + 1) % len(self.SORTS)]
        self.refresh_table()

    def action_transcript(self) -> None:
        model = self.selected_model()
        if model:
            self.controller.show_transcript(model)

    def action_diff(self) -> None:
        self.controller.show_diff()

    def action_export(self) -> None:
        self.controller.show_export()

    def action_rerun(self) -> None:
        model = self.selected_model()
        if model:
            self.controller.rerun_model(model.model.ref)


class TranscriptScreen(ModalScreen[None]):
    """Browse repeat transcripts with formatted tool JSON."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("n", "next", "Next repeat"),
        Binding("p", "previous", "Previous repeat"),
        Binding("y", "copy", "Copy"),
    ]

    def __init__(self, model: ModelResult, text_filter: object = None) -> None:
        super().__init__()
        self.model = model
        self.repeats = sorted({s.repeat for s in model.samples}) or [1]
        self.position = 0
        self.text = ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.model.model.ref.key, id="transcript-title", markup=False)
            yield RichLog(wrap=True, highlight=False, markup=False, id="transcript-body")
            yield Footer()

    def on_mount(self) -> None:
        self.refresh_transcript()

    def refresh_transcript(self) -> None:
        repeat = self.repeats[self.position]
        documents = []
        for sample in self.model.samples:
            if sample.repeat != repeat:
                continue
            messages = []
            for message in sample.messages:
                message = dict(message)
                if message.get("role") == "tool":
                    with contextlib.suppress(ValueError, TypeError):
                        message["content"] = json.loads(message["content"])
                messages.append(message)
            documents.append(
                {
                    "scenario": sample.scenario,
                    "repeat": repeat,
                    "messages": messages,
                    "output": sample.output,
                    "error": sample.error,
                }
            )
        app = cast("SweepApp", self.app)
        self.text = app.redact(canonical_json(documents))
        self.query_one("#transcript-title", Static).update(
            Text(f"{self.model.model.ref.key} · repeat {repeat}")
        )
        log = self.query_one(RichLog)
        log.clear()
        log.write(Text(self.text))

    def action_next(self) -> None:
        self.position = (self.position + 1) % len(self.repeats)
        self.refresh_transcript()

    def action_previous(self) -> None:
        self.position = (self.position - 1) % len(self.repeats)
        self.refresh_transcript()

    def action_copy(self) -> None:
        self.app.copy_to_clipboard(self.text)
        self.notify("Transcript copied")


class PathDialog(ModalScreen[str | None]):
    """Collect a filesystem path within a worker-owned modal."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, initial: str = "") -> None:
        super().__init__()
        self.title_text, self.initial = title, initial

    def compose(self) -> ComposeResult:
        with Vertical(classes="path-dialog"):
            yield Static(self.title_text, markup=False)
            yield Input(value=self.initial, id="dialog-path")
            yield Button("Continue", id="dialog-continue", variant="primary")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.query_one(Input).value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DiffScreen(ModalScreen[None]):
    """Present baseline verdicts with text as well as symbols."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss", "Close")]

    def __init__(self, comparisons: list[dict[str, object]]) -> None:
        super().__init__()
        self.comparisons = comparisons

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("BASELINE COMPARISON", classes="page-title")
            yield RichLog(wrap=True, markup=False)
            yield Footer()

    def on_mount(self) -> None:
        log = self.query_one(RichLog)
        for row in self.comparisons:
            verdict = (
                "▼ regression"
                if row["verdict"] == "regression"
                else (
                    "▲ within threshold"
                    if row["verdict"] == "within threshold"
                    else "not comparable"
                )
            )
            log.write(
                Text(f"{row['model']} / {row['scenario']}: {verdict}\n{row.get('reason') or ''}")
            )
