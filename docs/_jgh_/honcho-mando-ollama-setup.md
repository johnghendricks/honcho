# Honcho on Mando — Self-Hosted Ollama + LAN Setup Runbook

> Personal runbook for John (jgh). Self-hosting Honcho on **Mando** (MacBook Pro
> M1 Max, 64GB) with fully-local Ollama models, exposed to the LAN, and hooked
> into VSCode/Claude Code on **Bossk** (Windows 11 Pro workstation).

## Topology

Mando (Mac) runs everything — Ollama (native, for Metal GPU), Honcho's 4
containers (Docker), and the Honcho MCP worker. Bossk (Windows) connects from
VSCode/Claude Code over the LAN.

**Key architecture point:** local Ollama models run **natively on macOS, not
inside Docker.** Docker on Apple Silicon can't use the Metal GPU, so the Honcho
containers reach Ollama on the host via `host.docker.internal:11434`.

### Decisions

| Choice | Value |
| --- | --- |
| LLMs | Fully local via Ollama (OpenAI-compatible endpoint) |
| Chat/reasoning model | `gemma4:12b` — **GGUF, not `-mlx`** (all text-gen features) |
| Embeddings | `bge-large:latest` (1024-dim) |
| Auth | Off (`AUTH_USE_AUTH=false`) — trusted home LAN |
| API binding | `0.0.0.0:8000` (LAN-accessible) |

> ### ⚠️ Critical: use GGUF models, never `-mlx`, for text-gen
>
> Honcho's deriver/summarizer/dreamer make **schema-constrained JSON** calls (the
> "minimal deriver" is a single structured-output LLM call). **Community `-mlx`
> model builds on Ollama silently ignore JSON-schema / grammar constraints** — they
> return free-text markdown instead of JSON. Honcho then logs `json_parser: Repair
> failed: Expecting value: line 1 column 1` and "Deriver generated zero
> observations", but the queue task still flips to `processed=true` **with no
> error** — a silent failure that looks like a working install producing no memory.
>
> **GGUF (non-`-mlx`) tags honor the schema and work.** Verified: `gemma4:12b` GGUF
> returns valid JSON; `gemma4:26b-mlx` and `qwen3.6:27b-mlx` returned markdown.
> Always pull the plain tag (`gemma4:12b`, not `gemma4:12b-mlx`). Embeddings are
> unaffected by this. `gemma4:12b` is small and conservative (extracts fewer
> conclusions per message) — for richer extraction pull GGUF `gemma4:26b` and
> swap the model name; just never the `-mlx` variant.

---

## Part 1 — Ollama on Mando

```bash
# Install if needed
brew install ollama        # or download Ollama.app from ollama.com

# Pull models — use the plain GGUF tags, NOT the -mlx builds (see warning above)
ollama pull gemma4:12b                 # chat/reasoning — GGUF, does structured output
ollama pull bge-large                  # embeddings — 1024-dim
```

**Embedder note:** `bge-large` is **1024-dim** and caps input at **512 tokens**
(longer messages are silently truncated when embedded — we set
`EMBEDDING_MAX_INPUT_TOKENS=512` below so the limit is explicit). Other 1024-dim
options if you'd rather match a different store: `mxbai-embed-large` (also 512-token
cap) or `qwen3-embedding:0.6b` (~32k token input — stronger for long messages).
Whatever you pick, Honcho's dimension is locked at 1024 on first boot.

**Critical: make Ollama listen on all interfaces.** By default it binds
`127.0.0.1`, which Docker containers cannot reach via `host.docker.internal`:

```bash
# Ollama.app (menubar):
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
# then fully quit and reopen Ollama.app

# Or, running from a terminal:
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Verify Docker can reach it (after Docker Desktop is running):

```bash
docker run --rm curlimages/curl -s http://host.docker.internal:11434/api/tags
```

You should see your pulled models. (macOS may prompt for firewall access — allow it.)

---

## Part 2 — Clone Honcho & write `.env`

```bash
git clone https://github.com/plastic-labs/honcho.git
cd honcho
cp docker-compose.yml.example docker-compose.yml
```

Create `.env` (full file — this is the verified-working config):

```bash
LOG_LEVEL=INFO
AUTH_USE_AUTH=false

# ---- Ollama via OpenAI-compatible endpoint ----
LLM_OPENAI_API_KEY=ollama          # dummy; Ollama ignores it, but the client needs a value

# Deriver
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=gemma4:12b
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# Summary
SUMMARY_MODEL_CONFIG__TRANSPORT=openai
SUMMARY_MODEL_CONFIG__MODEL=gemma4:12b
SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# Dream (two specialists)
DREAM_DEDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_DEDUCTION_MODEL_CONFIG__MODEL=gemma4:12b
DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DREAM_INDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_INDUCTION_MODEL_CONFIG__MODEL=gemma4:12b
DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# Dialectic — must set ALL 5 reasoning levels
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=gemma4:12b
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__low__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL=gemma4:12b
DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__medium__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__medium__MODEL_CONFIG__MODEL=gemma4:12b
DIALECTIC_LEVELS__medium__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__high__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__high__MODEL_CONFIG__MODEL=gemma4:12b
DIALECTIC_LEVELS__high__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__max__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__max__MODEL_CONFIG__MODEL=gemma4:12b
DIALECTIC_LEVELS__max__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# ---- Embeddings (1024-dim, Ollama / bge-large) ----
EMBED_MESSAGES=true
EMBEDDING_VECTOR_DIMENSIONS=1024
EMBEDDING_MODEL_CONFIG__TRANSPORT=openai
EMBEDDING_MODEL_CONFIG__MODEL=bge-large:latest
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE=never
# bge-large caps input at 512 tokens — set explicitly to avoid silent truncation
EMBEDDING_MAX_INPUT_TOKENS=512

# ---- Processing cadence ----
# Process each message batch immediately instead of waiting for
# REPRESENTATION_BATCH_MAX_TOKENS (default 1024) to accumulate. Good for a
# low-volume single-user setup so memories form promptly. Set to false (or
# remove) to restore token-batched processing — more efficient at high volume.
DERIVER__FLUSH_ENABLED=true
```

`DIMENSIONS_MODE=never` stops Honcho from sending a `dimensions=` parameter that
Ollama's embedding endpoint rejects.

> **Heads up on `FLUSH_ENABLED`.** With it unset (the default), the deriver won't
> form any conclusions until ~1024 tokens of conversation accumulate in a work
> unit — so a short test message appears to do nothing (queue task sits at
> `processed=false`). That's expected batching, not a failure. `FLUSH_ENABLED=true`
> processes every batch immediately, which is what you want for interactive
> single-user use and for verifying the install with one message.

**Make the API reachable on the LAN.** Edit `docker-compose.yml`, in the `api`
service change:

```yaml
    ports:
      - "0.0.0.0:8000:8000"     # was 127.0.0.1:8000:8000
```

Leave `database` and `redis` bound to `127.0.0.1` — no reason to expose
Postgres/Redis to the network.

---

## Part 3 — Bootstrap (the 1024-dim dance)

Honcho's migrations create the vector columns at the **default 1536 dims**, and
the API **crash-loops on boot** if the schema dim ≠ configured dim. Since we're
at 1024, run a one-time `ALTER` *before* the API starts. The default compose
entrypoint doesn't do this, so do it manually — **once, on a fresh install:**

```bash
docker compose build

# 1. Start only Postgres
docker compose up -d database
docker compose ps          # wait until 'database' is healthy

# 2. Run migrations, then shrink the vector columns 1536 -> 1024 (empty tables only)
docker compose run --rm --no-deps --entrypoint /app/.venv/bin/python api scripts/provision_db.py
docker compose run --rm --no-deps --entrypoint /app/.venv/bin/python api scripts/configure_embeddings.py --yes

# 3. Bring up everything
docker compose up -d
```

After this, normal lifecycle is just `docker compose up -d` / `down`. Only repeat
step 2 if the database volume is ever wiped.

---

## Part 4 — Verify Honcho

```bash
# Find Mando's LAN IP (note it for Bossk)
ipconfig getifaddr en0

docker compose ps                       # all four services up
curl http://localhost:8000/health       # {"status":"ok"}

# Real end-to-end test: create a workspace
curl -s -X POST http://localhost:8000/v3/workspaces \
  -H "Content-Type: application/json" -d '{"name":"default"}'

# Watch the deriver actually call Ollama (after creating a peer + message):
docker compose logs deriver --tail 30
```

From **Bossk**, confirm LAN reach: open `http://<MANDO-IP>:8000/docs` in a browser.

---

## Part 5 — Hook into VSCode / Claude Code (on Bossk)

Honcho ships an MCP server (`mcp/`, a Cloudflare Worker). Cleanest setup: run it
on Mando alongside Honcho; Bossk connects over the LAN.

**On Mando:**

```bash
cd honcho/mcp
bun install
echo 'HONCHO_API_URL=http://localhost:8000' > .dev.vars   # worker talks to local Honcho
bun run dev --ip 0.0.0.0 --port 8787                       # expose MCP to the LAN
```

**On Bossk**, register with Claude Code:

```powershell
claude mcp add honcho -- npx -y mcp-remote http://<MANDO-IP>:8787 --header "Authorization:Bearer local" --header "X-Honcho-User-Name:john"
```

- `Authorization: Bearer local` — the worker requires *some* bearer token, but
  with `AUTH_USE_AUTH=false` Honcho ignores the value. (Becomes a real scoped JWT
  when auth is turned on.)
- `X-Honcho-User-Name:john` — the peer identity VSCode sessions read/write as.
- Optional: `--header "X-Honcho-Workspace-ID:default"` to pin a workspace.

Restart Claude Code in VSCode → Honcho tools (`chat`, `search`,
`add_messages_to_session`, `get_representation`, …) appear alongside the
existing `kb_proto_1` tools.

> `bun run dev` is a dev server — fine to start with. For always-on, run under a
> process manager (`pm2`/`launchd`) or `bun run deploy` to Cloudflare.

---

## Notes & follow-ups

- **kb-proto-1 access:** Honcho reaching the knowledgebase works through the
  `kb_proto_1` MCP tools (model calls `kb_search` etc.), *not* by sharing
  vectors — so the embedder match is for consistency, not function. Auto-enriching
  Honcho's dialectic from kb is an application-layer wiring task (see the
  `honcho-integration` skill).
- **Security:** auth off means anything on the LAN can read/write all memory.
  To lock down: `AUTH_USE_AUTH=true` + `python scripts/generate_jwt_secret.py`,
  then issue scoped keys per client.
- **Per-feature model tuning:** every feature is independently configurable —
  e.g. GGUF `gemma4:26b` for `DIALECTIC_LEVELS__max` (deep recall) while keeping
  `gemma4:12b` for the high-volume deriver. (Always the GGUF tag, never `-mlx`.)
- **Embedding dimension is immutable after first boot** (enforced by
  `src/startup/embedding_validator.py`). Changing it requires a fresh deployment
  and re-embed; see `docs/v3/contributing/changing-embeddings.mdx`.

## Troubleshooting

- **"Deriver generated zero observations" / no memory forms, but no error.**
  Almost always an `-mlx` text-gen model — it ignores the JSON-schema constraint
  and returns markdown, so `json_parser` logs `Repair failed: Expecting value:
  line 1 column 1` and the queue task still marks `processed=true`. Fix: switch to
  the GGUF tag (`gemma4:12b`, not `gemma4:12b-mlx`) and restart `api` + `deriver`.
  Confirm the model honors structured output:
  `curl -s localhost:11434/api/chat -d '{"model":"gemma4:12b","stream":false,"messages":[{"role":"user","content":"Extract facts: John lives in Boston."}],"format":{"type":"object","properties":{"facts":{"type":"array","items":{"type":"string"}}},"required":["facts"]}}'`
  — it must return JSON, not markdown bullets.
- **Test message seems ignored (queue task stays `processed=false`).** Expected if
  `FLUSH_ENABLED` is unset — the deriver waits for ~1024 accumulated tokens. Set
  `DERIVER__FLUSH_ENABLED=true` (see Part 2) to process immediately.
- **Message create returns `peer_id` field required.** The messages API expects
  `peer_id`, not `peer_name`:
  `curl -X POST .../sessions/s1/messages -d '{"messages":[{"peer_id":"john","content":"..."}]}'`.
- **Watch the deriver work:** `docker compose logs deriver -f` (look for the
  `PERFORMANCE` panel with `Observation Count`), and `ollama ps` should show the
  text-gen model load when a representation task runs.

## Source references (verified during setup)

- `docker-compose.yml.example`, `Dockerfile`, `docker/entrypoint.sh`
- `.env.template`, `docs/v3/contributing/{configuration,self-hosting,changing-embeddings,troubleshooting}.mdx`
- `scripts/{provision_db,configure_embeddings}.py`, `src/startup/embedding_validator.py`
- `src/config.py` (`EmbeddingDimensionsMode = Literal["auto","always","never"]`,
  `DeriverSettings.REPRESENTATION_BATCH_MAX_TOKENS` / `FLUSH_ENABLED`)
- `src/deriver/queue_manager.py` (`get_and_claim_work_units` — token-batch gating)
- `src/llm/structured_output.py`, `src/llm/backends/openai.py` (schema repair path)
- `mcp/README.md`, `mcp/src/{index,config}.ts`, `mcp/package.json`

## Verified-working state (2026-06-10)

Confirmed end-to-end on Mando: all 4 containers healthy, `/health` ok, API on
`192.168.0.225:8000`, `bge-large` storing 1024-dim vectors, `gemma4:12b` deriver
producing conclusions, and the dialectic `/chat` endpoint answering from memory.
Install lives at `~/honcho/honcho`.
