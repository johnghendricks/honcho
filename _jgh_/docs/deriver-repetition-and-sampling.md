# Deriver Repetition Loops — `repeat_penalty` vs `frequency_penalty`

> Personal note for John (jgh). What to do when a **local Ollama deriver model
> degenerates into a repetition loop** and writes garbage conclusions, and how to
> apply an anti-repetition penalty given Honcho's OpenAI-compatible plumbing.

## Symptom

A stored conclusion whose `content` collapses into a single token repeated
thousands of times, e.g.:

```
"... is working on a build-plan at Phase 6 ...', ' much/ much/ much/ much/
much/ much/ much/ much/ much/ much/ much/ much/ much/ ...
```

This is a classic small-local-model **degeneration loop** (greedy/low-penalty
sampling getting stuck). The deriver still marks the queue task `processed=true`
with no error, so the bad conclusion lands silently in the peer's
representation. (Observed 2026-06-10 on peer `john`, conclusion
`bfBImDmAR9I5lpfRtIE3B` — deleted via `mcp__honcho__delete_conclusion`.)

### Cleanup of an existing bad conclusion

```
delete_conclusion(peer_id=<observer>, target_peer_id=<observed>, conclusion_id=<id>)
```

For self-conclusions `observer == observed` (e.g. both `john`).

## Why you can't just set `repeat_penalty`

`repeat_penalty` is an **Ollama-native** sampling option. It is **not** an
OpenAI-API parameter, and Honcho talks to Ollama over the OpenAI-compatible
endpoint (`:11434/v1`). Two structural reasons it can't be threaded from Honcho
config:

1. `ConfiguredModelSettings` (`src/config.py:161`) has no `repeat_penalty` field
   and no generic provider-params passthrough on operator-facing model config.
2. The OpenAI backend only forwards a fixed allow-list of sampling knobs:
   `top_p, top_k, frequency_penalty, presence_penalty, seed`
   (`src/llm/request_builder.py:19` `build_config_extra_params` →
   `src/llm/backends/openai.py:316`). Even an `extra_body.repeat_penalty` would
   be dropped by Ollama's OpenAI shim, which ignores unknown top-level fields.

So there are exactly two ways to apply an anti-repetition penalty to the deriver.

## Option A — bake `repeat_penalty` into an Ollama model variant (true repeat_penalty)

Run on **Mando** (where Ollama lives):

```bash
# 1. Modelfile inheriting the base + setting the native penalty
cat > Modelfile.deriver <<'EOF'
FROM gemma4:26b           # confirmed deriver/text-gen base model (via /api/ps)
PARAMETER repeat_penalty 1.15
EOF

# 2. Build the variant
ollama create gemma4-26b-deriver -f Modelfile.deriver
```

Then point only the deriver at it (leaves dialectic/summary/dream untouched):

```bash
DERIVER_MODEL_CONFIG__MODEL=gemma4-26b-deriver
```

Apply with `docker compose up -d deriver` (recreate — `docker compose restart`
won't re-read `.env`; see the ⚠️ box under Option B). Pros: true `repeat_penalty`
(multiplicative), deriver-scoped. Cons: a Modelfile variant to maintain per
base-model upgrade.

## Option B — `frequency_penalty` through Honcho config (no Ollama changes) ✅ chosen

`frequency_penalty` **is** in Honcho's allow-list, so it flows end to end:

`[deriver.model_config].frequency_penalty` → `ModelConfig.frequency_penalty` →
`build_config_extra_params()` (`request_builder.py:31`) → forwarded by the OpenAI
backend (`openai.py:319`) → reaches Ollama as `frequency_penalty`, which Ollama
honors.

Apply on **Mando** (deployment config is not in the Bossk checkout — only
`.env.template` / `config.toml.example` live here).

**`.env` form:**

```bash
DERIVER_MODEL_CONFIG__FREQUENCY_PENALTY=0.3
```

**`config.toml` form** — add to the existing `[deriver.model_config]` block:

```toml
[deriver.model_config]
transport = "openai"
model = "gemma4:26b"
frequency_penalty = 0.3
```

Recreate the deriver worker to apply: **`docker compose up -d deriver`**.

> ⚠️ **`restart` ≠ `up -d` — this is exactly why `0.3` never reached Mando.**
> `docker compose restart deriver` reuses the existing container and does **NOT**
> re-read `.env`, so the new `frequency_penalty` is silently dropped and the
> deriver keeps running with no penalty. Use **`docker compose up -d deriver`** to
> recreate the container with the new env. **Always verify it landed:**
> `docker exec honcho-deriver-1 printenv | grep DERIVER_MODEL_CONFIG`
> (must show `DERIVER_MODEL_CONFIG__FREQUENCY_PENALTY=0.3`). Confirmed applied
> this way on Mando 2026-06-11.

### Tuning the value

`frequency_penalty` runs **−2.0 → 2.0** (additive per-token penalty) — *not* a
1:1 swap for `repeat_penalty 1.15` (multiplicative). Guidance:

| Value | Effect |
| --- | --- |
| `0.2` | Mild — use if conclusions start feeling clipped/unnatural |
| `0.3` | **Recommended start** — kills runaway loops, minimal extraction distortion |
| `0.5` | Stronger — use if repetition persists at 0.3 |

## Decision — Option B, now actually deployed (see update below)

Went with **Option B (`frequency_penalty = 0.3`)** — no Ollama Modelfile to
maintain, fully version-controllable in Honcho config, deriver-scoped. The caveat
was explicit: *revisit toward Option A only if the additive penalty proves
insufficient against loops.* As of **2026-06-11 this is live and verified on
Mando**; whether `0.3` is *sufficient* is pending Run 5 — see the update below.

## Update 2026-06-11 — Option B was never deployed (not "insufficient"); now live, re-testing

**Run 4 still looped — but Option B was never actually applied.** A 12-message
deriver benchmark on `gemma4:26b` (after the `num_ctx` 256K→32K cap, queue
healthy) produced 84 conclusions on peer `dbench`, of which **~15–20% degenerated
into the classic repetition loop** — e.g. `//note: //note: //note: …` ×hundreds
then `moderator/moderator/…`; `…mass of the scope of the mass of the scope…`;
`(is)s (is)s (is)s…`; `part number ascending within that stem,` ×hundreds. The
clean conclusions were accurate; the garbage clustered on **dense, structured
source messages** (skill/build-plan documentation — exactly what kb-proto-1 is
full of). See `honcho-import-benchmarks.md` Run 4. **Critically, Run 4 ran with
no anti-repetition penalty at all** — so it is *not* evidence that Option B
failed; it's the baseline garbage rate.

**Resolved 2026-06-11: `frequency_penalty` had never reached the deployed config.**
The Mando `.env` had no `frequency_penalty` line at all, and the running deriver
container's env confirmed it — Option B was never tested. Root cause: `docker
compose restart` does **not** re-read `.env` (it reuses the old container), so an
earlier "set it and restart" would have silently kept the old config anyway (see
the ⚠️ box under Option B). `frequency_penalty=0.3` is **now genuinely applied and
verified in the container** (recreated with `docker compose up -d deriver`).
Whether `0.3` (additive) is *strong enough* against these hard loops is the open
question for **Run 5** — Option A below is the fallback if it still leaks.

**This still blocks the kb-proto-1 import until a penalty-live benchmark comes
back clean** — at the no-penalty ~15–20%, a full 6,386-message run would write
1,000+ garbage conclusions into representations, silently (`processed=true`, no
error). Don't import until Run 5 confirms a clean rate.

### → Next step — re-test Option B (Run 5); Option A is the fallback

**First:** with `frequency_penalty=0.3` now live, re-run `bench_deriver.py` from
bossk (Run 5) and check `list_conclusions` for repetition. If the loop rate drops
to ~0, Option B wins — no Modelfile, one resident model — and we're done.

**If `0.3` still leaks** (try `0.5` first — see the tuning table above; it's a
one-line `.env` change + `docker compose up -d deriver`), fall back to **Option
A**: fold the multiplicative `repeat_penalty` into the **same deriver-only
Modelfile variant** that `deriver-tuning.md` uses for the `num_ctx` cap — one
model carries both fixes:

```dockerfile
# Modelfile.deriver
FROM gemma4:26b
PARAMETER num_ctx 8192          # deriver batches are tiny — small KV cache
PARAMETER repeat_penalty 1.15   # multiplicative; kills degeneration loops
```
```bash
ollama create gemma4-26b-deriver -f Modelfile.deriver
DERIVER_MODEL_CONFIG__MODEL=gemma4-26b-deriver   # deriver .env, then `docker compose up -d deriver`
```

If `1.15` still leaks loops on the densest content, step to `1.2`. Then re-run
`bench_deriver.py` and confirm `list_conclusions` is clean **and** check whether
per-task time dropped (degenerate calls run to the max-output-token cap, so
killing loops should also speed the deriver up). Append the result to
`honcho-import-benchmarks.md`.

## Related

- `deriver-tuning.md` — the `num_ctx` cap + smaller-model levers (the Modelfile
  variant above is shared with that doc).
- `honcho-import-benchmarks.md` — Run 4 has the benchmark + garbage-rate evidence.
- `honcho-mando-ollama-setup.md` — the broader Mando/Ollama runbook (model
  choice, the `-mlx`/reasoning-model JSON-failure pitfall, embeddings).
