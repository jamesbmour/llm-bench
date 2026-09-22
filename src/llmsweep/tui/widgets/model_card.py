"""Coalesced streaming output, tool timeline, and throughput sparkline."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, RichLog, Sparkline, Static

from llmsweep.results import ModelResult
from llmsweep.runner import RunEvent
from llmsweep.security import Redactor

STATUS_GLYPHS: dict[str, str] = {
    "pending": "○",
    "running": "●",
    "completed": "✔",
    "error": "✖",
    "cancelled": "■",
}


class ModelCard(Vertical):
    """Present one model using a tick-flushed stream buffer."""

    can_focus = True
    STATUSES: ClassVar[tuple[str, ...]] = tuple(STATUS_GLYPHS)

    def __init__(self, result: ModelResult, redact: Redactor) -> None:
        super().__init__()
        self.result = result
        self.redact = redact
        self.output_text = ""
        self.rendered_text = ""
        self.buffer: list[str] = []
        self.tool_rows: list[tuple[str, str, str]] = []
        self.timeline_history: list[tuple[str, str, str]] = []
        self.turn = 0
        self.scenario = "pending"
        self.updates = 0
        self.speeds: deque[float] = deque(maxlen=80)
        self.live_speed: float | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-status"):
            yield Static("○ pending", classes="card-state", markup=False)
            yield Static("", classes="card-progress", markup=False)
        yield Static("", classes="card-metrics", markup=False)
        yield RichLog(wrap=True, highlight=False, markup=False, classes="stream-pane")
        yield Sparkline([], classes="speed-graph")
        yield DataTable(classes="tool-timeline", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.border_title = self.result.model.ref.key
        self.query_one(DataTable).add_columns("Turn", "Tool", "Result")

    def accept(self, event: RunEvent) -> None:
        if event.kind in ("text", "reasoning"):
            self.buffer.append(event.text)
            self.live_speed = event.tok_s
        elif event.kind == "scenario_start":
            self.scenario = event.scenario
            self.buffer.append(f"\n── {event.scenario} ──\n")
        elif event.kind == "turn":
            self.turn = event.turn
            self.live_speed = None
        elif event.kind == "tool":
            parsed = json.loads(event.text)
            state = "error" if "error" in parsed["result"] else "ok"
            row = (str(event.turn), event.tool, state)
            self.tool_rows.append(row)
            self.timeline_history.append(row)

    def flush(self, elapsed: float) -> None:
        if self.buffer:
            self.output_text += "".join(self.buffer)
            self.buffer.clear()
        clean = self.redact(self.output_text)
        reserve = max((len(key) for key in self.redact.secrets), default=0)
        if reserve and self.result.status == "running":
            clean = clean[:-reserve] if len(clean) > reserve else ""
        if not self.is_mounted or not self.query(DataTable):
            return
        # Only mark text as rendered once it has reached a mounted pane, so deltas that
        # arrive before mount (or text carried over from a rerun) are still drawn.
        if clean != self.rendered_text:
            self.rendered_text = clean
            log = self.query_one(RichLog)
            log.clear()
            log.write(Text(clean))
            self.updates += 1
        timeline = self.query_one(DataTable)
        while self.tool_rows:
            timeline.add_row(*self.tool_rows.pop(0))
        summary = self.result.summary()
        speed = summary["tok_s"]
        # During a turn counts are estimates; final reported throughput comes from the runner.
        if self.result.status == "running":
            speed = self.live_speed
        if speed is not None:
            self.speeds.append(speed)
            self.query_one(Sparkline).data = list(self.speeds)
        status = self.result.status
        for name in self.STATUSES:
            self.set_class(status == name, f"status-{name}")
        self.query_one(".card-state", Static).update(
            Text(f"{STATUS_GLYPHS.get(status, '·')} {status}")
        )
        error = f" · {self.redact(self.result.error)}" if self.result.error else ""
        self.query_one(".card-progress", Static).update(
            Text(f"{self.scenario} · turn {self.turn} · {elapsed:.1f}s{error}")
        )
        self.query_one(".card-metrics", Static).update(Text(self.metrics_line(summary, speed)))

    def metrics_line(self, summary: dict[str, Any], speed: float | None) -> str:
        ttft = summary["ttft_ms"]
        parts = [
            "tok/s " + (f"{speed:.1f}" if speed is not None else "n/a"),
            "ttft " + (f"{ttft:.0f} ms" if ttft is not None else "n/a"),
            f"tokens {summary['output_tokens']}",
            f"tools {len(self.timeline_history)}",
        ]
        if summary["token_source"]:
            parts.append(str(summary["token_source"]))
        return " · ".join(parts)
