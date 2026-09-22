"""Typed error taxonomy shared by every llmsweep component.

Errors are grouped so callers can act on a category instead of string matching:
configuration problems, transport failures (DNS / connect / TLS / timeout),
protocol failures (HTTP status, malformed body, bad stream framing), model
lifecycle failures, and store failures. Each error knows whether retrying the
same operation could plausibly succeed.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "ConfigError",
    "ConnectionFailedError",
    "DnsError",
    "HttpStatusError",
    "LlmsweepError",
    "MalformedResponseError",
    "ModelError",
    "ModelLoadError",
    "ModelNotFoundError",
    "ModelUnloadError",
    "ProviderError",
    "RequestTimeoutError",
    "SchemaVersionError",
    "SelectionError",
    "StoreCorruptError",
    "StoreError",
    "StreamError",
    "StreamProtocolError",
    "TlsError",
    "TransportError",
    "UnsupportedEndpointError",
]


class LlmsweepError(Exception):
    """Base class for every error llmsweep raises deliberately."""

    #: Whether retrying the identical operation could plausibly succeed.
    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class ConfigError(LlmsweepError):
    """Configuration, flag, or environment problem. Maps to exit code 2."""


class SelectionError(ConfigError):
    """A model or scenario selection expression could not be resolved."""


class ProviderError(LlmsweepError):
    """Base class for failures talking to a model provider."""


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


class TransportError(ProviderError):
    """The request never produced an HTTP response."""


class DnsError(TransportError):
    """The provider host name could not be resolved."""


class ConnectionFailedError(TransportError):
    """A TCP connection to the provider could not be established."""

    retryable = True


class TlsError(TransportError):
    """TLS negotiation with the provider failed."""


class RequestTimeoutError(TransportError):
    """The provider did not respond, or stopped responding, in time."""


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


class HttpStatusError(ProviderError):
    """The provider returned a non-success HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:  # type: ignore[override]
        return self.status_code >= 500


class AuthenticationError(HttpStatusError):
    """The provider rejected the supplied credentials. Maps to exit code 4."""

    retryable = False


class UnsupportedEndpointError(HttpStatusError):
    """The provider does not implement this endpoint (as opposed to failing it)."""

    retryable = False


class MalformedResponseError(ProviderError):
    """A response was received but did not match the documented shape."""


class StreamError(ProviderError):
    """A streaming response failed after it started."""


class StreamProtocolError(StreamError):
    """Server-sent-event framing or payload could not be parsed."""


# --------------------------------------------------------------------------
# Model lifecycle
# --------------------------------------------------------------------------


class ModelError(ProviderError):
    """Base class for model lifecycle failures."""


class ModelNotFoundError(ModelError):
    """The provider does not know about the requested model."""


class ModelLoadError(ModelError):
    """A model failed to load, or did not become ready before the deadline."""


class ModelUnloadError(ModelError):
    """A model instance could not be unloaded, or did not disappear."""


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class StoreError(LlmsweepError):
    """Base class for run-store failures."""


class SchemaVersionError(StoreError):
    """A stored document uses a schema version this build cannot read."""


class StoreCorruptError(StoreError):
    """A stored document could not be parsed."""
