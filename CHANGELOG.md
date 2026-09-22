# Changelog

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
