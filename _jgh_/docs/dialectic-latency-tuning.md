# Dialectic Latency Tuning — Fast Interactive Queries on Local Ollama

> Personal guide for John (jgh). Why a `chat` (dialectic) query against the
> self-hosted Mando/Ollama stack can take **minutes**, and how to get it down to
> seconds. Companion to `honcho-mando-ollama-setup.md` and
> `deriver-repetition-and-sampling.md`.

## TL;DR

The setup isn't broken — latency is dominated by **(1) the reasoning level you
pick** and **(2) repeated large-context prefill on a heavy local model**. For
interactive queries: use `reasoning_level=minimal`, keep Ollama models resident,
and run a *small* model for the dialectic while leaving the big model on the
background agents.

## Why it's slow

The **dialectic is the only tool-*using* agent**. At each reasoning level it runs
a **sequential tool loop** — every iteration is a full LLM generation on the
local model, and each generation prefills up to the dialectic input budget
(default `MAX_INPUT_TOKENS = 100000`). So latency ≈
`(tool_iterations + 1) × per-call-prefill+decode`.

| Level | `MAX_TOOL_ITERATIONS` | Output cap | Toolset |
| --- | --- | --- | --- |
| `minimal` | **1** | 250 tokens | 2 tools (`search_memory`, `search_messages`) |
| `medium` | 2 | global | full |
| `high` | 4 | global | full (7 tools) |
| `max` | 10 | global | full |
| `low` | **5** | global | full |

Source: `config.toml.example:148` / `src/config.py:887` (`_default_dialectic_levels`).

> ⚠️ **The levels are NOT monotonic.** `low` is set to **5** iterations — *more*
> than `high` (4). Only `minimal` (1) and `medium` (2) are genuinely "fast."
> Don't reach for `low` expecting speed.

On a 26B-class model on an M1 Max, large-context prefill is the bottleneck;
doing it 4–5× back-to-back is where the "5 minutes" came from. (The MCP client
times out at 60s, but the server keeps grinding to completion regardless.)

## Confirmed via network introspection (2026-06-10)

Honcho exposes no config endpoint — only `/health` and `/metrics` (no model
labels in this build). The settings live server-side. **Ollama is the
introspection surface**: query it directly over the LAN (Mando =
`192.168.0.140`):

```bash
curl -s http://192.168.0.140:11434/api/tags   # installed models
curl -s http://192.168.0.140:11434/api/ps     # loaded right now + ctx + VRAM
curl -s http://192.168.0.140:11434/api/show -d '{"name":"gemma4:26b"}'  # params/num_ctx
```

Firing a `minimal` dialectic call, then checking `/api/ps`, gave ground truth:

```
gemma4:26b       | ctx = 262144 (256K!) | 18.1 GB | expires ~5 min
bge-large:latest | ctx = 512            |  0.7 GB
```

Three confirmed facts:

1. **Dialectic model = `gemma4:26b`** (not `qwen2.5:14b` — old note was stale).
   So the heavy-model fixes below apply at full force.
2. **No `keep_alive`** — before the call, `/api/ps` showed *zero* models loaded.
   Every query cold-loads 18 GB of weights, then unloads ~5 min later. Confirmed,
   not theoretical.
3. **🔴 Loaded context window = 262144 (256K).** A quarter-million-token context
   means huge KV-cache setup and slow prefill on *every* call, even trivial ones.
   New finding — see fix #5.

## Fixes, in priority order

### 1. Pick the right reasoning level (free, instant)

For interactive / ad-hoc queries, pass `reasoning_level=minimal` (1 call, 2
tools, 250-token cap) — seconds, not minutes. Use `medium` (2 iterations) when
you want more thoroughness. Reserve `high`/`max` for offline/background
questions where latency doesn't matter.

### 2. Keep Ollama models resident (hidden reload cost)

If the deriver and dialectic use different models, or Ollama unloads after its
default 5-min idle, **every dialectic call may pay a cold model-reload** (tens of
seconds for a 26B). On **Mando**:

```bash
OLLAMA_KEEP_ALIVE=-1   # never unload; keep models hot in memory
```

On 64GB this matters most when a big deriver model and a big dialectic model
compete for memory and swap in/out between calls.

### 3. Run a *small* model for the dialectic (the real "different model" answer)

A 26B is fine for the **deriver/dreamer** (background, latency-insensitive) but
heavy for an interactive tool loop. Honcho supports **per-level model config**,
so split them: a fast 7–8B instruct model handles the dialectic's "pick a tool /
write a grounded answer" job well and prefills several× faster.

### 4. Lower the dialectic input budget

`DIALECTIC_MAX_INPUT_TOKENS=100000` lets each tool-loop call prefill an enormous
context. For personal-scale data, drop it to **16k–32k** — cuts prefill time
directly with little quality loss.

### 5. Cap the Ollama context window (`num_ctx`) — confirmed 256K

`/api/ps` showed `gemma4:26b` loaded with **`ctx=262144`**. That's a massive
KV-cache allocation and slow per-call prefill regardless of how short the prompt
is. Cap it on **Mando** (Ollama-side). Two ways:

```bash
# Global default for every model Ollama loads:
OLLAMA_CONTEXT_LENGTH=32768
```

…or bake it into a model variant (also where `repeat_penalty` would go — see
`deriver-repetition-and-sampling.md`):

```bash
cat > Modelfile.gemma-ctx <<'EOF'
FROM gemma4:26b
PARAMETER num_ctx 32768
EOF
ollama create gemma4-26b-ctx32k -f Modelfile.gemma-ctx
```

Keep `num_ctx` comfortably above `DIALECTIC_MAX_INPUT_TOKENS` (fix #4) so prompts
aren't silently truncated. After changing it, re-check with `/api/ps`.

## Config block (apply on Mando)

> Deployment config lives on Mando, not in the Bossk checkout (only
> `.env.template` / `config.toml.example` are here). Apply one of the forms
> below, then **restart the API server** (dialectic runs inline in the API
> process, so the deriver worker restart is not what reloads this).

Fast model = **`gemma4:12b`** (already installed; GGUF instruct, same family as
the big model, *not* a reasoning/`-mlx` build — see the runbook's pitfall
warning). Big model = **`gemma4:26b`** (confirmed via `/api/ps` as the current
dialectic model).

### `.env` form

```bash
# --- Keep models hot (Ollama-side, set wherever Ollama's env is defined) ---
OLLAMA_KEEP_ALIVE=-1

# --- Trim the dialectic prefill budget ---
DIALECTIC_MAX_INPUT_TOKENS=24000

# --- Fast small model on the interactive levels ---
DIALECTIC_LEVELS__MINIMAL__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__MINIMAL__MODEL_CONFIG__MODEL=gemma4:12b
DIALECTIC_LEVELS__MEDIUM__MODEL_CONFIG__TRANSPORT=openai
DIALECTIC_LEVELS__MEDIUM__MODEL_CONFIG__MODEL=gemma4:12b

# --- Leave high/max on the big model (background-grade thoroughness) ---
# DIALECTIC_LEVELS__HIGH__MODEL_CONFIG__MODEL=gemma4:26b
# DIALECTIC_LEVELS__MAX__MODEL_CONFIG__MODEL=gemma4:26b
```

### `config.toml` form

```toml
[dialectic]
MAX_INPUT_TOKENS = 24000

[dialectic.levels.minimal]
MAX_TOOL_ITERATIONS = 1
MAX_OUTPUT_TOKENS = 250
TOOL_CHOICE = "auto"

[dialectic.levels.minimal.model_config]
transport = "openai"
model = "gemma4:12b"        # fast small model (installed, GGUF instruct)

[dialectic.levels.medium]
MAX_TOOL_ITERATIONS = 2

[dialectic.levels.medium.model_config]
transport = "openai"
model = "gemma4:12b"        # fast small model (installed, GGUF instruct)

# high / max keep the big model — background-grade thoroughness
[dialectic.levels.high.model_config]
transport = "openai"
model = "gemma4:26b"

[dialectic.levels.max.model_config]
transport = "openai"
model = "gemma4:26b"
```

## Expected impact

| Change | Effect |
| --- | --- |
| `minimal` instead of `high` | ~4–5× fewer sequential generations |
| `OLLAMA_KEEP_ALIVE=-1` | Removes per-call cold-reload stalls (18 GB load — confirmed) |
| `gemma4:12b` dialectic model | Several× faster prefill/decode per call |
| `MAX_INPUT_TOKENS` 100k → 24k | Proportionally cheaper prefill |
| `num_ctx` 262144 → 32768 | Smaller KV cache + faster per-call prefill |

Stacked, an interactive `minimal` query should land in **seconds**.

## Resolved (2026-06-10)

Dialectic model **confirmed `gemma4:26b`** via `/api/ps` — so fixes #2/#3 apply
at full force. Two extra issues found in the same check: no `keep_alive`
(cold-loads every call) and a **256K loaded context window** (fix #5). The
`qwen2.5:14b` figure in older notes was stale.
