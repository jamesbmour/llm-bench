"""Argument parsing and terminal-mode selection before UI construction."""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .config import Settings, resolve_config
from .errors import AuthenticationError, ConfigError, LlmsweepError
from .plain import export_run, render_models, render_run
from .providers.lmstudio import LMStudio
from .results import RunResult
from .runner import RunEvent, RunSession
from .security import Redactor
from .selection import filter_models, resolve_selection
from .store import RunStore, load_run

COMMANDS = ("run", "list", "show", "export", "doctor", "providers")


def wants_tui(plain: bool = False) -> bool:
    return (
        not plain
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.environ.get("TERM") != "dumb"
        and "CI" not in os.environ
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="llmsweep", description="LM Studio benchmarks and saved-run viewer"
    )
    subcommands = root.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = subcommands.add_parser(name)
        command.add_argument("--config", type=Path)
        command.add_argument("--run-store", type=Path)
        command.add_argument("--no-color", action="store_true", default=None)
        command.add_argument("--plain", action="store_true", default=None)
        if name in ("run", "show"):
            command.add_argument("--theme")
        if name in ("run", "list", "doctor"):
            command.add_argument("--provider")
            command.add_argument("--providers")
            command.add_argument("--base-url")
            command.add_argument("--api-key")
            command.add_argument("--host")
            command.add_argument("--port", type=int)
            command.add_argument("--timeout", type=float)
        if name in ("run", "list"):
            group = command.add_mutually_exclusive_group()
            group.add_argument("--models")
            group.add_argument("--all", action="store_true", default=None)
            command.add_argument("--require-tool-use", action="store_true", default=None)
            command.add_argument("--exclude")
        if name in ("run", "show", "export"):
            command.add_argument(
                "--sort-by", choices=["order", "tok_s", "ttft", "total", "load", "model", "cost"]
            )
            command.add_argument("--baseline", type=Path)
            command.add_argument("--fail-on-regression", action="store_true", default=None)
            command.add_argument("--json", type=Path)
            command.add_argument("--csv", type=Path)
            command.add_argument("--markdown", type=Path)
        if name in ("show", "export"):
            command.add_argument("path", type=Path)
        if name == "run":
            command.add_argument("--scenarios")
            command.add_argument("--task")
            command.add_argument("--repeat", type=int)
            command.add_argument("--max-turns", type=int)
            command.add_argument("--max-tokens", type=int)
            command.add_argument("--load-timeout", type=float)
            command.add_argument("--no-warmup", action="store_true", default=None)
            command.add_argument(
                "--no-unload",
                "--keep-loaded",
                dest="keep_loaded",
                action="store_true",
                default=None,
            )
            command.add_argument("--verbose", action="store_true", default=None)
            command.add_argument("--transcript-dir", type=Path)
            command.add_argument("--parallel", type=int)
    return root


def normalize_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] in ("--help", "-h"):
        return argv
    if "--list" in argv:
        if argv and argv[0] in COMMANDS:
            raise ConfigError("--list cannot be combined with a subcommand")
        return ["list", *[a for a in argv if a != "--list"]]
    if "--show" in argv:
        if argv and argv[0] in COMMANDS:
            raise ConfigError("--show cannot be combined with a subcommand")
        index = argv.index("--show")
        if index + 1 >= len(argv):
            raise ConfigError("--show requires a path")
        return ["show", argv[index + 1], *argv[:index], *argv[index + 2 :]]
    return argv if argv and argv[0] in COMMANDS else ["run", *argv]


def make_provider(settings: Settings) -> LMStudio:
    return LMStudio(settings.values["base_url"], settings.api_key, settings.values["timeout"])


def make_session(
    settings: Settings, provider: LMStudio, emit: Callable[[RunEvent], None] | None = None
) -> RunSession:
    v = settings.values
    store = RunStore(
        Path(v["run_store"]) if v["run_store"] else None,
        Path(v["transcript_dir"]) if v["transcript_dir"] else None,
        provider.redact,
    )
    baseline = load_run(Path(v["baseline"])) if v["baseline"] else None
    return RunSession(
        provider,
        store,
        settings.run_options(),
        emit=emit,
        baseline=baseline,
        thresholds=settings.thresholds,
    )


def exports(
    args: argparse.Namespace, run: RunResult, settings: Settings, provider: LMStudio | None = None
) -> None:
    export_run(
        run,
        json_path=getattr(args, "json", None),
        csv_path=getattr(args, "csv", None),
        markdown_path=getattr(args, "markdown", None),
        redact=provider.redact if provider else Redactor(settings.api_key),
        sort_by=settings.values["sort_by"],
    )


async def online(args: argparse.Namespace, settings: Settings) -> int:
    v = settings.values
    provider = make_provider(settings)
    try:
        models = filter_models(
            await provider.list_models(), tools_only=v["require_tool_use"], exclude=v["exclude"]
        )
        if args.command == "doctor":
            directory = Path(v["run_store"]) if v["run_store"] else RunStore().root
            ancestor = directory
            while not ancestor.exists():
                ancestor = ancestor.parent
            if not os.access(ancestor, os.W_OK):
                raise ConfigError(f"run store is not writable: {directory}")
            print(
                f"LM Studio {provider.version}: reachable; "
                f"{len(models)} chat models; store writable"
            )
            return 0
        if args.command == "list":
            selected = resolve_selection(v["models"], models) if v["models"] else models
            buffer = io.StringIO()
            render_models(selected, buffer)
            print(provider.redact(buffer.getvalue()), end="")
            return 0
        if not v["models"] and not v["all"]:
            raise ConfigError(
                "plain mode requires --models SPEC or --all; use list to inspect models"
            )
        selected = resolve_selection(v["models"], models) if v["models"] else models
        if not selected:
            raise ConfigError("no eligible chat models selected")

        def emit(event: RunEvent) -> None:
            if v["verbose"] and event.kind in ("tool", "model_start", "sample_done"):
                print(
                    provider.redact(f"{event.kind}\t{event.ref.key}\t{event.tool}\t{event.text}"),
                    file=sys.stderr,
                )

        session = make_session(settings, provider, emit)
        run = await session.run_all(selected)
        buffer = io.StringIO()
        render_run(run, buffer, v["sort_by"])
        print(provider.redact(buffer.getvalue()), end="")
        print(f"Saved: {session.store.directory(run)}")
        exports(args, run, settings, provider)
        return run.exit_code(v["fail_on_regression"])
    finally:
        await provider.close()


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(normalize_argv(list(sys.argv[1:] if argv is None else argv)))
        settings = resolve_config(vars(args))
        interactive = wants_tui(settings.values["plain"])
        if args.command == "providers":
            print("lmstudio\tlocal\tHTTP/SSE\tload/unload on v1; v0 JIT fallback")
            return 0
        if args.command in ("show", "export"):
            run = load_run(args.path)
            if settings.values["baseline"]:
                from .results import compare_runs

                run.comparisons = compare_runs(
                    run, load_run(Path(settings.values["baseline"])), *settings.thresholds
                )
            if args.command == "export" and not any((args.json, args.csv, args.markdown)):
                raise ConfigError("export requires --json, --csv, or --markdown")
            if args.command == "show":
                if interactive:
                    from .tui.app import SweepApp

                    SweepApp(settings, saved_run=run).run()
                else:
                    buffer = io.StringIO()
                    render_run(run, buffer, settings.values["sort_by"])
                    print(Redactor(settings.api_key)(buffer.getvalue()), end="")
            exports(args, run, settings)
            return run.exit_code(settings.values["fail_on_regression"])
        if args.command == "run":
            if settings.values["fail_on_regression"] and not settings.values["baseline"]:
                raise ConfigError("--fail-on-regression requires --baseline PATH")
            if interactive:
                from .tui.app import SweepApp

                app = SweepApp(settings)
                code = app.run()
                if app.saved_run:
                    exports(args, app.saved_run, settings)
                return code or 0
            if not settings.values["models"] and not settings.values["all"]:
                raise ConfigError("plain mode requires --models SPEC or --all")
        return asyncio.run(online(args, settings))
    except AuthenticationError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except (LlmsweepError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
