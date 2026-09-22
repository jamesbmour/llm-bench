"""Environment snapshots. Unknown measurements stay null."""

from __future__ import annotations

import os
import platform
from typing import Any

from llmsweep import __version__


def _memory_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None
    if pages < 0 or size < 0:
        return None
    return pages * size


def capture_provenance(
    *,
    provider: str,
    server_version: str | None,
    policy: str,
    requested: dict[str, Any],
    benchmark_versions: dict[str, str],
) -> dict[str, Any]:
    uname = platform.uname()
    cpu = platform.processor() or None
    return {
        "hardware": {"cpu": cpu, "memory_bytes": _memory_bytes(), "gpu": None},
        "os": {"system": uname.system, "release": uname.release, "machine": uname.machine},
        "inference_server": {"name": provider, "version": server_version},
        "app_version": __version__,
        "benchmark_versions": dict(benchmark_versions),
        "execution_policy": policy,
        "requested": {
            "temperature": requested.get("temperature"),
            "seed": requested.get("seed"),
            "max_tokens": requested.get("max_tokens"),
            "context_limit": requested.get("context_limit"),
        },
        "observed": {
            "temperature": None,
            "seed": None,
            "revision": None,
            "quantization": None,
            "context_limit": None,
        },
    }
