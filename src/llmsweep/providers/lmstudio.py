from __future__ import annotations

import asyncio
import json
import math
import random
import socket
import ssl
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from llmsweep.errors import (
    AuthenticationError,
    ConnectionFailedError,
    DnsError,
    HttpStatusError,
    MalformedResponseError,
    ModelLoadError,
    ModelUnloadError,
    ProviderError,
    RequestTimeoutError,
    StreamError,
    TlsError,
    UnsupportedEndpointError,
)
from llmsweep.models import ModelInfo, normalize_model, sort_models
from llmsweep.security import Redactor
from llmsweep.streams import ChatStreamParser, StreamEvent, is_output_delta

from .base import Lease


class LMStudio:
    def __init__(
        self,
        base_url: str = "http://localhost:1234",
        api_key: str | None = None,
        timeout: float = 300,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.redact = Redactor(api_key)
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        self.version: str | None = None
        self.sleep = sleep
        self.clock = clock

    async def close(self) -> None:
        await self.client.aclose()

    def _status_error(self, response: httpx.Response) -> HttpStatusError:
        code = response.status_code
        detail = self.redact(response.text)[:500]
        error: type[HttpStatusError] = HttpStatusError
        if code in (401, 403):
            error = AuthenticationError
            detail += "; check LMSTUDIO_API_KEY or --api-key"
        elif code in (404, 405, 501):
            error = UnsupportedEndpointError
        return error(f"HTTP {code} {response.request.url.path}: {detail}", status_code=code)

    def _transport_error(self, exc: httpx.HTTPError) -> ProviderError:
        cause: BaseException | None = exc
        while cause:
            if isinstance(cause, socket.gaierror):
                return DnsError(self.redact(str(exc)))
            if isinstance(cause, ssl.SSLError):
                return TlsError(self.redact(str(exc)))
            cause = cause.__cause__
        message = self.redact(str(exc))
        if isinstance(exc, httpx.TimeoutException):
            return RequestTimeoutError(f"request inactivity timeout: {message}")
        if "CERTIFICATE_VERIFY_FAILED" in message or "SSL" in message:
            return TlsError(message)
        if isinstance(exc, httpx.ConnectError):
            return ConnectionFailedError(message)
        return StreamError(f"connection interrupted: {message}")

    async def _backoff(self, attempt: int) -> None:
        await self.sleep(0.5 * 2**attempt + random.uniform(0, 0.25))

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        retry: bool = True,
    ) -> dict[str, Any]:
        for attempt in range(3):
            try:
                response = await self.client.request(method, path, json=payload)
                if not response.is_success:
                    raise self._status_error(response)
                try:
                    result = response.json()
                except (ValueError, UnicodeError):
                    raise MalformedResponseError(f"invalid JSON from {path}") from None
                if not isinstance(result, dict):
                    raise MalformedResponseError(f"expected JSON object from {path}")
                return result
            except httpx.HTTPError as exc:
                error = self._transport_error(exc)
            except ProviderError as exc:
                error = exc
            if not retry or not error.retryable or attempt == 2:
                raise error
            await self._backoff(attempt)
        raise AssertionError("unreachable")

    async def list_models(self) -> list[ModelInfo]:
        if self.version != "v0":
            try:
                body = await self._request("GET", "/api/v1/models")
                self.version = "v1"
            except UnsupportedEndpointError:
                self.version = "v0"
        if self.version == "v0":
            body = await self._request("GET", "/api/v0/models")
        key = "models" if self.version == "v1" else "data"
        rows = body.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise MalformedResponseError(f"{key} must be an array of models")
        return sort_models([normalize_model(row, self.version or "v1") for row in rows])

    async def acquire(self, model: ModelInfo, load_deadline: float) -> Lease:
        current = next((m for m in await self.list_models() if m.ref == model.ref), None)
        if current is None:
            raise ModelLoadError(f"model disappeared: {model.ref.key}")
        if current.loaded:
            return Lease(current, next(iter(current.instances), None))
        if self.version == "v0":
            return Lease(current, note="v0: JIT loading; load timing and unload unavailable")
        started = self.clock()
        lease: Lease | None = None
        try:
            async with asyncio.timeout(load_deadline):
                # A timed-out POST may still complete remotely; never blindly repeat it.
                info = await self._request(
                    "POST",
                    "/api/v1/models/load",
                    {"model": model.ref.id},
                    retry=False,
                )
                instance = info.get("instance_id")
                if not isinstance(instance, str) or not instance:
                    raise ModelLoadError(
                        "load returned no instance ID; ownership cannot be verified"
                    )
                lease = Lease(current, instance, True, note="loaded by this run")
                while True:
                    models = await self.list_models()
                    if any(m.ref == model.ref and instance in m.instances for m in models):
                        reported = info.get("load_time_seconds")
                        lease.load_s = (
                            float(reported)
                            if (
                                isinstance(reported, (int, float))
                                and not isinstance(reported, bool)
                                and reported >= 0
                                and math.isfinite(reported)
                            )
                            else self.clock() - started
                        )
                        return lease
                    await self.sleep(1)
        except (TimeoutError, ProviderError) as exc:
            cleanup = ""
            if lease:
                try:
                    await self.release(lease)
                except ProviderError as release_error:
                    cleanup = f"; cleanup failed: {release_error}"
            else:
                cleanup = "; load outcome unconfirmed; no unidentified instance was unloaded"
            if isinstance(exc, AuthenticationError):
                raise
            raise ModelLoadError(f"loading {model.ref.key} failed: {exc}{cleanup}") from None

    async def release(self, lease: Lease) -> None:
        if lease.released or not lease.we_loaded or not lease.instance_id:
            return
        try:
            async with asyncio.timeout(30):
                # A known instance can still be loading and absent from discovery.
                try:
                    await self._request(
                        "POST",
                        "/api/v1/models/unload",
                        {"instance_id": lease.instance_id},
                        retry=False,
                    )
                except ProviderError:
                    if any(lease.instance_id in m.instances for m in await self.list_models()):
                        raise
                for _ in range(10):
                    if not any(lease.instance_id in m.instances for m in await self.list_models()):
                        lease.released = True
                        return
                    await self.sleep(1)
        except (TimeoutError, ProviderError) as exc:
            raise ModelUnloadError(f"unload unconfirmed for {lease.instance_id}: {exc}") from None
        raise ModelUnloadError(f"instance remains loaded: {lease.instance_id}")

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        emitted = False
        for attempt in range(3):
            parser = ChatStreamParser()
            try:
                async with self.client.stream(
                    "POST", "/v1/chat/completions", json=payload
                ) as response:
                    if not response.is_success:
                        await response.aread()
                        raise self._status_error(response)
                    async for chunk in response.aiter_bytes():
                        for event in parser.feed(chunk):
                            emitted |= is_output_delta(event)
                            yield event
                        if parser.saw_done:
                            return
                    for event in parser.close():
                        emitted |= is_output_delta(event)
                        yield event
                    if not parser.saw_done:
                        raise StreamError("stream ended before [DONE]; partial output retained")
                    return
            except httpx.HTTPError as exc:
                error = self._transport_error(exc)
            except ProviderError as exc:
                # Do not let a provider body echo credentials through a worker exception.
                exc.message = self.redact(exc.message)
                error = exc
            if emitted or not error.retryable or attempt == 2:
                raise error
            await self._backoff(attempt)
        raise AssertionError("unreachable")


def sse(value: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(value) + "\n\n").encode()
