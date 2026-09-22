"""Incremental server-sent-event decoding and OpenAI chat-chunk normalization.

Two layers live here, both pure and I/O free:

``SseDecoder``
    Byte-oriented, resumable SSE framing. It tolerates chunk boundaries falling
    anywhere -- mid-line, mid-CRLF, or mid-UTF-8-sequence -- and follows the
    SSE rules for comments, field parsing, and multi-line ``data`` payloads.
    A line that is not an SSE field (for example a bare NDJSON object) carries
    no ``data`` field, so it contributes nothing.

``ChatStreamParser``
    Turns decoded payloads into normalized :data:`StreamEvent` values. Only
    content, reasoning, and tool-call fragments count as *output*; role-only
    headers and usage packets do not, which is what keeps time-to-first-token
    honest.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .errors import StreamProtocolError

__all__ = [
    "DONE_SENTINEL",
    "ChatStreamParser",
    "FinishEvent",
    "ReasoningDelta",
    "SseDecoder",
    "SsePacket",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolCallAccumulator",
    "ToolCallDelta",
    "UsageEvent",
    "is_output_delta",
]

DONE_SENTINEL = "[DONE]"


# --------------------------------------------------------------------------
# SSE framing
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SsePacket:
    """One dispatched SSE event: its ``event`` name and joined ``data`` payload."""

    data: str
    event: str | None = None

    @property
    def is_done(self) -> bool:
        return self.data == DONE_SENTINEL


class SseDecoder:
    """Resumable SSE framing over an arbitrary byte stream.

    Feed byte chunks of any size, in any alignment; each call yields the events
    that became complete. Call :meth:`close` at end of stream to flush a final
    unterminated event.
    """

    __slots__ = ("_buffer", "_data", "_decoder", "_event_name", "_finished")

    def __init__(self) -> None:
        # ``replace`` keeps a truncated stream from masking the real failure
        # (a disconnect) behind a decoding error.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""
        self._data: list[str] = []
        self._event_name: str | None = None
        self._finished = False

    def feed(self, chunk: bytes) -> Iterator[SsePacket]:
        """Decode ``chunk`` and yield every event it completed."""
        if self._finished:
            raise StreamProtocolError("cannot feed an SSE decoder after close()")
        self._buffer += self._decoder.decode(chunk)
        yield from self._drain(final=False)

    def close(self) -> Iterator[SsePacket]:
        """Flush the decoder at end of stream and yield any trailing event."""
        if self._finished:
            return
        self._buffer += self._decoder.decode(b"", True)
        self._finished = True
        yield from self._drain(final=True)
        if self._data:
            packet = self._dispatch()
            if packet is not None:
                yield packet

    def _drain(self, *, final: bool) -> Iterator[SsePacket]:
        for line in self._pop_lines(final=final):
            packet = self._handle_line(line)
            if packet is not None:
                yield packet

    def _pop_lines(self, *, final: bool) -> list[str]:
        """Split buffered text on LF, CRLF, or CR, holding back ambiguous tails."""
        lines: list[str] = []
        while True:
            lf = self._buffer.find("\n")
            cr = self._buffer.find("\r")
            if lf == -1 and cr == -1:
                break
            if cr != -1 and (lf == -1 or cr < lf):
                # A trailing CR may be the first half of a CRLF still in flight.
                if cr == len(self._buffer) - 1 and not final:
                    break
                end, skip = cr, 2 if self._buffer[cr + 1 : cr + 2] == "\n" else 1
            else:
                end, skip = lf, 1
            lines.append(self._buffer[:end])
            self._buffer = self._buffer[end + skip :]
        if final and self._buffer:
            lines.append(self._buffer)
            self._buffer = ""
        return lines

    def _handle_line(self, line: str) -> SsePacket | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):  # comment / keep-alive
            return None
        name, sep, value = line.partition(":")
        if not sep:
            # A field name with no value; none of the fields we honor are
            # meaningful when empty, so there is nothing to record.
            return None
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event_name = value
        # ``id``, ``retry``, and unknown fields (including NDJSON lines, whose
        # "field name" is a JSON fragment) are deliberately ignored.
        return None

    def _dispatch(self) -> SsePacket | None:
        if not self._data:
            self._event_name = None
            return None
        packet = SsePacket(data="\n".join(self._data), event=self._event_name)
        self._data = []
        self._event_name = None
        return packet


# --------------------------------------------------------------------------
# Normalized chat events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A fragment of assistant-visible content."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """A fragment of reasoning / thinking output."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """A fragment of one indexed tool call.

    ``arguments`` is kept as the raw string the server sent; fragments are only
    parsed once the whole call has been assembled.
    """

    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """A usage packet. These often arrive with an empty ``choices`` list."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class FinishEvent:
    """A choice reported a finish reason."""

    reason: str | None = None


StreamEvent = TextDelta | ReasoningDelta | ToolCallDelta | UsageEvent | FinishEvent

_OUTPUT_EVENTS = (TextDelta, ReasoningDelta, ToolCallDelta)


def is_output_delta(event: StreamEvent) -> bool:
    """True for events that represent generated output.

    Usage packets, finish markers, and role-only headers (which produce no
    event at all) are excluded, so TTFT and the generation window measure only
    real output.
    """
    return isinstance(event, _OUTPUT_EVENTS)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call assembled from its fragments, with arguments still raw."""

    index: int
    call_id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> dict[str, Any]:
        """Decode ``arguments``; raises :class:`ValueError` when malformed."""
        decoded = json.loads(self.arguments or "{}")
        if not isinstance(decoded, dict):
            raise ValueError("tool call arguments must decode to an object")
        return decoded


@dataclass
class ToolCallAccumulator:
    """Assembles indexed tool-call fragments in arrival order.

    Argument strings are concatenated verbatim and never parsed here: a partial
    call is not valid JSON, and the runner needs the raw text to report
    malformed arguments faithfully.
    """

    _slots: dict[int, dict[str, Any]] = field(default_factory=dict)
    _order: list[int] = field(default_factory=list)

    def add(self, delta: ToolCallDelta) -> None:
        slot = self._slots.get(delta.index)
        if slot is None:
            slot = {"id": None, "name": None, "arguments": ""}
            self._slots[delta.index] = slot
            self._order.append(delta.index)
        if delta.call_id:
            slot["id"] = delta.call_id
        if delta.name:
            slot["name"] = str(slot["name"] or "") + delta.name
        if delta.arguments:
            slot["arguments"] = str(slot["arguments"]) + delta.arguments

    def __bool__(self) -> bool:
        return bool(self._slots)

    def finish(self, *, turn: int = 0) -> list[ToolCall]:
        """Return the assembled calls, synthesizing ids the server omitted."""
        calls: list[ToolCall] = []
        for position, index in enumerate(self._order):
            slot = self._slots[index]
            calls.append(
                ToolCall(
                    index=index,
                    call_id=str(slot["id"] or f"call_{turn}_{position}"),
                    name=str(slot["name"] or ""),
                    arguments=str(slot["arguments"]),
                )
            )
        return calls


class ChatStreamParser:
    """Normalizes OpenAI-shaped chat-completion chunks into stream events."""

    __slots__ = ("_decoder", "_done")

    def __init__(self) -> None:
        self._decoder = SseDecoder()
        self._done = False

    @property
    def saw_done(self) -> bool:
        """Whether the server sent the ``[DONE]`` sentinel."""
        return self._done

    def feed(self, chunk: bytes) -> Iterator[StreamEvent]:
        for packet in self._decoder.feed(chunk):
            yield from self._parse(packet)

    def close(self) -> Iterator[StreamEvent]:
        for packet in self._decoder.close():
            yield from self._parse(packet)

    def _parse(self, packet: SsePacket) -> Iterator[StreamEvent]:
        if self._done:
            return
        if packet.is_done:
            self._done = True
            return
        payload = packet.data.strip()
        if not payload:
            return
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise StreamProtocolError(
                f"malformed SSE data payload: {exc.msg} at position {exc.pos}"
            ) from None
        if not isinstance(obj, dict):
            raise StreamProtocolError("SSE data payload must be a JSON object")
        yield from _events_from_chunk(obj)


def _events_from_chunk(obj: dict[str, Any]) -> Iterator[StreamEvent]:
    error = obj.get("error")
    if error is not None:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise StreamProtocolError(f"provider reported a stream error: {message}")

    usage = obj.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            raise StreamProtocolError("'usage' must be an object when present")
        yield UsageEvent(
            prompt_tokens=_opt_int(usage.get("prompt_tokens"), "prompt_tokens"),
            completion_tokens=_opt_int(usage.get("completion_tokens"), "completion_tokens"),
            total_tokens=_opt_int(usage.get("total_tokens"), "total_tokens"),
            reasoning_tokens=_reasoning_tokens(usage),
        )

    choices = obj.get("choices")
    if choices is None:  # usage-only packet
        return
    if not isinstance(choices, list):
        raise StreamProtocolError("'choices' must be a list when present")
    for choice in choices:
        if not isinstance(choice, dict):
            raise StreamProtocolError("each entry of 'choices' must be an object")
        yield from _events_from_choice(choice)


def _events_from_choice(choice: dict[str, Any]) -> Iterator[StreamEvent]:
    delta = choice.get("delta")
    if delta is not None:
        if not isinstance(delta, dict):
            raise StreamProtocolError("'delta' must be an object when present")
        yield from _events_from_delta(delta)
    finish = choice.get("finish_reason")
    if finish is not None:
        if not isinstance(finish, str):
            raise StreamProtocolError("'finish_reason' must be a string when present")
        yield FinishEvent(reason=finish)


def _events_from_delta(delta: dict[str, Any]) -> Iterator[StreamEvent]:
    content = delta.get("content")
    if content is not None:
        if isinstance(content, str):
            if content:
                yield TextDelta(content)
        elif isinstance(content, list):
            # Some servers emit the structured content-part form.
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]:
                    yield TextDelta(part["text"])
        else:
            raise StreamProtocolError("'content' must be a string or list when present")

    for key in ("reasoning", "reasoning_content"):
        value = delta.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise StreamProtocolError(f"{key!r} must be a string when present")
        if value:
            yield ReasoningDelta(value)

    tool_calls = delta.get("tool_calls")
    if tool_calls is None:
        return
    if not isinstance(tool_calls, list):
        raise StreamProtocolError("'tool_calls' must be a list when present")
    for raw in tool_calls:
        if not isinstance(raw, dict):
            raise StreamProtocolError("each entry of 'tool_calls' must be an object")
        event = _tool_call_delta(raw)
        if event is not None:
            yield event


def _tool_call_delta(raw: dict[str, Any]) -> ToolCallDelta | None:
    index = raw.get("index", 0)
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise StreamProtocolError("tool call 'index' must be an integer")
    call_id = raw.get("id")
    if call_id is not None and not isinstance(call_id, str):
        raise StreamProtocolError("tool call 'id' must be a string when present")
    function = raw.get("function") or {}
    if not isinstance(function, dict):
        raise StreamProtocolError("tool call 'function' must be an object when present")
    name = function.get("name")
    if name is not None and not isinstance(name, str):
        raise StreamProtocolError("tool call 'function.name' must be a string when present")
    arguments = function.get("arguments")
    if arguments is None:
        arguments = ""
    if not isinstance(arguments, str):
        raise StreamProtocolError("tool call 'function.arguments' must be a string")
    if not call_id and not name and not arguments:
        return None  # index-only placeholder fragment
    return ToolCallDelta(index=index, call_id=call_id, name=name, arguments=arguments)


def _opt_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StreamProtocolError(f"usage {field_name!r} must be an integer when present")
    return value


def _reasoning_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("completion_tokens_details") or {}
    if not isinstance(details, dict):
        raise StreamProtocolError("completion_tokens_details must be an object")
    return _opt_int(details.get("reasoning_tokens"), "reasoning_tokens")
