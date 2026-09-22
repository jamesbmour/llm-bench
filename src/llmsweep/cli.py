"""Argument parsing and terminal-mode selection before UI construction."""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings, resolve_config
from .errors import AuthenticationError, ConfigError, LlmsweepError
from .plain import export_run, render_models, render_run
from .providers.lmstudio import LMStudio
from .results import RunResult
from .runner import RunEvent, RunSession
from .security import Redactor
from .selection import filter_models, resolve_selection
from .store import RunStore, load_run

COMMANDS = (
    "run",
    "list",
    "show",
    "export",
    "doctor",
    "providers",
    "benchmarks",
    "profiles",
    "resume",
    "rerun",
    "compare",
    "coverage",
    "setup",
)


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
    catalog = {"benchmarks", "profiles", "resume", "rerun", "compare", "coverage"}
    for name in COMMANDS:
        if name in catalog:
            continue
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
            command.add_argument("--preset")
            command.add_argument("--profile")
            command.add_argument("--pack")
            command.add_argument("--tasks")
            command.add_argument("--repeat-mode", choices=["fixed", "exploratory"])
            command.add_argument("--min-repeats", type=int)
            command.add_argument("--max-repeats", type=int)
            command.add_argument("--precision", type=float)
            command.add_argument("--time-cap", dest="time_cap_s", type=float)
            command.add_argument("--token-cap", type=int)
            command.add_argument("--temperature", type=float)
            command.add_argument("--seed", type=int)
            command.add_argument("--context-limit", type=int)
        if name == "setup":
            command.add_argument("--preset")
            command.add_argument("--profile")
            command.add_argument("--scenarios")
            command.add_argument("--tasks")
            command.add_argument("--pack")
    _catalog_commands(subcommands)
    return root


def _catalog_commands(subcommands: Any) -> None:
    benchmarks = subcommands.add_parser("benchmarks")
    benchmark_actions = benchmarks.add_subparsers(dest="action", required=True)
    benchmark_actions.add_parser("list")
    inspect = benchmark_actions.add_parser("inspect")
    inspect.add_argument("name")
    validate = benchmark_actions.add_parser("validate")
    validate.add_argument("path", type=Path)
    profiles = subcommands.add_parser("profiles")
    profile_actions = profiles.add_subparsers(dest="action", required=True)
    profile_actions.add_parser("list")
    show = profile_actions.add_parser("show")
    show.add_argument("name")
    save = profile_actions.add_parser("save")
    save.add_argument("name")
    save.add_argument("--from-run", type=Path, required=True)
    resume = subcommands.add_parser("resume")
    resume.add_argument("path", type=Path)
    resume.add_argument("--plain", action="store_true", default=None)
    resume.add_argument("--run-store", type=Path)
    rerun = subcommands.add_parser("rerun")
    rerun.add_argument("path", type=Path)
    rerun.add_argument("--sample", required=True)
    rerun.add_argument("--plain", action="store_true", default=None)
    rerun.add_argument("--models")
    rerun.add_argument("--all", action="store_true", default=None)
    rerun.add_argument("--run-store", type=Path)
    compare = subcommands.add_parser("compare")
    compare.add_argument("paths", nargs=2, type=Path)
    compare.add_argument("--plain", action="store_true", default=None)
    compare.add_argument("--json", type=Path, help="Export comparison as JSON")
    compare.add_argument("--csv", type=Path, help="Export comparison as CSV")
    compare.add_argument("--markdown", type=Path, help="Export comparison as Markdown")
    coverage = subcommands.add_parser("coverage")
    coverage.add_argument("path", type=Path)
    coverage.add_argument("--plain", action="store_true", default=None)
    coverage.add_argument("--json", type=Path, help="Export coverage as JSON")
    coverage.add_argument("--csv", type=Path, help="Export coverage as CSV")
    coverage.add_argument("--markdown", type=Path, help="Export coverage as Markdown")

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
            from .execution.policy import isolated_policy, workflow_policy

            isolation = isolated_policy()
            print(
                f"LM Studio {provider.version}: reachable; "
                f"{len(models)} chat models; store writable"
            )
            isolated = f"available via {isolation.detail}" if isolation.available else "unavailable"
            print(f"execution: {workflow_policy().name} available; isolated {isolated}")
            return 0
        if args.command in ("resume", "rerun"):
            return await _continue(args, settings, provider, models)
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


def _local(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "benchmarks":
        from .benchmarks.registry import get_suite, suites
        from .packs import load_pack

        if args.action == "list":
            for suite in suites().values():
                tools = "yes" if suite.requires_tools else "no"
                print(
                    f"{suite.suite_id}\t{suite.task_count}\t{suite.execution}\t{tools}\t{suite.title}"
                )
            return 0
        if args.action == "inspect":
            suite = get_suite(args.name)
            print(f"{suite.suite_id}\t{suite.version}\t{suite.execution}\t{suite.title}")
            for task in suite.tasks:
                print(f"{task.task_id}\t{task.partition}\t{task.difficulty}\t{task.content_digest}")
            return 0
        pack = load_pack(args.path)
        print(f"{pack.pack_id}\t{pack.version}\t{len(pack.tasks)}\t{pack.digest}")
        return 0
    if args.command == "profiles":
        from platformdirs import user_config_path

        from .profiles import list_profiles, load_profile_file, resolve_profile_path, save_profile

        project = Path.cwd()
        user = user_config_path("llmsweep", appauthor=False)
        if args.action == "list":
            for name in list_profiles(project, user):
                print(name)
            return 0
        if args.action == "show":
            path = resolve_profile_path(args.name, project, user)
            for key, value in load_profile_file(path).items():
                print(f"{key}\t{value}")
            return 0
        parent = load_run(args.from_run)
        save_profile(project / "profiles" / f"{args.name}.toml", parent.settings)
        print(project / "profiles" / f"{args.name}.toml")
        return 0
    if args.command == "compare":
        from .comparison import build_views, compare_checked, export_comparison

        current = load_run(args.paths[0])
        baseline = load_run(args.paths[1])
        rows = compare_checked(current, baseline, *settings.thresholds)
        if not rows:
            print("no overlapping samples")
        for row in rows:
            print(
                "\t".join(
                    str(row.get(key, ""))
                    for key in ("model", "scenario", "verdict", "reason", "quality_only")
                )
            )
        view = build_views([current, baseline])
        print(f"points\t{len(view['points'])}")
        if any((args.json, args.csv, args.markdown)):
            export_comparison(
                view,
                json_path=args.json,
                csv_path=args.csv,
                markdown_path=args.markdown,
                redact=Redactor(settings.api_key),
            )
        return 0
    if args.command == "coverage":
        from .coverage import build_coverage_matrix, coverage_to_markdown, export_coverage

        run = load_run(args.path)
        view = build_coverage_matrix([run])
        print(coverage_to_markdown(view))
        if any((args.json, args.csv, args.markdown)):
            export_coverage(
                view,
                json_path=args.json,
                csv_path=args.csv,
                markdown_path=args.markdown,
                redact=Redactor(settings.api_key),
            )
        return 0
    from .benchmarks.presets import plan_report

    print(plan_report(settings.values), end="")
    return 0


async def _continue(
    args: argparse.Namespace,
    settings: Settings,
    provider: LMStudio,
    models: list[Any],
) -> int:
    from dataclasses import replace

    from .models import ModelInfo
    from .runner import options_from_settings

    parent = load_run(args.path)
    options = options_from_settings(parent.settings)
    store = RunStore(
        Path(settings.values["run_store"]) if settings.values["run_store"] else None,
        redact=provider.redact,
    )
    if args.command == "rerun":
        found = next(
            (
                (model, sample)
                for model in parent.models
                for sample in model.samples
                if args.sample in (sample.attempt_id, sample.task_id)
            ),
            None,
        )
        if found is None:
            raise ConfigError(f"sample not found: {args.sample}")
        owner, sample = found
        suite = sample.suite_id or sample.scenario
        options = replace(options, scenarios=(suite,), tasks=f"{suite}:{sample.task_id}", repeat=1)
        session = RunSession(provider, store, options, thresholds=settings.thresholds)
        session.run.parent_run_id = parent.run_id
        session.run.diagnostic = True
        wanted = {owner.model.ref.key}
    else:
        session = RunSession(provider, store, options, run=parent, thresholds=settings.thresholds)
        wanted = {model.model.ref.key for model in parent.models}
    available = {model.ref.key: model for model in models}
    if not wanted <= set(available):
        missing = ", ".join(sorted(wanted - set(available)))
        raise ConfigError(f"saved models are not available: {missing}")
    selected: list[ModelInfo] = [available[key] for key in wanted]
    run = (
        await session.run_all(selected)
        if args.command == "rerun"
        else await session.resume_all(selected)
    )
    buffer = io.StringIO()
    render_run(run, buffer, settings.values["sort_by"])
    print(provider.redact(buffer.getvalue()), end="")
    print(f"Saved: {session.store.directory(run)}")
    return run.exit_code(settings.values["fail_on_regression"])


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(normalize_argv(list(sys.argv[1:] if argv is None else argv)))
        settings = resolve_config(vars(args))
        interactive = wants_tui(settings.values["plain"])
        if args.command == "providers":
            print("lmstudio\tlocal\tHTTP/SSE\tload/unload on v1; v0 JIT fallback")
            return 0
        if args.command == "compare":
            if interactive:
                from .tui.app import SweepApp

                current = load_run(args.paths[0])
                baseline = load_run(args.paths[1])
                app = SweepApp(settings, comparison_runs=[current, baseline])
                code = app.run()
                return code or 0
            return _local(args, settings)
        if args.command == "coverage":
            run = load_run(args.path)
            if interactive:
                from .tui.app import SweepApp

                app = SweepApp(settings, coverage_runs=[run])
                code = app.run()
                return code or 0
            return _local(args, settings)
        if args.command in ("benchmarks", "profiles", "setup"):
            return _local(args, settings)
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
