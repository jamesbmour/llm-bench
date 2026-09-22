"""Atomic schema-versioned run storage, transcripts, and historical ETA lookup."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_data_path

from .errors import SchemaVersionError, StoreCorruptError, StoreError
from .models import ModelRef
from .results import RunResult, SampleResult, restore_run
from .security import Redactor


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".llmsweep-", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    except OSError as exc:
        raise StoreError(f"cannot write {path}: {exc}") from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def transcript_name(ref: ModelRef, scenario: str, repeat: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", ref.id)[:70]
    digest = hashlib.sha256(ref.key.encode()).hexdigest()[:16]
    return f"{ref.provider}_{slug}-{digest}_{scenario}_r{repeat}.json"


class RunStore:
    """Persist canonical run documents and provider-qualified transcripts atomically."""

    def __init__(
        self,
        root: Path | None = None,
        transcript_dir: Path | None = None,
        redact: Redactor | None = None,
    ) -> None:
        self.root = root or user_data_path("llmsweep", appauthor=False)
        self.transcript_dir = transcript_dir
        self.redact = redact or Redactor()

    def create(
        self, settings: dict[str, Any], *, run_id: str | None = None, started_at: str | None = None
    ) -> RunResult:
        now = datetime.now(UTC)
        return RunResult(
            run_id or f"{now:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}",
            started_at or now.isoformat(),
            settings,
        )

    def directory(self, run: RunResult) -> Path:
        return self.root / "runs" / run.run_id

    def save(self, run: RunResult) -> None:
        directory = self.directory(run)
        for model in run.models:
            for sample in model.samples:
                name = transcript_name(model.model.ref, sample.scenario, sample.repeat)
                sample.transcript = f"transcripts/{name}"
                data = {
                    "provider": model.model.ref.provider,
                    "model": model.model.ref.id,
                    "scenario": sample.scenario,
                    "repeat": sample.repeat,
                    "messages": sample.messages,
                    "answer": sample.answer,
                    "output": sample.output,
                    "error": sample.error,
                    "status": sample.status,
                }
                text = self.redact(canonical_json(data))
                atomic_write(directory / "transcripts" / name, text)
                if self.transcript_dir:
                    atomic_write(self.transcript_dir / run.run_id / name, text)
        atomic_write(directory / "run.json", self.redact(canonical_json(run.document())))
        self._index(run)

    def _index(self, run: RunResult) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".index.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            path = self.root / "index.json"
            try:
                rows = json.loads(path.read_text()) if path.exists() else []
                if not isinstance(rows, list):
                    rows = []
            except (OSError, ValueError):
                rows = []
            rows = [
                row for row in rows if isinstance(row, dict) and row.get("run_id") != run.run_id
            ]
            rows.append({"run_id": run.run_id, "started_at": run.started_at, "status": run.status})
            atomic_write(path, canonical_json(sorted(rows, key=lambda row: row["started_at"])))

    def estimate(self, refs: list[ModelRef], settings: dict[str, Any]) -> float | None:
        path = self.root / "index.json"
        if not path.exists():
            return None
        try:
            rows = json.loads(path.read_text())
            estimates: dict[ModelRef, float] = {}
            for row in reversed(rows):
                run = load_run(self.root / "runs" / row["run_id"])
                if run.settings != settings:
                    continue
                for model in run.models:
                    if model.status == "completed" and model.model.ref in refs:
                        estimates.setdefault(model.model.ref, model.total_s)
                if len(estimates) == len(refs):
                    return sum(estimates.values())
        except (OSError, ValueError, KeyError, TypeError, StoreError):
            return None
        return None


def load_run(path: Path) -> RunResult:
    if path.is_dir():
        path = path / "run.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("expected a run object")
        version = raw.get("schema_version")
        # v1 is the first published schema; unversioned reference files have no safe migration.
        if version != 1 or isinstance(version, bool):
            raise SchemaVersionError(f"unsupported schema_version {version!r}; this build reads 1")
        run = restore_run(raw)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run.run_id):
            raise ValueError("invalid run_id")
        if not isinstance(run.settings, dict):
            raise ValueError("settings must be an object")
        canonical_json(run.document())
        return run
    except SchemaVersionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise StoreCorruptError(f"cannot read run {path}: {exc}") from None


def sample_transcript(sample: SampleResult) -> str:
    return canonical_json(
        {
            "messages": sample.messages,
            "answer": sample.answer,
            "output": sample.output,
            "error": sample.error,
        }
    )
