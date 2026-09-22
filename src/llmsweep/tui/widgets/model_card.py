from __future__ import annotations

import json
from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, RichLog, Sparkline, Static

from llmsweep.results import ModelResult
from llmsweep.runner import RunEvent
from llmsweep.security import Redactor


class ModelCard(Vertical):
    def __init__(self, result: ModelResult, redact: Redactor) -> None:
        super().__init__()
        self.result = result
        self.redact = redact
        self.output_text = ""
        self.rendered_text = ""
        self.buffer: list[str] = []
        self.tool_rows: list[tuple[str, str, str]] = []
        self.turn = 0
        self.scenario = "pending"
        self.updates = 0
        self.speeds: deque[float] = deque(maxlen=80)
        self.live_speed: float | None = None

    def compose(self) -> ComposeResult:
        yield Static(self.result.model.ref.key, classes="card-title", markup=False)
        yield Static("Pending", classes="card-status", markup=False)
        yield RichLog(wrap=True, highlight=False, markup=False, classes="stream-pane")
        yield Sparkline([], classes="speed-graph")
        yield DataTable(classes="tool-timeline", cursor_type="row")

    def on_mount(self) -> None:
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
            self.tool_rows.append((str(event.turn), event.tool, state))

    def flush(self, elapsed: float) -> None:
        changed = bool(self.buffer)
        if changed:
            self.output_text += "".join(self.buffer)
            self.buffer.clear()
            clean = self.redact(self.output_text)
            reserve = max((len(key) for key in self.redact.secrets), default=0)
            if reserve and self.result.status == "running":
                clean = clean[:-reserve] if len(clean) > reserve else ""
            self.rendered_text = clean
        if not self.is_mounted or not self.query(DataTable):
            return
        if changed:
            log = self.query_one(RichLog)
            log.clear()
            log.write(Text(self.rendered_text))
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
        error = f" | {self.result.error}" if self.result.error else ""
        self.query_one(".card-status", Static).update(
            Text(
                f"{self.result.status} | {self.scenario} | turn {self.turn} | {elapsed:.1f}s{error}"
            )
        )
