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
| Chat/reasoning model | `qwen2.5:14b` (all text-gen features) |
| Embeddings | 1024-dim, matching kb-proto-1 (`mxbai-embed-large` / `bge-large-en-v1.5` / `qwen3-embedding:0.6b`) |
| Auth | Off (`AUTH_USE_AUTH=false`) — trusted home LAN |
| API binding | `0.0.0.0:8000` (LAN-accessible) |

---

## Part 1 — Ollama on Mando

```bash
# Install if needed
brew install ollama        # or download Ollama.app from ollama.com

# Pull models
ollama pull qwen2.5:14b               # chat/reasoning (tool-calling capable)
ollama pull mxbai-embed-large         # embeddings — SWAP to match kb-proto-1
```

**Embedder note:** all three candidates (`mxbai-embed-large`,
`bge-large-en-v1.5`, `qwen3-embedding:0.6b`) are **1024-dim**, so Honcho's locked
dimension is 1024 either way — pull the exact tag kb-proto-1 uses.
`mxbai`/`bge-large` cap input at **512 tokens** (longer messages truncated when
embedded); **`qwen3-embedding:0.6b` handles ~32k tokens** and is the stronger
choice for Honcho if kb-proto-1 also uses it.

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

Create `.env` (full file — **find/replace `mxbai-embed-large` with your
kb-proto-1 embedder**):

```bash
LOG_LEVEL=INFO
AUTH_USE_AUTH=false

# ---- Ollama via OpenAI-compatible endpoint ----
LLM_OPENAI_API_KEY=ollama          # dummy; Ollama ignores it, but the client needs a value

# Deriver
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=qwen2.5:14b
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# Summary
SUMMARY_MODEL_CONFIG__TRANSPORT=openai
SUMMARY_MODEL_CONFIG__MODEL=qwen2.5:14b
SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# Dream (two specialists)
DREAM_DEDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_DEDUCTION_MODEL_CONFIG__MODEL=qwen2.5:14b
DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DREAM_INDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_INDUCTION_MODEL_CONFIG__MODEL=qwen2.5:14b
DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# Dialectic — must set ALL 5 reasoning levels
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=qwen2.5:14b
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__low__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL=qwen2.5:14b
DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__medium__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__medium__MODEL_CONFIG__MODEL=qwen2.5:14b
DIALECTIC_LEVELS__medium__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__high__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__high__MODEL_CONFIG__MODEL=qwen2.5:14b
DIALECTIC_LEVELS__high__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DIALECTIC_LEVELS__max__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__max__MODEL_CONFIG__MODEL=qwen2.5:14b
DIALECTIC_LEVELS__max__MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# ---- Embeddings (1024-dim, Ollama) ----
EMBED_MESSAGES=true
EMBEDDING_VECTOR_DIMENSIONS=1024
EMBEDDING_MODEL_CONFIG__TRANSPORT=openai
EMBEDDING_MODEL_CONFIG__MODEL=mxbai-embed-large
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE=never
# If your embedder caps at 512 tokens (mxbai/bge), uncomment to avoid silent truncation:
# EMBEDDING_MAX_INPUT_TOKENS=512
```

`DIMENSIONS_MODE=never` stops Honcho from sending a `dimensions=` parameter that
Ollama's embedding endpoint rejects.

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
  e.g. `qwen2.5:32b` for `DIALECTIC_LEVELS__max` (deep recall) while keeping
  `14b` for the high-volume deriver.
- **Embedding dimension is immutable after first boot** (enforced by
  `src/startup/embedding_validator.py`). Changing it requires a fresh deployment
  + re-embed; see `docs/v3/contributing/changing-embeddings.mdx`.

## Source references (verified during setup)

- `docker-compose.yml.example`, `Dockerfile`, `docker/entrypoint.sh`
- `.env.template`, `docs/v3/contributing/{configuration,self-hosting,changing-embeddings,troubleshooting}.mdx`
- `scripts/{provision_db,configure_embeddings}.py`, `src/startup/embedding_validator.py`
- `src/config.py` (`EmbeddingDimensionsMode = Literal["auto","always","never"]`)
- `mcp/README.md`, `mcp/src/{index,config}.ts`, `mcp/package.json`
