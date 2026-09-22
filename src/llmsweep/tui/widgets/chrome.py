"""Screen chrome shared by every view: the title bar and the status bar."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

if TYPE_CHECKING:
    from llmsweep.tui.app import SweepApp


class TitleBar(Horizontal):
    """One-line header: product mark, screen name, and the endpoint or run being viewed."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Static("llmsweep", classes="title-brand", markup=False)
        yield Static(f"/  {self.title_text}", classes="title-screen", markup=False)
        yield Static("", classes="title-context", markup=False)

    def on_mount(self) -> None:
        context = cast("SweepApp", self.app).context_label()
        self.query_one(".title-context", Static).update(Text(context))


class StatusBar(Horizontal):
    """Persistent context strip: fixed-order segments on the left, theme and clock on the right."""

    LEFT: ClassVar[tuple[str, ...]] = ("context", "run", "sort", "filter", "hint")
    RIGHT: ClassVar[tuple[str, ...]] = ("theme", "clock")
    SEPARATOR = "  ·  "

    def __init__(self) -> None:
        super().__init__()
        self.shown: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Static("", classes="status-left", markup=False)
        yield Static("", classes="status-right", markup=False)

    def on_mount(self) -> None:
        self.show(cast("SweepApp", self.app).status)

    def show(self, status: dict[str, str]) -> None:
        left = self.SEPARATOR.join(status[key] for key in self.LEFT if key in status)
        right = self.SEPARATOR.join(status[key] for key in self.RIGHT if key in status)
        if self.shown == (left, right):
            return
        self.shown = (left, right)
        self.query_one(".status-left", Static).update(Text(left))
        self.query_one(".status-right", Static).update(Text(right))
