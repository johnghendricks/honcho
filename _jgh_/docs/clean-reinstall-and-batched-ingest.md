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

## Status (2026-06-12)

- **Phase 1 — DONE.** Clean wipe + reinstall executed on Mando: `down -v`, fresh
  `.env` (qwen2.5:14b everywhere, 1024-dim bge-large, `FREQUENCY_PENALTY=0.3`,
  ctx 32768), all migrations + `configure_embeddings.py`, `default` workspace with
  `reasoning.enabled=true` + the 703-char anti-over-attribution `custom_instructions`.
  Queue empty, 0 `full-*` sessions. (old `.env` backed up to `.env.bak.preinstall-2026-06-12`.)
- **Corpus — regenerated (twice).** `full_kb.json` rebuilt with the patched parser:
  first pass **0 skill-injection leakage**; second pass (2026-06-12) adds the
  **continuation/auto-compact-summary strip** → **0 continuation leakage** too. Now
  **590 sessions** (604 raw transcripts on Bossk; the `~/.claude/projects` dir has
  fewer than the earlier 640/618 snapshot). Pre-strip copy kept at `full_kb.preStrip.bak.json`.
- **Deriver config — SETTLED.** Grounding bench (see `honcho-import-benchmarks.md`
  §"Grounding sweep") proved **model size is not the lever** (32b ≈ 14b, worse
  leakage) and the **parser continuation-strip cleared the ≥80 % bar**: same 8
  sessions, unchanged 14b, **75.0 % → 82.8 % grounded**, over-attribution 13.1 % →
  5.4 %, worst session `f1a03124` 6 OVER/4 leak → 0/0. Mando reverted to qwen2.5:14b.
- **Ingest tool — built (`ingest_batch.py`).** Single resumable/pausable/batched
  tool serving both Phase 2 and Phase 3 (see those sections). Supersedes the old
  `run_eval.py` + `ingest_batch.py` split and `load_to_honcho.py`/`import_groups.py`.
- **Next:** quality gate met — run the full batched ingest of `full_kb.json` via
  `ingest_batch.py` (Phase 2 = batch 0, then Phase 3).

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

## Ablation result — assistant context is NOT the contamination driver (2026-06-12)

Before committing to a structural import change, ran the 3-arm assistant-context
ablation (`_jgh_/honcho-import/ablation_assistant_context.py`): 50 matched
stratified sessions loaded into 3 arms in ws `ablation`, model +
custom_instructions held constant, **only session composition varied**. Drained
on Mando's current deriver; source-verified grounding audit on **8 matched
sessions/arm (376 john explicit conclusions)** via blind Claude-sonnet judges
(`build_audit_payload.py` → `audit_bundles/group_*.json` → `aggregate_audit.py`).

| Arm | composition | n | GROUNDED | OVER+HALL |
| --- | --- | --- | --- | --- |
| A | john + claude verbatim (incl `[tools:]`) | 132 | 22.0% | 65.9% |
| B | john solo (no claude peer) | 124 | 12.1% | 72.6% |
| C | john + claude, `[tools:]` stripped | 120 | 21.7% | 65.0% |

- **A ≈ C (over-rate z=0.15):** the `[tools:]` markers contribute nothing — stripping them is pointless.
- **A vs B over-interpretation z=−1.15 (n.s.):** removing claude context does NOT reduce over-attribution (slightly worse).
- **A vs B grounded rate z=2.09 (p≈0.04):** paired sessions are *significantly more grounded* than solo. Claude's turns anchor what john's terse commands ("yes, proceed", "commit and push") refer to; without them the deriver fills gaps with speculation.

**Decision (refutes this runbook's earlier premise):** over-interpretation is NOT
caused by assistant/tool context bleeding into john's self-rep. **Do NOT isolate
john from claude** in the Phase-3 import (arm-B shape) — it would *hurt* grounding.
Keep paired sessions (current import shape).

**The real contamination lives in john's OWN user turns** (common to all arms →
flat 65–73% floor):
1. **Skill-injection in user turns — was 14.9% (502/3375) of `full_kb.json` user
   turns** (raw skill bodies, `Base directory for this skill: …`). The committed
   `parse_transcripts.py` fix strips these. ✅ **DONE 2026-06-12:** `full_kb.json`
   regenerated with the patched parser → **0 leakage**, 640→618 sessions (22
   all-noise sessions dropped). Single biggest, now-applied lever.
2. **Over-generalization from terse commands / incidental data** (file paths,
   pasted output). custom_instructions (held constant here) did NOT prevent it
   (65%+ over) → per the decision rule the lever is **custom_instructions tuning
   and/or a stronger deriver model**, not import-shape surgery.

Caveats: derived on Mando's current pre-reinstall deriver (exact model unverified
this session); strict rubric (skill-injection and any non-USER-turn support both
graded OVER) inflates absolute rates — the **relative** arm comparison is the
result. n≈125/arm. Artifacts: `ablation_setup.json`, `audit_payload.json`,
`audit_bundles/`, `audit_final.json`.

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

## Phase 1 — Clean install, configured correctly up front  ✅ DONE 2026-06-12

> Executed exactly as below. Recorded here as the install spec / rebuild recipe.

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
what the peer explicitly says or does. When you do form a conclusion, stay close
to what the message supports: prefer specific, source-traceable facts over broad
generalizations about the peer's character, expertise, or intentions.
```

> ⚠️ `docker compose up -d <svc>` (recreate), **never** `restart`, to apply `.env`
> changes — `restart` reuses the old container env. See `[[deriver-tuning]]` §4.

---

## Phase 2 — Robust 200-file test ("water through the pipe")

Phase 2 is **batch 0 of the final ingest** — same tool, same shape (per John's
"the test mirrors the final ingestion queue"). Tool:
**`_jgh_/honcho-import/ingest_batch.py`** (see §"The ingest tool" below).

1. `python ingest_batch.py --source full_kb.json --dry-run` — preview the plan.
   Batch 0 = 200 sessions, **size-stratified** across the corpus (the indicative
   set); the patched parser already stripped skill-injection.
2. `python ingest_batch.py --source full_kb.json --batches 1` — load batch 0.
   Peers ensured `john-cc` `observe_me=true` / `claude-cc` `observe_me=false`;
   workspace `reasoning.enabled` verified True. Per-file timings → SQLite ledger.
3. Watch the deriver drain: `python ingest_batch.py --source full_kb.json --status`
   (or `get_queue_status`). Add `--drain-between-batches` to the load to block
   until drained.
4. Evaluate: source-verified grounding audit (method below) and append a row to
   `honcho-import-benchmarks.md`.

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

Same tool as Phase 2 — just keep running batches:
`python ingest_batch.py --source full_kb.json` (all remaining), or `--batches N`
for N at a time, optionally `--drain-between-batches` so Mando idles at each
boundary.

- **Control model:** John runs one (or N) batch(es) when Mando is free; derivation
  drains in a bounded window; next batch on John's go. Mando never tied up ~9 h
  straight. Pause anytime: drop a `PAUSE` file or Ctrl-C → finishes the current
  file, flushes the ledger, exits clean. Re-run (even after a Bossk reboot) to
  resume — the SQLite ledger drives the skip; partial/crashed files are
  delete-then-reloaded so messages never duplicate.
- **Ground truth = SQLite ledger** (`ingest_ledger.db`, `status='done'`),
  reconciled against Honcho's live `full-*` sessions — fixes the old bug where
  `import_groups.py` read but never wrote `loaded_sessions.json`.
- Optional Phase-3 speed: flip to bulk batching (`FLUSH_ENABLED=false`,
  `REPRESENTATION_BATCH_MAX_TOKENS=8192`) once Phase-2 quality is confirmed; revert
  to `FLUSH_ENABLED=true` afterward for live `/clear`-hook responsiveness.

---

## The ingest tool — `_jgh_/honcho-import/ingest_batch.py`

One resumable/pausable/batched tool for both phases. Supersedes `run_eval.py`,
`load_to_honcho.py`, `import_groups.py`, and the `loaded_sessions.json` ledger.

**Flags:**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source` | `~/.claude/projects` | A single `.jsonl`, a folder of transcripts, or a pre-parsed `.json` corpus (e.g. `full_kb.json`). |
| `--batch-size` | `25` | Files per batch. Drives **stable** global batch numbers (same source + size ⇒ same batches across runs). |
| `--batches` | `0` (all) | Max batches to process this run. `--batches 1` = batch 0 only (Phase 2). |
| `--deriver` | `true` | Run the deriver on this batch. `false` sets `reasoning.enabled=false` **per-session** on the `full-*` sessions → messages load without derivation (scoped to this ingest; workspace flag + live data untouched; **not retroactive** — re-ingest to derive later). |
| `--drain-between-batches` | off | Block until the queue empties at each batch boundary. |
| `--dry-run` | off | Print the plan; no writes. |
| `--status` | off | Print the tally + timing stats; no writes. |
| `--reset` | off | **DANGER:** delete all `full-*` sessions and drop the ledger. |

**Behavior:**

- **Unit** = one session = one top-level `.jsonl`. Folder globbing is
  **non-recursive** — `subagents/` and `tool-results/` subdirs are excluded (they
  are not the peer's words; ingesting them re-contaminates john-cc).
- **Stratified order:** sessions sorted by size; **batch 0 is an even-stride
  representative sample** of the whole source; remaining batches cover the rest in
  size order.
- **Idempotent:** a file is marked `done` only after all its message-POSTs
  succeed; orphan/partial sessions are delete-then-reloaded on resume.
- **observe_me:** `john-cc=true`, `claude-cc=false`.

**SQLite ledger (`ingest_ledger.db`)** — the tally + troubleshooting record:

| Table | Columns of note |
| --- | --- |
| `files` | per session: `status` (`done`/`error`), `n_msgs`, `n_chars`, `parse_secs`, `transfer_secs` (POST wall-time), `started_at`/`completed_at`, `error`. |
| `batches` | per loaded batch: `transfer_secs` **vs** `drain_secs` (derivation wall-time) — tells a slow POST from a slow deriver. |
| `runs` | per invocation: source, batch_size, files/msgs loaded, status. |

`--status` reports done/pending/error totals, per-batch progress, transfer vs
drain timings, and recent errors.

---

## What stays peripheral (boundary check)

| Action | Mechanism | Touches Honcho `src/`? |
| --- | --- | --- |
| Model/embedding/penalty/cadence config | `.env` (operator config) | No |
| Reasoning on/off + `custom_instructions` | Public API (`PUT /v3/workspaces/…`) | No |
| Wipe / reinstall | `docker compose` + bootstrap scripts | No |
| Parse / import / eval / batch tooling | `_jgh_/honcho-import/*.py` (`ingest_batch.py`, HTTP API only) | No |
| Ingestion ledger / tally | local `ingest_ledger.db` (SQLite, on Bossk) — not Honcho data | No |
| Clear data / cancel pending work | API: delete conclusions / delete sessions (**not** DB row surgery) | No |

## Resolved confirms (pre-Phase-1)

- Batching at the Run-6 baseline (`FLUSH_ENABLED=true`) for the test — **yes** (in `.env`).
- `custom_instructions` wording — **kept as written** (703 chars, set on `default`).

## Related

- `[[honcho-mando-ollama-setup]]` — base deployment, the 1024-dim bootstrap dance, the `-mlx`/reasoning-model JSON pitfall.
- `[[deriver-tuning]]` — `num_ctx=32768`, `KEEP_ALIVE=-1`, `MAX_LOADED_MODELS=6`, the `up -d` vs `restart` trap.
- `[[deriver-repetition-and-sampling]]` — `frequency_penalty=0.3` rationale.
- `[[honcho-import-benchmarks]]` — Run 6 (qwen2.5:14b green light) + the Dialectic A/B; append Phase-2 results here.
- `import-over-attribution` memory — the audit findings + the layered fix this plan implements.
```
