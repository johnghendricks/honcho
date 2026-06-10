# Honcho — Codebase Scan Report

_Generated 2026-06-10. Branch `_jgh_`, server version **3.0.9**._

## 1. What this is

Honcho is **memory infrastructure for stateful AI agents** — a FastAPI service that
ingests messages/events, reasons over them in the background, and serves back peer
representations, session context, search, and natural-language insights ("conclusions").
Core abstraction is the **Peer** (humans and agents unified), grouped into
**Workspaces** and **Sessions**. Available managed (`api.honcho.dev`) or self-hosted.

This repo is the **core service**. Python + TypeScript client SDKs live in `sdks/`.

## 2. Shape of the code

| Area | LOC | Notes |
|---|---:|---|
| `src/utils/` | 6,906 | agent tools, summarizer, search, tokens, formatting |
| `src/crud/` | 5,711 | per-resource DB ops + RepresentationManager |
| `src/llm/` | 4,430 | provider-agnostic LLM layer (anthropic/openai/gemini) |
| `src/telemetry/` | 3,557 | CloudEvents, Prometheus, Sentry, reasoning traces |
| `src/dreamer/` | 2,853 | memory consolidation specialists + reasoning trees |
| `src/deriver/` | 2,626 | background queue consumer (separate process) |
| `src/routers/` | 2,284 | FastAPI `/v3` route handlers |
| `src/config.py` | 1,377 | single large pydantic-settings module |
| root `src/*.py` | 3,576 | main, models, db, security, embedding_client, exceptions |

- **Two processes** sharing Postgres + Redis: the **API server** (enqueues work,
  hosts the Dialectic agent inline) and the **deriver worker** (queue consumer that
  runs Deriver, Summarizer, Dreamer, plus an in-process Reconciler scheduler).
- **Four LLM agents**: Deriver (single structured-output call per batch),
  Dialectic (the one true tool-loop agent, 5 reasoning tiers), Dreamer (multi-specialist
  consolidation off the queue), Summarizer (two-tier, direct call).
- **25 Alembic migrations**; text/nanoid PKs; composite-FK multi-tenancy
  (`workspace_name` in nearly every FK — cross-workspace leakage is structurally impossible).
- **Tech-debt markers**: only **9** TODO/FIXME/HACK across `src/` — very clean.

## 3. LLM / agent layer (current state)

- **Backends**: Anthropic, OpenAI, Gemini behind a registry (`src/llm/registry.py`),
  unified via `honcho_llm_call()`. Per-agent `MODEL_CONFIG` with fallback chains.
- **Default model for every agent is currently `gpt-5.4-mini` (transport `openai`)** —
  Deriver, all 5 Dialectic tiers, both Dreamer specialists, and the Summarizer
  (inherits Deriver config). _(`src/config.py:779-923`, `1182-1203`)_
- **Fallback is pinned per-attempt** via `AttemptPlan` so a settled tool loop doesn't
  bounce back to primary on a stream-final retry; fallback reasoning params come from the
  fallback config itself, enabling true cross-provider fallback _(`src/llm/runtime.py:128-175`)_.
- **Dialectic tiers** (`minimal`→`low`→`medium`→`high`→`max`): tool-iteration caps
  1 / 5 / 2 / 4 / 10; `minimal` uses a 2-tool set (`search_memory`, `search_messages`),
  the rest use the full 7-tool set. CLAUDE.md's description verified accurate.
- **Streaming + tool calling** is disallowed unless `stream_final_only=True`
  (raises `ValidationException`) — a sharp edge worth knowing.

## 4. Tests & CI

- **160 test files, ~59.5k LOC**, mirroring `src/`. pytest with `asyncio_mode=auto`,
  parallelized via `pytest-xdist` (`-n auto`; disabled for alembic).
- **Infra**: Postgres 15 + **pgvector required**; Redis is **mocked with fakeredis**
  (real Redis not needed for the unit suite). Embeddings/LLM/vector-store mocked by default;
  a blocklist (`bench/`, `alembic/`, `unified/`, `live_llm/`, `llm/`) opts out of mocking.
- **Live provider tests** gated behind `--live-llm` + API keys.
- **SDK tests**: Python SDK runs against the in-process TestClient; **TypeScript SDK
  spins up a real uvicorn server** and is orchestrated **only** through pytest
  (`uv run pytest tests/ -k typescript` — never `bun test` directly).
- **CI**: `unittest.yml` (Postgres service, `pytest -x`), `unified-tests.yml`
  (Fly.io ephemeral runner, 90-min budget), `staticanalysis.yml` (basedpyright).
- **Well covered**: routes/API, deriver queue, advanced filters/search, telemetry, migrations.
  **Thin**: cache layer (no dedicated tests), webhook event triggers, vector-store
  backends, reconciler, DB-failure edge cases.

## 5. Recent activity (theme: DB resilience under load)

The last several releases are dominated by **connection-pool/Postgres saturation** work:

- **3.0.9**: reverted 3.0.8's connection-checkout retry + `HonchoAsyncSession`; switched to
  single-attempt acquisition with `DB_CONNECT_TIMEOUT_SECONDS` (fail fast, let the pooler drain).
- Added **deriver poll jitter** (`DERIVER_POLLING_STARTUP_JITTER_SECONDS`,
  `DERIVER_POLLING_JITTER_RATIO`) so co-started instances don't poll in lockstep.
- **3.0.8** (now partly reverted): adaptive deriver polling backoff, new Prometheus pool
  gauges (`db_pool_connections`, `db_queries_in_flight`).
- Other recent: configurable CORS origins, `generate_jwt.py` for scoped JWTs,
  peer-card prompts reframed as stable identity markers, reverse pagination restored.

The branch list shows heavy parallel feature work (custom deriver instructions, PDF/Mistral
OCR ingest, workspace chat, two-phase dialectic cost, MCP server improvements).

## 6. Discussion threads (where I'd want your steer)

1. **Self-hosting w/ local models** — defaults are `gpt-5.4-mini`/OpenAI. Your setup runs
   Honcho + Ollama on Mando; worth discussing the OpenAI-compatible `base_url` path and
   which agents are cheap enough to run locally vs. which need a frontier model.
2. **DB saturation saga** — 3.0.8→3.0.9 flip-flopped on the retry strategy. Is the
   current "fail fast + jitter + pooler drain" the intended end state, or interim?
3. **Test coverage gaps** — cache, webhook triggers, reconciler, DB-failure paths are thin.
   Candidate for hardening?
4. **`config.py` at 1,377 lines** — the nested MODEL_CONFIG merge validator is the main
   complexity hotspot. Worth decomposing?
5. **Single-model monoculture** — every agent on one model. Is per-agent tiering
   (cheap deriver, strong dialectic) on the roadmap?
