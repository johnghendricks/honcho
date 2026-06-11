# Agent Model Swap — non-deriver agents → qwen2.5:32b (Mando)

> Handoff for the Mando session (jgh). Apply on Mando, where the Docker stack and
> Ollama live. Bossk is driving the A/B benchmark; this doc is the config side.

## Goal

Move **everything except the deriver** off `gemma4:26b` onto `qwen2.5:32b`.

- **Deriver stays on `qwen2.5:14b`** — it won the deriver bench (Run 6: 0 % garbage,
  ~7.7 s/task; see `honcho-import-benchmarks.md`). Do **not** touch it.
- **Embeddings stay on `bge-large`.** Do not touch.
- Rationale: we want a non-reasoning qwen-family model for the user-facing /
  structured paths. The qwen3.x line is hybrid-*thinking* (risk of `<think>`
  leakage breaking tool-call / structured-output JSON on Ollama's OpenAI shim, plus
  latency on the `minimal` dialectic tier's 250-token budget). `qwen2.5:32b` is the
  proven non-reasoning sibling of the deriver winner — no thinking risk.

`qwen2.5:32b` is already pulled on Mando (confirmed 2026-06-11).

## The swap — 8 keys (`.env` form)

Only the `MODEL` value changes; each already has its `OVERRIDES__BASE_URL`
pointing at Ollama (`http://host.docker.internal:11434/v1`), leave those intact.

```bash
# Dialectic — all 5 reasoning tiers (level names are lowercase)
DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=qwen2.5:32b
DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL=qwen2.5:32b
DIALECTIC_LEVELS__medium__MODEL_CONFIG__MODEL=qwen2.5:32b
DIALECTIC_LEVELS__high__MODEL_CONFIG__MODEL=qwen2.5:32b
DIALECTIC_LEVELS__max__MODEL_CONFIG__MODEL=qwen2.5:32b

# Summarizer
SUMMARY_MODEL_CONFIG__MODEL=qwen2.5:32b

# Dreamer — both specialists
DREAM_DEDUCTION_MODEL_CONFIG__MODEL=qwen2.5:32b
DREAM_INDUCTION_MODEL_CONFIG__MODEL=qwen2.5:32b
```

Find them all (and confirm nothing else is missed) with:

```bash
grep -rn 'gemma4:26b' .env config.toml   # expect exactly these 8 — and NOT the deriver line
```

## Apply + verify

These agents run in the **api** container (Dialectic is inline on the request
path; Summary/Dream run in the worker if split out — recreate whichever hosts
them).

```bash
docker compose up -d api          # recreate — NOT `restart` (see ⚠️ below)
# + `docker compose up -d deriver` too IF summary/dream run in the worker

# verify the new model actually landed in the container env:
docker exec honcho-api-1 printenv | grep -E 'DIALECTIC_LEVELS|SUMMARY_MODEL|DREAM_'
# every line above must read qwen2.5:32b; DERIVER must still read qwen2.5:14b
```

> ⚠️ **`docker compose restart` does NOT re-read `.env`** — it reuses the old
> container and silently keeps the old model. Always **`up -d`** to recreate, then
> `printenv` to confirm. (This is the exact trap that wasted a deriver test cycle.)

Then **paste the `printenv` output back to the Bossk session** (or into
`terminal-share`) so it can run the qwen2.5:32b half of the A/B.

## A/B benchmark context (Bossk side)

Bossk captured the **gemma4:26b baseline** before the swap via
`bench_dialectic.py` (6 recall probes against peer `dbench-qwen`, `high` reasoning):

| Metric | gemma4:26b |
| --- | ---: |
| Steady mean / query | **26.7 s** (21–31 s) |
| Avg response | 1113 chars |
| Quality | well-grounded; correctly recalled F0.23 Phase 6/7, pre-commit hooks, git-worktree workflow, build-plan skill; even flagged a phase contradiction in the representation |

After the swap + verify, Bossk re-runs `python bench_dialectic.py qwen2.5-32b high`
and compares latency + response quality head-to-head. **The dialectic was already
healthy on gemma** — this swap is an optimization attempt, not a fix, so qwen2.5:32b
has to be faster and/or better-grounded to justify keeping it.

## Rollback

If qwen2.5:32b regresses (slower, or worse grounding), revert the 8 keys to
`gemma4:26b` and `docker compose up -d api`. Nothing else changed.

## Related

- `honcho-import-benchmarks.md` — deriver Runs 4–6 (why the deriver is on
  qwen2.5:14b) + the dialectic A/B results once both halves are in.
- `deriver-repetition-and-sampling.md` — the `restart` ≠ `up -d` trap, in detail.
- `honcho-mando-ollama-setup.md` — the reasoning-model JSON-failure pitfall that
  steered us away from qwen3.x for these agents.
