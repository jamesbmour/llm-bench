# Changelog

## Unreleased

- Restyle the Textual UI on theme variables: title bar with the endpoint or run ID, a
  persistent status bar, status-colored model cards with a metrics strip, zebra tables,
  a marked sort column, and consistent dialog framing; monochrome mode keeps borders.
- Add `--theme` / `LLMSWEEP_THEME` / `theme` with `Ctrl+T` cycling, an `F1` / `?` help
  overlay built from the active bindings plus the metric legend, results text and
  status filters, live card zoom with `[` / `]` navigation, and picker select-all/clear.
- `Ctrl+C` now cleans up and quits the TUI; `Ctrl+Q` shows the quit/cancel reminder instead.

## 1.0.0 — 2026-09-22

- Implement the LM Studio-only CLI and Textual application: run, list, show, export,
  doctor, and providers, with automatic plain mode for pipes and CI.
- Add v1/v0 discovery, SSE streaming, instance ownership, readiness polling,
  bounded retries, secret redaction, and cancellation cleanup.
- Add deterministic weather, agent-code, and codegen scenarios with fresh workspaces.
- Record per-turn and per-repeat detail, mean/median/p95 scenario rollups, token-source
  labels, and provider-qualified baseline regression verdicts.
- Add atomic schema-versioned storage, JSON/CSV/Markdown exports, offline viewing,
  historical ETA, strict TOML configuration, and environment overrides.
- Add offline contracts and Textual Pilot tests, plus macOS/Linux CI.

Ollama, generic OpenAI, OpenRouter, billing, and OS-level sandboxing are deferred.
The original reference benchmark script remains unchanged.
