from __future__ import annotations

import json

import pytest

from llmsweep.errors import StreamProtocolError
from llmsweep.streams import (
    ChatStreamParser,
    ReasoningDelta,
    TextDelta,
    ToolCallAccumulator,
    ToolCallDelta,
    UsageEvent,
)


def packet(value: object) -> bytes:
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


def test_fragmented_utf8_reasoning_tools_and_usage() -> None:
    body = (
        b": keepalive\r\n"
        + packet(
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "réfléchir",
                            "content": "été",
                            "tool_calls": [
                                {
                                    "index": 2,
                                    "id": "a",
                                    "function": {"name": "get_", "arguments": '{"ci'},
                                },
                                {
                                    "index": 0,
                                    "id": "b",
                                    "function": {"name": "time", "arguments": "{}"},
                                },
                            ],
                        }
                    }
                ]
            }
        )
        + packet(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 2,
                                    "function": {"name": "weather", "arguments": 'ty":"Paris"}'},
                                },
                            ]
                        }
                    }
                ]
            }
        )
        + packet(
            {
                "choices": [],
                "usage": {
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "completion_tokens_details": {"reasoning_tokens": 7},
                },
            }
        )
        + b"data: [DONE]\n\n"
    )
    parser = ChatStreamParser()
    events = [event for byte in body for event in parser.feed(bytes([byte]))]
    events.extend(parser.close())
    assert parser.saw_done
    assert TextDelta("été") in events and ReasoningDelta("réfléchir") in events
    calls = ToolCallAccumulator()
    for event in events:
        if isinstance(event, ToolCallDelta):
            calls.add(event)
    assert calls.finish()[0].name == "get_weather"
    assert calls.finish()[0].parsed_arguments() == {"city": "Paris"}
    assert events[-1] == UsageEvent(None, 20, 30, 7)


def test_ndjson_yields_nothing() -> None:
    parser = ChatStreamParser()
    assert list(parser.feed(b'{"message":{"content":"hello"},"done":false}\n{"done":true}\n')) == []
    assert list(parser.close()) == []


def test_malformed_payload_is_error() -> None:
    with pytest.raises(StreamProtocolError, match="malformed"):
        list(ChatStreamParser().feed(b"data: nope\n\n"))
