# Clean Reinstall + Robust Test + Batched Ingestion — Plan & Runbook

> Personal runbook for John (jgh). Wipe the self-hosted Honcho on **Mando**,
> reinstall it configured correctly **up front**, validate the whole pipeline on a
> controlled 200-file test, then ingest the kb-proto-1 corpus in **John-controlled
> batches** so Mando isn't tied up for ~9 h straight. Decided 2026-06-11 after the
> import-quality audit (see `[[import-over-attribution]]` memory + below).
>
> **Boundary rule (non-negotiable):** we do **not** edit Honcho's `src/` or "fix"
> Honcho. We use it as-is and steer it only via **`.env` (operator config)** and the
> **public API**. All tooling we add lives in `_jgh_/honcho-import/`. Whatever
> `custom_instructions` can't steer, we accept — we don't patch Honcho.

## Why we're resetting

The kb-proto-1 transcript import contaminated the `john-cc` representation:
source-verified grounding audit (n=71) found only **~41% of conclusions grounded
in John**, ~51% over-interpreted, ~3% hallucinated. Root cause is the **import
feeding non-John content** into "user" turns (skill-injection ~60%, assistant-turn
attribution ~16%, pasted incidental-data ~18%) — **not** the deriver model
(qwen2.5:14b is clean, 0% degeneration). Parser fix for the skill-injection slice
is committed (`660b937`, `_jgh_/honcho-import/parse_transcripts.py`). The remaining
slices are addressed by deriver `reasoning.custom_instructions`. Rather than patch a
contaminated store, we start clean with all fixes in place.

## Decisions (locked 2026-06-11)

| Decision | Choice |
| --- | --- |
| Reset scope | **Full wipe** — `docker compose down -v` + re-bootstrap |
| Live `john`/`claude` `/clear`-hook memory | **Let it go** — not preserved; the hook rebuilds over time |
| Stack model (all agents) | **`qwen2.5:14b` everywhere** (deriver, dialectic ×5, summary, dream). Supersedes the stale `[[lan-topology]]` note that still says `gemma4:26b`. Basis: `honcho-import-benchmarks.md` Run 6 (0% garbage, 2.6× faster) + the Dialectic A/B. |
| Phase-2 test set | **200 files, stratified by size** (even strides across sessions sorted by msg count) |
| Phase-2 batching | **`FLUSH_ENABLED=true`** (per-message) to match the Run-6 quality baseline; bulk-batching deferred to Phase 3 |

## Mando state at reset time (captured 2026-06-11 via SSH)

- **Honcho:** install `/Users/johnhendricks/honcho/honcho`, branch `main` @ **`f75b336`**
  (`feat: add generate_jwt.py …(#757)`). Pin this commit as the install version.
  Services: api/database/redis healthy; **deriver stopped** (we stopped it during the audit).
- **Ollama:** runs as **Ollama.app** (`/Applications/Ollama.app/.../ollama serve`).
  Models pulled include `qwen2.5:14b` (9 GB) ✓ and `bge-large:latest` (670 MB) ✓.
  Config via 3 LaunchAgents: `com.user.ollama.{context,keepalive,maxmodels}.plist`.
  - ⚠️ `launchctl getenv OLLAMA_*` over **SSH returns empty** — SSH is a different
    launchd domain than the GUI Ollama.app session, so it can't see the app's env.
    Don't trust that as "unset." **Verify the real value** by loading qwen2.5:14b and
    checking `/api/ps` for `context_length` (want **32768**, per `[[deriver-tuning]]`).
- **`down -v` wipes only the Honcho Docker stack — Ollama (models + tuning) is
  untouched.** This is a Honcho-only reinstall.

## Reachability

SSH `mando` works from Bossk (`ssh mando "…"`), so the assistant can drive the Mando
shell **and** the API. Destructive steps still get an explicit go first. (Note: a
remote `cat .env` is blocked by the harness as a secret-dump — build the new `.env`
from this doc, don't echo the live one.)

---

## Phase 1 — Clean install, configured correctly up front

### 1a. The fresh `.env` (full)

```bash
LOG_LEVEL=INFO
AUTH_USE_AUTH=false
LLM_OPENAI_API_KEY=ollama          # dummy; Ollama ignores it, client needs a value

# ---- Deriver — qwen2.5:14b + anti-repetition penalty (Run 6) ----
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=qwen2.5:14b
DERIVER_MODEL_CONFIG__FREQUENCY_PENALTY=0.3
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# ---- Summary ----
SUMMARY_MODEL_CONFIG__TRANSPORT=openai
SUMMARY_MODEL_CONFIG__MODEL=qwen2.5:14b
SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# ---- Dream (two specialists) ----
DREAM_DEDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_DEDUCTION_MODEL_CONFIG__MODEL=qwen2.5:14b
DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
DREAM_INDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_INDUCTION_MODEL_CONFIG__MODEL=qwen2.5:14b
DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1

# ---- Dialectic — ALL 5 reasoning levels ----
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

# ---- Embeddings (1024-dim, bge-large) ----
EMBED_MESSAGES=true
EMBEDDING_VECTOR_DIMENSIONS=1024
EMBEDDING_MODEL_CONFIG__TRANSPORT=openai
EMBEDDING_MODEL_CONFIG__MODEL=bge-large:latest
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE=never
EMBEDDING_MAX_INPUT_TOKENS=512

# ---- Processing cadence ----
DERIVER__FLUSH_ENABLED=true        # per-message; matches Run-6 quality baseline
# Phase-3 bulk speed (only after quality is confirmed):
#   DERIVER__FLUSH_ENABLED=false
#   DERIVER__REPRESENTATION_BATCH_MAX_TOKENS=8192   # ~6 msgs/call, ~6x fewer calls
```

Also keep `docker-compose.yml`'s api port bound LAN-wide (`0.0.0.0:8000:8000`) per
`[[honcho-mando-ollama-setup]]`.

### 1b. Reinstall sequence (Honcho only; Ollama untouched)

```bash
cd ~/honcho/honcho
docker compose down -v                 # ⬅ DESTRUCTIVE: wipes all Honcho data/volumes
#   ... write the .env above ...
docker compose up -d database          # wait until 'database' is healthy
docker compose run --rm --no-deps --entrypoint /app/.venv/bin/python api scripts/provision_db.py
docker compose run --rm --no-deps --entrypoint /app/.venv/bin/python api scripts/configure_embeddings.py --yes
docker compose up -d                   # all services incl. deriver
```

### 1c. Verify (don't declare clean until all pass)

```bash
curl -s http://192.168.0.225:8000/health                      # {"status":"ok"}
docker compose ps                                             # api/database/redis/deriver healthy
docker exec honcho-deriver-1 printenv | grep DERIVER_MODEL_CONFIG   # MODEL=qwen2.5:14b, FREQUENCY_PENALTY=0.3
# force-load qwen2.5:14b, then confirm ctx:
curl -s http://192.168.0.225:11434/api/ps                    # qwen2.5:14b context_length == 32768
# confirm 1024-dim vector columns (psql) — see honcho-mando-ollama-setup.md Part 4
```

### 1d. Create workspace + set anti-over-attribution custom_instructions (API)

Create `default`, then `PUT /v3/workspaces/default` with `reasoning.enabled=true`
and `reasoning.custom_instructions` (config field — **not** a source change; capped
by `DERIVER__MAX_CUSTOM_INSTRUCTIONS_TOKENS`, default 2000):

```
When forming conclusions about this peer, attribute a fact to them only if they
personally authored or stated it in their own messages. Do not attribute content
from assistant replies, system notifications, task notifications, tool output, or
injected skill/command definitions to the peer. Do not infer the peer's
preferences, ownership, habits, or identity from incidental data such as file
paths, directory listings, process lists, or pasted command output — only from
what the peer explicitly says or does.
```

> ⚠️ `docker compose up -d <svc>` (recreate), **never** `restart`, to apply `.env`
> changes — `restart` reuses the old container env. See `[[deriver-tuning]]` §4.

---

## Phase 2 — Robust 200-file test ("water through the pipe")

New tool **`_jgh_/honcho-import/run_eval.py`** orchestrating end-to-end, repeatable:

1. Select 200 sessions, **stratified by size** (even strides across sessions sorted
   by message count — reuse `bench_transcript_slice.py`'s sampling).
2. Parse with the **patched** `parse_transcripts.py` (skill-injection stripped).
3. Import with **reasoning ON** + `custom_instructions` set (peers `john-cc`
   `observe_me=true` / `claude-cc` `observe_me=false`).
4. Poll the queue to full drain (`poll_deriver.py` / `get_queue_status`).
5. Evaluate and append a row to `honcho-import-benchmarks.md`.

**Metrics (the benchmark / pass-criteria):**

| Metric | Target |
| --- | --- |
| Skill-body leakage | **0** |
| Mechanical degeneration (loops/char-spray) | **0%** |
| **Grounding (source-verified audit, n≈40-70 via subagents)** | **≥80% grounded** (up from 41%) |
| Throughput | msgs/s, s/derivation-task, total wall-time, drain curve |
| Volume | conclusions/msg |

The grounding audit reuses the method proven in this effort: random sample →
fetch each conclusion's source session → judge GROUNDED / PARTIAL /
OVER-INTERPRETED / HALLUCINATED with attribution scrutiny. This validates the
**whole** setup (parser fix + custom_instructions + clean config) before scaling.

---

## Phase 3 — John-controlled batched ingestion

New tool **`_jgh_/honcho-import/ingest_batch.py`**:

- Loads the next ~200 **not-yet-loaded** sessions (reasoning ON), against a **real
  loaded-ledger** (fixes the old bug where `import_groups.py` read but never wrote
  `loaded_sessions.json` → re-runs would duplicate). Use Honcho's actual loaded
  `full-*` sessions as ground truth, not a stale local file.
- Reports drain + a quick per-batch quality gate (degeneration + small grounding
  spot-check); then **stops**.
- **Control model:** John runs one batch when Mando is free; derivation drains in a
  bounded window; next batch on John's go. Mando never tied up ~9 h straight.
- Optional Phase-3 speed: flip to bulk batching (`FLUSH_ENABLED=false`,
  `REPRESENTATION_BATCH_MAX_TOKENS=8192`) once Phase-2 quality is confirmed; revert
  to `FLUSH_ENABLED=true` afterward for live `/clear`-hook responsiveness.

---

## What stays peripheral (boundary check)

| Action | Mechanism | Touches Honcho `src/`? |
| --- | --- | --- |
| Model/embedding/penalty/cadence config | `.env` (operator config) | No |
| Reasoning on/off + `custom_instructions` | Public API (`PUT /v3/workspaces/…`) | No |
| Wipe / reinstall | `docker compose` + bootstrap scripts | No |
| Parse / import / eval / batch tooling | `_jgh_/honcho-import/*.py` (HTTP API only) | No |
| Clear data / cancel pending work | API: delete conclusions / delete sessions (**not** DB row surgery) | No |

## Open confirms before executing Phase 1

- Keep batching at the Run-6 baseline (`FLUSH_ENABLED=true`) for the test? (current plan: yes)
- `custom_instructions` wording good, or tighten?

## Related

- `[[honcho-mando-ollama-setup]]` — base deployment, the 1024-dim bootstrap dance, the `-mlx`/reasoning-model JSON pitfall.
- `[[deriver-tuning]]` — `num_ctx=32768`, `KEEP_ALIVE=-1`, `MAX_LOADED_MODELS=6`, the `up -d` vs `restart` trap.
- `[[deriver-repetition-and-sampling]]` — `frequency_penalty=0.3` rationale.
- `[[honcho-import-benchmarks]]` — Run 6 (qwen2.5:14b green light) + the Dialectic A/B; append Phase-2 results here.
- `import-over-attribution` memory — the audit findings + the layered fix this plan implements.
```
