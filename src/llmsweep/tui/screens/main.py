"""Terminal navigation and presentation without benchmark execution logic."""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.dom import DOMNode
from textual.events import Resize
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, ProgressBar, RichLog, Static

from llmsweep.comparison import build_ascii_plot, build_views
from llmsweep.errors import SelectionError
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.plain import display, ordered_models
from llmsweep.results import ModelResult, RunResult
from llmsweep.selection import filter_models, resolve_selection
from llmsweep.store import canonical_json
from llmsweep.tui.widgets.chrome import StatusBar, TitleBar
from llmsweep.tui.widgets.model_card import STATUS_GLYPHS, ModelCard

if TYPE_CHECKING:
    from llmsweep.tui.app import SweepApp

HELP_LEGEND = """\
Metrics  tok/s is client-observed: output tokens ÷ generation window (first → last delta),
         excluding load, warmup, tool execution, and checker time
         TTFT: request dispatch → first output delta · Load: model load latency (n/a when the
         model was already loaded) · Source: usage (API token counts) or estimated (utf-8
         bytes ÷ 4); mixed sources block baseline comparison
Status   ○ pending  ● running  ✔ completed  ✖ error  ■ cancelled
         A wrong answer is a scoring failure: reported as completed with a lower pass rate,
         never as a model error
Exit     0 success · 1 model error · 2 config or setup · 3 regression only · 4 auth"""


def seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}s"


def status_tone(model: ModelResult, summary: dict[str, Any]) -> str:
    if model.status == "error":
        return "error"
    if model.status == "cancelled":
        return "warning"
    if model.status == "completed":
        return "warning" if summary["success"] is False else "success"
    return "primary"


class SweepScreen(Screen[None]):
    """Base screen with a typed reference to the application controller."""

    @property
    def controller(self) -> SweepApp:
        return cast("SweepApp", self.app)


class PickerScreen(SweepScreen):
    """Select eligible models using the same resolver as command-line runs."""

    AUTO_FOCUS = "#model-picker"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("slash", "search", "Search"),
        Binding("space", "select_model", "Select"),
        Binding("a", "select_all", "Select all"),
        Binding("x", "clear_selection", "Clear"),
        Binding("t", "tools", "Tools only"),
        Binding("e", "strict", "Known chat types"),
        Binding("ctrl+r", "run", "Run selected"),
        Binding("g", "guided_setup", "Guided setup"),
        Binding("escape", "focus_table", "", show=False),
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
        yield TitleBar("MODEL PICKER")
        yield Static(
            "Space toggles the highlighted row · a selects every visible model · x clears"
            " · Ctrl+R starts the sweep",
            classes="section-note",
            markup=False,
        )
        with Horizontal(id="picker-inputs"):
            yield Input(placeholder="type to narrow the list", id="model-search")
            yield Input(placeholder="1-3,5 or model ids", id="model-selection")
        yield DataTable(id="model-picker", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="picker-footer"):
            yield Static("Connecting to LM Studio…", id="selection-summary", markup=False)
            yield Button("Run selected", id="run-selected", variant="primary")
        yield StatusBar()
        yield Footer()

    def action_guided_setup(self) -> None:
        self.app.push_screen(SetupScreen())

    def on_mount(self) -> None:
        self.query_one("#model-search", Input).border_title = "Search  /"
        self.query_one("#model-selection", Input).border_title = "Select by index or id"
        self.query_one(DataTable).add_columns("✓", "#", "Model", "B params", "Tools", "Loaded")
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
        toggles = (("tools-only", self.tools_only), ("known types", self.strict_types))
        filters = [name for name, active in toggles if active]
        self.controller.update_status(
            context=(
                f"{len(self.visible_models)} of {len(self.eligible)} models"
                f" · {len(self.selected)} selected"
            ),
            filter=", ".join(filters) or None,
            run=None,
            sort=None,
            clock=None,
            hint=None,
        )

    def refresh_keeping_cursor(self) -> None:
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        self.refresh_models()
        table.move_cursor(row=cursor)

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
                text += f" · previous-run ETA {eta:.0f}s"
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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_focus_table()

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
            self.refresh_keeping_cursor()

    def action_select_all(self) -> None:
        self.selected.update(model.ref for model in self.visible_models)
        self.refresh_keeping_cursor()

    def action_clear_selection(self) -> None:
        self.selected.clear()
        self.refresh_keeping_cursor()

    def action_search(self) -> None:
        self.query_one("#model-search", Input).focus()

    def action_focus_table(self) -> None:
        self.query_one(DataTable).focus()

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

    AUTO_FOCUS = "ModelCard"
    ESCAPE_TO_MINIMIZE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.cancel_run", "Cancel"),
        Binding("z", "zoom", "Zoom card"),
        Binding("right_square_bracket", "next_card", "Next card", key_display="]"),
        Binding("left_square_bracket", "previous_card", "Prev card", key_display="["),
    ]

    def __init__(
        self, results: list[ModelResult], previous: dict[ModelRef, ModelCard] | None = None
    ) -> None:
        super().__init__()
        self.results = results
        self.cards: dict[ModelRef, ModelCard] = {}
        self.previous = previous or {}
        self.zoomed = False

    def compose(self) -> ComposeResult:
        yield TitleBar("BENCHMARK IN PROGRESS")
        with Horizontal(id="run-header"):
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
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#verbose-log").display = self.controller.settings.values["verbose"]
        self.set_class(self.size.width < 110, "narrow")
        self.controller.update_status(
            context=f"{len(self.results)} models", run="starting", sort=None, filter=None, hint=None
        )

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < 110, "narrow")

    def current_card(self) -> ModelCard | None:
        node: DOMNode | None = self.focused
        while node is not None and not isinstance(node, ModelCard):
            node = node.parent
        if isinstance(node, ModelCard):
            return node
        return next(iter(self.cards.values()), None)

    def focus_card(self, card: ModelCard) -> None:
        for other in self.cards.values():
            other.set_class(self.zoomed and other is card, "zoom-target")
        card.focus()
        card.scroll_visible()
        self.controller.update_status(
            hint=f"zoomed on {card.result.model.ref.key} (z restores)" if self.zoomed else None
        )

    def step_card(self, offset: int) -> None:
        cards = list(self.cards.values())
        current = self.current_card()
        if current is None:
            return
        self.focus_card(cards[(cards.index(current) + offset) % len(cards)])

    def action_next_card(self) -> None:
        self.step_card(1)

    def action_previous_card(self) -> None:
        self.step_card(-1)

    def action_zoom(self) -> None:
        card = self.current_card()
        if card is None:
            return
        self.zoomed = not self.zoomed
        self.set_class(self.zoomed, "zoomed")
        self.focus_card(card)


class ResultsScreen(SweepScreen):
    """Sort, filter, and inspect persisted model outcomes."""

    AUTO_FOCUS = "#results-table"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "sort", "Sort"),
        Binding("slash", "filter", "Filter"),
        Binding("f", "status_filter", "Status filter"),
        Binding("enter", "transcript", "Transcript"),
        Binding("d", "diff", "Baseline diff"),
        Binding("c", "compare", "Compare"),
        Binding("e", "export", "Export"),
        Binding("r", "rerun", "Rerun model"),
        Binding("escape", "focus_table", "", show=False),
    ]
    SORTS = ("order", "tok_s", "ttft", "total", "load", "model")
    STATUS_FILTERS = ("all", "passed", "failed", "errors")
    COLUMNS: ClassVar[tuple[tuple[str, str | None], ...]] = (
        ("Model", "model"),
        ("Status", None),
        ("Pass rate", None),
        ("tok/s", "tok_s"),
        ("TTFT ms", "ttft"),
        ("Load s", "load"),
        ("Total s", "total"),
        ("Source", None),
        ("Error", None),
    )

    def __init__(self, run: RunResult, sort_by: str = "order") -> None:
        super().__init__()
        self.run = run
        self.sort_by = sort_by
        self.status_filter = "all"
        self.needle = ""
        self.rows: list[ModelResult] = []

    def compose(self) -> ComposeResult:
        yield TitleBar("RESULTS")
        yield Input(placeholder="model, status, or error text", id="results-filter")
        yield Static("", id="results-description", markup=False)
        yield DataTable(id="results-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="results-detail", markup=False)
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#results-filter", Input).border_title = "Filter  /"
        self.refresh_table()

    def matches(self, model: ModelResult, summary: dict[str, Any]) -> bool:
        success = summary["success"]
        if self.status_filter == "passed" and (model.status != "completed" or success is not True):
            return False
        if self.status_filter == "failed" and success is not False:
            return False
        if (
            self.status_filter == "errors"
            and model.status not in ("error", "cancelled")
            and not model.error
        ):
            return False
        if not self.needle:
            return True
        haystack = " ".join([model.model.ref.key, model.status, model.error or ""]).casefold()
        return self.needle in haystack

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        previous = self.selected_model()
        table.clear(columns=True)
        for label, key in self.COLUMNS:
            marker = "" if key != self.sort_by else (" ▼" if key == "tok_s" else " ▲")
            table.add_column(label + marker, key=label)
        tone = self.controller.tone
        counts = {"passed": 0, "failed": 0, "errors": 0}
        self.rows = []
        for model in ordered_models(self.run, self.sort_by):
            summary = model.summary()
            if model.status in ("error", "cancelled") or model.error:
                counts["errors"] += 1
            elif summary["success"] is True:
                counts["passed"] += 1
            elif summary["success"] is False:
                counts["failed"] += 1
            if not self.matches(model, summary):
                continue
            self.rows.append(model)
            rate = summary["success_rate"]
            rate_tone = (
                ""
                if rate is None
                else tone("success" if rate == 1 else "error" if rate == 0 else "warning")
            )
            table.add_row(
                Text(model.model.ref.key),
                Text(
                    f"{STATUS_GLYPHS.get(model.status, '·')} {model.status}",
                    style=tone(status_tone(model, summary)),
                ),
                Text(display(rate), style=rate_tone),
                Text(display(summary["tok_s"])),
                Text(display(summary["ttft_ms"])),
                Text(display(model.load_s)),
                Text(display(model.total_s)),
                Text(display(summary["token_source"])),
                Text(
                    display(self.controller.redact(model.error or "")),
                    style=tone("error") if model.error else "",
                ),
                key=model.model.ref.key,
            )
        if previous is not None:
            keys = [m.model.ref.key for m in self.rows]
            if previous.model.ref.key in keys:
                table.move_cursor(row=keys.index(previous.model.ref.key))
        total = len(self.run.models)
        self.query_one("#results-description", Static).update(
            Text(
                f"{total} models · {counts['passed']} passed · {counts['failed']} failed"
                f" · {counts['errors']} errors · tok/s is client-observed, mean of scenario means"
            )
        )
        filters = [] if self.status_filter == "all" else [self.status_filter]
        if self.needle:
            filters.append(f'"{self.needle}"')
        self.controller.update_status(
            context=f"{len(self.rows)} of {total} models",
            run=f"run {self.run.status}",
            sort=f"sort {self.sort_by}",
            filter=" ".join(filters) or None,
            clock=None,
            hint=None,
        )
        self.show_details()

    def selected_model(self) -> ModelResult | None:
        row = self.query_one(DataTable).cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def show_details(self) -> None:
        model = self.selected_model()
        lines = []
        if model:
            lines.append(
                f"{model.model.ref.key} · load {seconds(model.load_s)} ({model.load_status})"
                f" · warmup {seconds(model.warmup_s)} · total {seconds(model.total_s)}"
            )
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "results-filter":
            self.needle = event.value.strip().casefold()
            self.refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_focus_table()

    def action_sort(self) -> None:
        self.sort_by = self.SORTS[(self.SORTS.index(self.sort_by) + 1) % len(self.SORTS)]
        self.refresh_table()

    def action_filter(self) -> None:
        self.query_one("#results-filter", Input).focus()

    def action_status_filter(self) -> None:
        options = self.STATUS_FILTERS
        self.status_filter = options[(options.index(self.status_filter) + 1) % len(options)]
        self.refresh_table()

    def action_focus_table(self) -> None:
        self.query_one(DataTable).focus()

    def action_transcript(self) -> None:
        model = self.selected_model()
        if model:
            self.controller.show_transcript(model)

    def action_diff(self) -> None:
        self.controller.show_diff()

    def action_compare(self) -> None:
        self.controller.show_comparison()

    def action_export(self) -> None:
        self.controller.show_export()
    def action_rerun(self) -> None:
        model = self.selected_model()
        if model:
            self.controller.rerun_model(model.model.ref)


class ComparisonScreen(SweepScreen):
    """Quality-speed comparison dashboard comparing correctness, responsiveness, and reliability."""

    AUTO_FOCUS = "#compare-table"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("v", "toggle_view", "Toggle view"),
        Binding("e", "export", "Export"),
        Binding("slash", "filter_target", "Filter"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, runs: list[RunResult], default_metric: str = "task_duration_s") -> None:
        super().__init__()
        self.runs = runs
        self.metric = default_metric
        self.filter_target = ""
        self.filter_benchmark = ""
        self.filter_task = ""
        self.filter_config = ""
        self.view: dict[str, Any] = {}
        self.points: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield TitleBar("QUALITY-SPEED COMPARISON")
        with Horizontal(id="compare-filter-bar"):
            yield Input(placeholder="target (model)", id="compare-filter-target")
            yield Input(placeholder="benchmark", id="compare-filter-benchmark")
            yield Input(placeholder="task", id="compare-filter-task")
            yield Input(placeholder="config", id="compare-filter-config")
        with Horizontal(id="compare-toolbar"):
            yield Button("View: Duration", id="compare-toggle-button", variant="primary")
            yield Button("Export", id="compare-export-button")
            yield Static("", id="compare-description", markup=False)
        with VerticalScroll(id="compare-content"):
            yield Static("", id="compare-plot", markup=False)
            yield DataTable(id="compare-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="compare-detail", markup=False)
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#compare-filter-target", Input).border_title = "Target  /"
        self.query_one("#compare-filter-benchmark", Input).border_title = "Benchmark"
        self.query_one("#compare-filter-task", Input).border_title = "Task / Suite"
        self.query_one("#compare-filter-config", Input).border_title = "Config"
        self.refresh_views()

    def refresh_views(self) -> None:
        self.view = build_views(
            self.runs,
            target=self.filter_target or None,
            benchmark=self.filter_benchmark or None,
            task=self.filter_task or None,
            configuration=self.filter_config or None,
        )
        self.points = self.view.get("points", [])

        metric_label = (
            "Task Duration (s)" if self.metric == "task_duration_s" else "Throughput (tok/s)"
        )
        toggle_btn = self.query_one("#compare-toggle-button", Button)
        toggle_btn.label = f"View: {metric_label}"

        total_pts = len(self.points)
        desc_text = f"{len(self.runs)} run(s) · {total_pts} comparison point(s) · {metric_label}"
        self.query_one("#compare-description", Static).update(Text(desc_text))

        plot_str = build_ascii_plot(self.points, self.metric)
        self.query_one("#compare-plot", Static).update(Text(plot_str))

        table = self.query_one("#compare-table", DataTable)
        previous_row = table.cursor_row
        table.clear(columns=True)
        table.add_columns(
            "Run",
            "Target",
            "Kind",
            "Benchmark",
            "Success Rate [95% CI]",
            metric_label,
            "Samples",
            "Coverage",
            "Timing",
        )
        tone = self.controller.tone
        for p in self.points:
            rate = p.get("success_rate")
            ci = p.get("confidence_interval")
            if rate is not None:
                ci_part = f" [{ci[0]:.0%}-{ci[1]:.0%}]" if ci else ""
                rate_text = f"{rate:.1%}{ci_part}"
                rate_tone = (
                    tone("success")
                    if rate == 1.0
                    else tone("error")
                    if rate == 0.0
                    else tone("warning")
                )
            else:
                rate_text = "n/a"
                rate_tone = ""

            metric_val = p.get(self.metric)
            metric_text = f"{metric_val:.2f}" if metric_val is not None else "n/a"

            samples_text = f"{p.get('scored', 0)}/{p.get('samples', 0)}"
            cov = p.get("coverage")
            cov_text = f"{cov:.0%}" if cov is not None else "n/a"
            if p.get("incomplete"):
                cov_text += " (inc)"

            timing_comparable = p.get("timing_comparable")
            timing_text = "comparable" if timing_comparable else "incompatible"
            timing_tone = tone("success") if timing_comparable else tone("warning")

            table.add_row(
                Text(str(p.get("run_id", ""))[:12]),
                Text(str(p.get("target", ""))),
                Text(str(p.get("target_kind", ""))),
                Text(str(p.get("benchmark", ""))),
                Text(rate_text, style=rate_tone),
                Text(metric_text),
                Text(samples_text),
                Text(cov_text),
                Text(timing_text, style=timing_tone),
                key=f"{p.get('run_id')}_{p.get('target')}_{p.get('benchmark')}",
            )

        if 0 <= previous_row < len(self.points):
            table.move_cursor(row=previous_row)

        self.controller.update_status(
            context=f"{len(self.runs)} runs",
            view=f"metric {self.metric}",
            filter=f"points {total_pts}" if total_pts else "no matches",
            clock=None,
            hint="v: toggle view  e: export  esc: back",
        )
        self.show_details()

    def selected_point(self) -> dict[str, Any] | None:
        table = self.query_one("#compare-table", DataTable)
        row = table.cursor_row
        return self.points[row] if 0 <= row < len(self.points) else None

    def show_details(self) -> None:
        p = self.selected_point()
        if not p:
            self.query_one("#compare-detail", Static).update(Text(""))
            return
        lines = [
            f"Run: {p['run_id']} · Target: {p['target']} ({p['target_kind']})"
            f" · Benchmark: {p['benchmark']}"
        ]
        ci = p.get("confidence_interval")
        ci_str = f"[{ci[0]:.1%}, {ci[1]:.1%}]" if ci else "n/a"
        rate_str = f"{p['success_rate']:.1%}" if p.get("success_rate") is not None else "n/a"
        lines.append(f"Success rate: {rate_str} · Wilson 95% CI: {ci_str}")
        dur_str = (
            f"{p['task_duration_s']:.2f}s"
            if p.get("task_duration_s") is not None
            else "n/a (unavailable)"
        )
        tok_str = f"{p['tok_s']:.2f} tok/s" if p.get("tok_s") is not None else "n/a (unavailable)"
        lines.append(f"Task duration: {dur_str} · Throughput: {tok_str}")
        lines.append(
            f"Samples: {p['scored']} scored / {p['samples']} planned · Failures: {p['failures']}"
            f" · Errors: {p['errors']} · Skipped: {p['skipped']} · Cancelled: {p['cancelled']}"
        )
        cov_str = f"{p['coverage']:.1%}" if p.get("coverage") is not None else "n/a"
        lines.append(
            f"Coverage: {cov_str} ({'incomplete coverage' if p['incomplete'] else 'complete'})"
        )
        if not p.get("timing_comparable"):
            lines.append(
                "Timing: Incompatible configuration/provenance; automatic timing verdicts disabled."
            )
        else:
            lines.append("Timing: Configuration compatible for timing comparisons.")
        self.query_one("#compare-detail", Static).update(Text("\n".join(lines)))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_details()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compare-toggle-button":
            self.action_toggle_view()
        elif event.button.id == "compare-export-button":
            self.action_export()

    def on_input_changed(self, event: Input.Changed) -> None:
        val = event.value.strip()
        if event.input.id == "compare-filter-target":
            self.filter_target = val
        elif event.input.id == "compare-filter-benchmark":
            self.filter_benchmark = val
        elif event.input.id == "compare-filter-task":
            self.filter_task = val
        elif event.input.id == "compare-filter-config":
            self.filter_config = val
        self.refresh_views()

    def action_toggle_view(self) -> None:
        self.metric = "tok_s" if self.metric == "task_duration_s" else "task_duration_s"
        self.refresh_views()

    def action_filter_target(self) -> None:
        self.query_one("#compare-filter-target", Input).focus()

    def action_export(self) -> None:
        self.controller.show_export_comparison(self.view)

    def action_back(self) -> None:
        self.app.pop_screen()


class HelpScreen(ModalScreen[None]):
    """Overlay listing the bindings active beneath it plus the metric and status legend."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("f1", "dismiss", "Close", show=False),
        Binding("question_mark", "dismiss", "Close", show=False),
    ]

    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog help-dialog"):
            yield DataTable(id="help-bindings", cursor_type="row", zebra_stripes=True)
            yield Static(HELP_LEGEND, id="help-legend", markup=False)
            yield Footer()

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "KEYBOARD SHORTCUTS & LEGEND"
        table = self.query_one(DataTable)
        table.add_columns("Key", "Action", "Scope")
        for key, action, scope in self.rows:
            table.add_row(Text(key, style="bold"), action, scope)


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
        self.query_one(".dialog").border_title = "TRANSCRIPT"
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
            Text(
                f"{self.model.model.ref.key} · repeat {repeat} of {len(self.repeats)}"
                " · n/p switch repeats · y copies JSON"
            )
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
        self.query_one(".path-dialog").border_title = "PATH"
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
            yield RichLog(wrap=True, markup=False)
            yield Footer()

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "BASELINE COMPARISON"
        tone = cast("SweepApp", self.app).tone
        log = self.query_one(RichLog)
        for row in self.comparisons:
            if row["verdict"] == "regression":
                verdict, style = "▼ regression", tone("error")
            elif row["verdict"] == "within threshold":
                verdict, style = "▲ within threshold", tone("success")
            else:
                verdict, style = "not comparable", ""
            log.write(
                Text.assemble(
                    (f"{row['model']} / {row['scenario']}: ", "bold"),
                    (verdict, style),
                    f"\n{row.get('reason') or ''}",
                )
            )


class SetupScreen(Screen[None]):
    """Review the resolved preset and execution policy before a run starts."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Static("", id="setup-plan", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        from llmsweep.benchmarks.presets import plan_report

        app = cast("SweepApp", self.app)
        self.query_one("#setup-plan", Static).update(plan_report(app.settings.values))

    def action_back(self) -> None:
        self.app.pop_screen()
