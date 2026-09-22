from __future__ import annotations

import socket
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
