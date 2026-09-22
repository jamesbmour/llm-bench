"""Credential redaction shared by transport, storage, and renderers."""

from __future__ import annotations

import json
import re


class Redactor:
    """Remove configured credentials and terminal escape sequences from output."""

    def __init__(self, *secrets: str | None) -> None:
        variants = {variant for s in secrets if s for variant in (s, json.dumps(s)[1:-1])}
        self.secrets = tuple(sorted(variants, key=len, reverse=True))

    def __call__(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        return re.sub(r"(?i)(bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
