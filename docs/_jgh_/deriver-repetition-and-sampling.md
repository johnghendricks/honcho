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

Restart the deriver worker. Pros: true `repeat_penalty` (multiplicative),
deriver-scoped. Cons: a Modelfile variant to maintain per base-model upgrade.

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

Restart the deriver worker to reload config.

### Tuning the value

`frequency_penalty` runs **−2.0 → 2.0** (additive per-token penalty) — *not* a
1:1 swap for `repeat_penalty 1.15` (multiplicative). Guidance:

| Value | Effect |
| --- | --- |
| `0.2` | Mild — use if conclusions start feeling clipped/unnatural |
| `0.3` | **Recommended start** — kills runaway loops, minimal extraction distortion |
| `0.5` | Stronger — use if repetition persists at 0.3 |

## Decision

Went with **Option B (`frequency_penalty = 0.3`)** — no Ollama Modelfile to
maintain, fully version-controllable in Honcho config, deriver-scoped. Revisit
toward Option A only if additive penalty proves insufficient against loops.

## Related

- `honcho-mando-ollama-setup.md` — the broader Mando/Ollama runbook (model
  choice, the `-mlx`/reasoning-model JSON-failure pitfall, embeddings).
