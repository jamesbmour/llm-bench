---
title: "CLI Reference & Configuration"
description: "Command documentation, flags, environment variables, configuration precedence, and exit codes"
---

## CLI Command & Flag Reference

Use `llmsweep COMMAND [OPTIONS]`. Omitting the command selects `run`.
Top-level `--list` and `--show PATH` are compatibility aliases. Use
`llmsweep run --help` for argparse's full usage.

| Command | Purpose |
| --- | --- |
| `run` | Benchmark selected models; opens the picker in an interactive terminal. |
| `list` | List eligible chat models, largest parameter count first. |
| `show PATH` | Open a run directory or `run.json` offline; `--plain` prints tables. |
| `export PATH --json FILE --csv FILE --markdown FILE` | Export one or more formats offline. |
| `doctor` | Check discovery, authentication, API version, and store writability without chat. |
| `providers` | Show the supported provider: `lmstudio`. |

| Flag | Default / meaning |
| --- | --- |
| `--models SPEC`, `--all` | IDs, indices/ranges, or unique substrings; mutually exclusive. Plain runs require one. |
| `--provider NAME`, `--providers NAMES` | Only `lmstudio` is implemented in v1. |
| `--base-url URL` | Server origin; defaults to `http://localhost:1234`. |
| `--host HOST`, `--port PORT` | Alternative to `--base-url`; default `localhost`, `1234`. |
| `--api-key KEY` | Optional credential; prefer environment variables to avoid shell history. |
| `--require-tool-use` | Select models advertising tool support. |
| `--exclude a,b` | Exclude matching model IDs/substrings. |
| `--scenarios LIST` | `weather,agent-code,codegen`; preserves order and deduplicates. |
| `--task TEXT` | Custom weather task, scored `n/a`; rejects explicitly selected other scenarios. |
| `--repeat N` | Repeats per scenario; default `1`. |
| `--max-turns N` | Override weather's 6 and agent-code's 10 turns; codegen stays single-turn. |
| `--max-tokens N` | Completion limit; default `1024`. |
| `--timeout SECONDS` | Per-request inactivity timeout; default `300`. |
| `--load-timeout SECONDS` | Load/readiness ceiling; default and maximum `600`. |
| `--no-warmup` | Skip the one 8-token warmup request per model. |
| `--no-unload`, `--keep-loaded` | Retain instances loaded by the run. Pre-existing instances are always retained. |
| `--parallel N` | Default `1`; values above 1 require preloaded models and mark timings contended. |
| `--plain`, `--no-color` | Plain mode / monochrome TUI; `NO_COLOR` is honored. |
| `--theme NAME` | Textual theme for the TUI (`run`, `show`); default `textual-dark`, `Ctrl+T` cycles at runtime. |
| `--verbose` | Include tool events in stderr or the TUI log. |
| `--transcript-dir DIR` | Additional transcript destination, grouped by run ID. |
| `--sort-by KEY` | `order`, `tok_s`, `ttft`, `total`, `load`, `model`; `cost` is rejected in LM Studio v1. |
| `--json PATH`, `--csv PATH`, `--markdown PATH` | Explicit export destinations. |
| `--baseline PATH` | Compare with a saved run directory or JSON file. |
| `--fail-on-regression` | Exit 3 for comparable regressions beyond thresholds; requires a baseline for runs. |
| `--run-store DIR` | Override the platform data directory. |
| `--config PATH` | Explicit TOML file instead of the project `llmsweep.toml`. |
| `--list`, `--show PATH` | Top-level compatibility aliases. |

Connection flags apply to `run`, `list`, and `doctor`; exports and baseline flags
apply to `run`, `show`, and `export`. Unknown or inapplicable flags are usage errors.

---

## Configuration & Precedence

Precedence is CLI > `LLMSWEEP_*` environment > project `./llmsweep.toml` >
`platformdirs.user_config_path("llmsweep") / "llmsweep.toml"` > defaults.
An explicit `--config PATH` replaces the project file. Keys are validated;
unknown keys and literal `api_key` values are errors. Provider settings use
`[providers.lmstudio]`. Deferred provider blocks are not activated in this release.

```toml
repeat = 3
scenarios = "weather,agent-code,codegen"
require_tool_use = false
keep_loaded = false

[providers.lmstudio]
base_url = "http://localhost:1234"
timeout = 300.0
load_timeout = 600.0
api_key_env = "LMSTUDIO_API_KEY"

[thresholds]
tok_s_regression_pct = 5.0
ttft_regression_pct = 5.0
```

| Environment variable | Meaning |
| --- | --- |
| `LMSTUDIO_API_KEY` | Preferred provider credential; `LLMSWEEP_API_KEY` is the fallback. |
| `LLMSWEEP_BASE_URL` | Server URL. |
| `LLMSWEEP_HOST`, `LLMSWEEP_PORT` | Hostname and port, when no base URL is configured. |
| `LLMSWEEP_TIMEOUT`, `LLMSWEEP_LOAD_TIMEOUT` | Request and readiness timeouts. |
| `LLMSWEEP_RUN_STORE` | Persistent run directory. |
| `LLMSWEEP_PLAIN` | Boolean (`true`, `false`, `1`, `0`, `yes`, `no`). |
| `NO_COLOR` | Disable colors in both renderers. |

Other root settings use the corresponding `LLMSWEEP_` uppercase name.
Credentials are redacted in error messages, logs, transcripts, and exports.
Redirects are disabled so credentials stay on the configured provider origin.

---

## Exit codes

0: execution completed (scoring can fail); 1: model error/cancellation; 2: usage, configuration, or initial connection error; 3: regression with `--fail-on-regression`; 4: authentication failure.
