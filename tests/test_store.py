from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmsweep.errors import SchemaVersionError, StoreCorruptError
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.results import ModelResult, SampleResult
from llmsweep.security import Redactor
from llmsweep.store import RunStore, load_run, transcript_name


def test_roundtrip_redaction_and_names(tmp_path: Path) -> None:
    store = RunStore(tmp_path, redact=Redactor("secret-key"))
    run = store.create({}, run_id="fixed", started_at="fixed")
    model = ModelResult(ModelInfo(ModelRef("lmstudio", "qwen/3:8b")))
    model.samples.append(SampleResult("weather", 1, answer="secret-key"))
    run.models.append(model)
    store.save(run)
    saved = store.directory(run) / "run.json"
    assert "secret-key" not in saved.read_text()
    assert load_run(saved).models[0].samples[0].answer == "[REDACTED]"
    first = saved.read_bytes()
    store.save(run)
    assert saved.read_bytes() == first
    assert transcript_name(ModelRef("ollama", "qwen/3:8b"), "weather", 1) != transcript_name(
        model.model.ref, "weather", 1
    )
    assert transcript_name(ModelRef("lmstudio", "a/b"), "weather", 1) != transcript_name(
        ModelRef("lmstudio", "a_b"), "weather", 1
    )
    assert len(json.loads((tmp_path / "index.json").read_text())) == 1


def test_schema_errors(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text('{"schema_version":99}')
    with pytest.raises(SchemaVersionError):
        load_run(path)
    path.write_text('{"schema_version":1}')
    with pytest.raises(StoreCorruptError):
        load_run(path)
