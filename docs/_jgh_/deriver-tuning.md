# Deriver Tuning — Fixing the Slow / Stalled Deriver on Mando

> Personal how-to for John (jgh). The Deriver on the self-hosted Mando/Ollama
> stack is the blocking bottleneck for any real import (Run 3 of
> `honcho-import-benchmarks.md`: ~27 s/task, queue not draining, ~48 h projected
> for kb-proto-1). This is the runbook to make it healthy and fast **before**
> turning derivation on for 6 K messages. Companion to
> `dialectic-latency-tuning.md`, `deriver-repetition-and-sampling.md`, and
> `honcho-mando-ollama-setup.md`.

## TL;DR

Three independent levers, in priority order. **Do #1 first — it's free and
likely the dominant fix.**

1. **Cap `num_ctx`** (Ollama-side). The deriver model loads with `ctx=262144`
   (256K) — confirmed on the shared `gemma4:26b` via `/api/ps`. The deriver's
   *actual* input per call is ~1–2 K tokens (batch cap is 1024), so a 256K KV
   cache is pure waste and slow prefill on every call. Cap it to **32768**
   globally (`OLLAMA_CONTEXT_LENGTH`) — covers the deriver and the dialectic at
   once, one resident model.
2. **(Optional) smaller deriver model** — `gemma4:26b → gemma4:12b`. The deriver
   is a single structured-output call per batch, background and
   latency-insensitive, but quality-sensitive. `gemma4:12b` (already installed,
   GGUF instruct) decodes several× faster and does structured output fine. Try it
   only if #1 alone isn't fast enough.
3. **Restart + verify the worker is actually draining.** Run 3 showed stale
   claims (worker hung mid-`gemma` call, 3 tasks stuck in-progress, backlog not
   claimed). A restart clears stale `ActiveQueueSession` claims; then confirm the
   queue count is going *down*.

Bonus lever for the **bulk import** specifically: pack more messages per call
(§5) — fewer, larger LLM calls instead of ~6,400 one-message calls.

---

## Why it's slow (deriver-specific)

The minimal deriver makes **one structured-output LLM call per batch**
(`src/deriver/deriver.py`, single call — not a tool loop). Cost ≈
`num_batches × per-call(prefill + decode)`. Two things inflate it on Mando:

- **Per-call prefill is dominated by the 256K `num_ctx`**, not the prompt. Even a
  1 K-token batch pays the 256K KV-cache setup. This is the same pathology the
  dialectic doc found, and it hits the deriver harder because the deriver does
  *many* small calls.
- **Batch count is high.** `REPRESENTATION_BATCH_MAX_TOKENS = 1024` (default) and
  kb-proto-1 averages ~1,200 tok/msg **> 1024**, so most batches = 1 message →
  ~6,400 calls for the full project. `WORKERS = 1` → serial.

So the two multipliers are *per-call time* (levers #1, #2) and *call count*
(lever #5).

---

## 1. Cap `num_ctx` (the big one)

`num_ctx` is an **Ollama-native** setting — Honcho does not send it (it's not in
the OpenAI-backend allow-list; see `deriver-repetition-and-sampling.md` §"Why you
can't just set repeat_penalty"). So cap it **on Mando, Ollama-side.**

**Recommended — global cap (covers deriver + dialectic, one resident model):**

```bash
# Wherever Ollama's env is defined on Mando (launchctl for Ollama.app, or the
# shell that runs `ollama serve`):
OLLAMA_CONTEXT_LENGTH=32768
```

Then **restart Ollama and force the model to reload** so the new ctx takes
effect (a loaded model keeps its old ctx; with `OLLAMA_KEEP_ALIVE=-1` it won't
unload on its own):

```bash
# fully quit & reopen Ollama.app, or restart `ollama serve`, then:
curl -s http://192.168.0.225:11434/api/ps    # confirm ctx is now 32768, not 262144
```

32768 sits comfortably above the deriver's real input (≤25000
`DERIVER__MAX_INPUT_TOKENS`, batches ≤1024 by default) **and** above the
dialectic's `MAX_INPUT_TOKENS=24000`, so neither is silently truncated.

> **Deriver-only, even smaller (advanced).** Because deriver batches are tiny,
> the deriver alone would be happy at `num_ctx 8192`. To cap it *lower than* the
> dialectic without touching the dialectic, bake a deriver-only variant and point
> only the deriver at it — at the cost of a **second resident model** (≈18 GB +
> the dialectic's 18 GB; fits 64 GB but wasteful):
>
> ```bash
> # On Mando:
> cat > Modelfile.deriver-ctx <<'EOF'
> FROM gemma4:26b
> PARAMETER num_ctx 8192
> EOF
> ollama create gemma4-26b-deriver -f Modelfile.deriver-ctx
> ```
> ```bash
> DERIVER_MODEL_CONFIG__MODEL=gemma4-26b-deriver   # deriver .env
> ```
>
> Prefer the **global 32768** unless you're chasing the last bit of deriver
> latency — one model stays resident and there's no Modelfile to maintain across
> base-model upgrades. (If you already maintain a deriver variant for
> `repeat_penalty` per `deriver-repetition-and-sampling.md` Option A, just add the
> `num_ctx` PARAMETER to that same Modelfile.)

## 2. (Optional) Smaller deriver model

Point only the deriver at `gemma4:12b` — leaves dialectic/summary/dream on the
big model. The deriver is background-grade, so the smaller model's job (extract
structured conclusions from a ~1 K-token batch) is well within a 12B's range.

**`.env` form** (Mando — config lives on Mando, not in the Bossk checkout):

```bash
DERIVER_MODEL_CONFIG__MODEL=gemma4:12b
# transport + base_url already set in the verified .env; only MODEL changes.
```

**`config.toml` form** — edit the existing `[deriver.model_config]` block (this
is also where `frequency_penalty = 0.3` already lives from the repetition fix):

```toml
[deriver.model_config]
transport = "openai"
model = "gemma4:12b"          # was gemma4:26b
frequency_penalty = 0.3       # keep — anti-repetition (deriver-repetition doc)
```

Restart the **deriver worker** to reload (deriver config reloads on the deriver
process restart — it is *not* the API process).

> ⚠️ **Don't use a `-mlx` / reasoning build for the deriver.** They break
> structured-output JSON — see the pitfall in `honcho-mando-ollama-setup.md`.
> `gemma4:12b` is a plain GGUF instruct model and is safe. If conclusion quality
> drops noticeably after the swap, revert to `gemma4:26b` and rely on #1 + #5.

## 3. Keep the model hot

Same fix as the dialectic doc — avoid paying a cold 18 GB (or 12 GB) reload
between tasks:

```bash
OLLAMA_KEEP_ALIVE=-1     # Ollama-side, on Mando
```

If you split models (deriver `12b`, dialectic `26b`), both stay resident
(≈12 + 18 GB + bge) — fine on 64 GB.

## 4. Restart the worker & clear stale claims

Run 3's stall was a worker hung mid-call with stale `ActiveQueueSession` claims
(3 tasks pinned in-progress, backlog never re-claimed). After applying #1–#3:

```bash
# On Mando:
docker compose ps                          # is `deriver` up?
docker compose logs deriver --tail 100     # errors, or hung mid-gemma call?
docker compose restart deriver             # clears stale claims; reloads config
docker compose logs deriver --tail 30 -f   # watch it claim + process
```

`DERIVER.STALE_SESSION_TIMEOUT_MINUTES = 5` means an abandoned claim is
reclaimable after 5 min, but a restart is immediate.

## 5. Bulk-import lever — pack more messages per call

For the **one-time kb-proto-1 derivation** (6,386 msgs), call *count* is the
dominant cost. Two knobs cut it:

```bash
DERIVER__FLUSH_ENABLED=false                      # batch instead of per-message
DERIVER__REPRESENTATION_BATCH_MAX_TOKENS=8192     # ~6 msgs/call instead of ~1
```

`REPRESENTATION_BATCH_MAX_TOKENS` is capped at 16384 and must be
`≤ DERIVER__MAX_INPUT_TOKENS` (25000). At 8192, kb-proto-1's ~1,200-tok messages
pack ~6 per call → roughly **~6× fewer calls** (~6,400 → ~1,100). `num_ctx 32768`
(from #1) easily holds an 8192-token batch.

Trade-off: larger prefill per call, but far fewer calls — net throughput win for
a bulk backfill. **Revert to `FLUSH_ENABLED=true` (small batch) afterward** for
interactive single-user use, so live `/clear`-hook captures form conclusions
promptly instead of waiting for a batch to fill.

---

## Apply-and-verify checklist (on Mando)

```bash
# 1. num_ctx cap (Ollama env) + keep hot
OLLAMA_CONTEXT_LENGTH=32768
OLLAMA_KEEP_ALIVE=-1
#    restart Ollama, then confirm:
curl -s http://192.168.0.225:11434/api/ps         # ctx == 32768 ?

# 2. (optional) smaller deriver model + (bulk) bigger batches — in deriver .env:
#    DERIVER_MODEL_CONFIG__MODEL=gemma4:12b
#    DERIVER__FLUSH_ENABLED=false
#    DERIVER__REPRESENTATION_BATCH_MAX_TOKENS=8192

# 3. restart the worker (reloads config, clears stale claims)
docker compose restart deriver

# 4. re-benchmark the real per-call number (needs a healthy worker)
python ~/.claude/honcho-import/bench_deriver.py <session_stem> <N>
python ~/.claude/honcho-import/poll_deriver.py <internal_session_id> <N>
```

Health check — the queue count must go **down**:

```bash
# via MCP (camelCase) or REST /queue/status (snake_case — see Run 3 tooling note)
mcp__honcho__get_queue_status        # pending should drop as tasks complete
docker compose logs deriver --tail 30 -f
```

## Expected impact

| Change | Effect |
| --- | --- |
| `num_ctx` 262144 → 32768 | Smaller KV cache + faster prefill on **every** deriver call — the dominant per-call win |
| `gemma4:26b → 12b` (deriver only) | Several× faster decode/prefill; small extraction-quality risk |
| `OLLAMA_KEEP_ALIVE=-1` | No cold model reload between tasks |
| `REPRESENTATION_BATCH_MAX_TOKENS` 1024 → 8192 (bulk) | ~6× fewer calls on the kb-proto-1 backfill |
| restart worker | Clears the Run-3 stall (stale claims), queue actually drains |

Stacked, the goal is to pull the observed ~27 s/task floor down enough that the
6 K-message backfill is hours, not the theoretical ~48 h — then run
`bench_deriver.py` for the real, post-fix per-call number and append it to
`honcho-import-benchmarks.md`.

## Open / next

- **Get the real per-call number after the fix** — Run 3 never got a clean one
  (worker wasn't draining). This unblocks the kb-proto-1 derivation-cost
  projection (currently theoretical ~18–53 h).
- **`WORKERS > 1`?** Serial `WORKERS=1` is the other multiplier. More workers =
  more concurrent `gemma` calls competing for the single Ollama instance on Mando
  — likely contention-bound, not a clear win. Test before assuming it helps.
