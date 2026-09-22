"""Greppable tables and portable exports of runner-owned results."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, TextIO

from .models import ModelInfo
from .results import ModelResult, RunResult, SampleResult
from .security import Redactor
from .store import atomic_write, canonical_json

SORT_FIELDS = {"tok_s": "tok_s", "ttft": "ttft_ms", "total": "total_s", "load": "load_s"}


def display(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")


def ordered_models(run: RunResult, sort_by: str = "order") -> list[ModelResult]:
    models = list(run.models)
    if sort_by == "model":
        return sorted(models, key=lambda m: m.model.ref.key.casefold())
    if sort_by in SORT_FIELDS:
        field = SORT_FIELDS[sort_by]

        def key(model: ModelResult) -> tuple[bool, float]:
            value = (model.summary() | {"total_s": model.total_s, "load_s": model.load_s})[field]
            return value is None, (
                -value if sort_by == "tok_s" else value
            ) if value is not None else 0

        models.sort(key=key)
    return sorted(models, key=lambda m: m.model.ref.provider)


def render_models(models: list[ModelInfo], stream: TextIO) -> None:
    print("Provider: lmstudio | chat models only | sizes in billions", file=stream)
    print("INDEX\tMODEL\tPARAMS_B\tTOOLS\tLOADED\tFORMAT\tQUANTIZATION", file=stream)
    for index, model in enumerate(models, 1):
        print(
            "\t".join(
                [
                    str(index),
                    display(model.ref.key),
                    display(model.params_b) if model.params_b is not None else "-",
                    "yes" if model.tool_use else "no" if model.tool_use is False else "unknown",
                    str(model.loaded).lower(),
                    display(model.format),
                    display(model.quantization),
                ]
            ),
            file=stream,
        )


def render_run(run: RunResult, stream: TextIO, sort_by: str = "order") -> None:
    print(f"Run {run.run_id} | {run.status}", file=stream)
    print("Throughput: client-observed tok/s; model value = mean of scenario means", file=stream)
    providers = {m.model.ref.provider for m in run.models}
    if len(providers) > 1:
        print(
            "Comparability: correctness may be compared across providers; "
            "speed rankings stay within each provider.",
            file=stream,
        )
    all_sources = {t.token_source for m in run.models for s in m.samples for t in s.turns}
    if len(all_sources) > 1:
        print(
            "WARNING: token sources differ; throughput values are not directly comparable.",
            file=stream,
        )
    provider = None
    for model in ordered_models(run, sort_by):
        if provider != model.model.ref.provider:
            provider = model.model.ref.provider
            print(f"\nProvider: {provider}", file=stream)
            print(
                "MODEL\tSTATUS\tSUCCESS_RATE\tTOK_S\tTTFT_MS\tLOAD_S\tWARMUP_S\tTOTAL_S\tTOKEN_SOURCE\tERROR",
                file=stream,
            )
        summary = model.summary()
        print(
            "\t".join(
                display(v)
                for v in [
                    model.model.ref.key,
                    model.status,
                    summary["success_rate"],
                    summary["tok_s"],
                    summary["ttft_ms"],
                    model.load_s,
                    model.warmup_s,
                    model.total_s,
                    summary["token_source"],
                    model.error,
                ]
            ),
            file=stream,
        )
        print(f"  load: {model.load_status}; cost: — (not billed)", file=stream)
        if model.contended:
            print("  WARNING: contended parallel measurement", file=stream)
        for warning in model.warnings:
            print(f"  WARNING: {warning}", file=stream)
        for scenario, values in model.rollups().items():
            for metric in ("tok_s", "ttft_ms", "total_s"):
                stats = values[metric]
                print(
                    f"  {scenario}\t{metric}\tmean={display(stats['mean'])}"
                    f"\tmedian={display(stats['median'])}\tp95={display(stats['p95'])}",
                    file=stream,
                )
        for sample in model.samples:
            print(
                f"  {sample.scenario}\tr{sample.repeat}\t{sample.status}"
                f"\tsuccess={display(sample.success)}\tturns={len(sample.turns)}"
                f"\ttools={','.join(sample.tools_called)}",
                file=stream,
            )
    for comparison in run.comparisons:
        marker = "▼ regression" if comparison["verdict"] == "regression" else comparison["verdict"]
        print(
            f"BASELINE\t{comparison['model']}\t{comparison['scenario']}\t{marker}"
            f"\t{comparison.get('reason') or ''}",
            file=stream,
        )


def csv_report(run: RunResult) -> str:
    buffer = io.StringIO(newline="")
    fields = [
        "provider",
        "model",
        "scenario",
        "repeat",
        "status",
        "success",
        "tok_s",
        "ttft_ms",
        "token_source",
        "load_s",
        "warmup_s",
        "total_s",
        "output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "turns",
        "tools_called",
        "expected_tools",
        "tool_errors",
        "contended",
        "error",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for model in run.models:
        samples: list[SampleResult | None] = list(model.samples) if model.samples else [None]
        for sample in samples:
            sources = sorted({t.token_source for t in sample.turns}) if sample else []
            row: dict[str, Any] = {
                "provider": model.model.ref.provider,
                "model": model.model.ref.id,
                "scenario": sample.scenario if sample else "",
                "repeat": sample.repeat if sample else "",
                "status": sample.status if sample else model.status,
                "success": sample.success if sample else None,
                "tok_s": sample.tok_s if sample else None,
                "ttft_ms": sample.ttft_ms if sample else None,
                "token_source": ",".join(sources),
                "load_s": model.load_s,
                "warmup_s": model.warmup_s,
                "total_s": sample.total_s if sample else model.total_s,
                "contended": model.contended,
                "turns": len(sample.turns) if sample else 0,
                "tools_called": ",".join(sample.tools_called) if sample else "",
                "expected_tools": ",".join(sample.expected_tools) if sample else "",
                "tool_errors": "; ".join(sample.tool_errors) if sample else "",
                "error": (sample.error if sample else None) or model.error,
            }
            for field in ("output_tokens", "total_tokens", "reasoning_tokens"):
                values = [getattr(t, field) for t in sample.turns] if sample else []
                row[field] = sum(values) if values and all(v is not None for v in values) else None
            writer.writerow({k: "" if v is None else v for k, v in row.items()})
    return buffer.getvalue()


def markdown_report(run: RunResult, sort_by: str = "order") -> str:
    def cell(value: Any) -> str:
        return display(value).replace("|", "\\|")

    lines = [f"# llmsweep {run.run_id}", "", "Client-observed tok/s; mean of scenario means.", ""]
    provider = None
    for model in ordered_models(run, sort_by):
        if provider != model.model.ref.provider:
            provider = model.model.ref.provider
            lines += [
                f"## {provider}",
                "",
                "| Model | Status | Success rate | tok/s | TTFT ms | Source | Error |",
                "|---|---|---:|---:|---:|---|---|",
            ]
        summary = model.summary()
        lines.append(
            "| "
            + " | ".join(
                cell(v)
                for v in [
                    model.model.ref.id,
                    model.status,
                    summary["success_rate"],
                    summary["tok_s"],
                    summary["ttft_ms"],
                    summary["token_source"],
                    model.error,
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Speed comparisons are valid only within a provider and matching token sources.",
        "",
        "```text",
    ]
    buffer = io.StringIO()
    render_run(run, buffer, sort_by)
    lines += [buffer.getvalue().replace("```", "'''"), "```", ""]
    return "\n".join(lines)


def export_run(
    run: RunResult,
    *,
    json_path: Path | None = None,
    csv_path: Path | None = None,
    markdown_path: Path | None = None,
    redact: Redactor | None = None,
    sort_by: str = "order",
) -> None:
    clean = redact or Redactor()
    for path, text in [
        (json_path, canonical_json(run.document())),
        (csv_path, csv_report(run)),
        (markdown_path, markdown_report(run, sort_by)),
    ]:
        if path is not None:
            atomic_write(path, clean(text))
