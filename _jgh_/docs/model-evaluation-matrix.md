# Honcho Local-Model Evaluation Matrix (Mando / Ollama)

> Personal evaluation log for John (jgh). Systematic comparison of the local
> Ollama models on **Mando** across every Honcho subsystem that calls an LLM, to
> pick the right model for each job rather than standardizing on one by default.
> Companion to `honcho-mando-ollama-setup.md` and `honcho-import-benchmarks.md`.
> Target: `http://192.168.0.140:8000`, ws `default`, self-hosted on Mando
> (M1 Max, 64 GB). **Status: Dreamer Phase 2 COMPLETE (5 finalists, 50 trials +
> 404 blind-graded conclusions, 2026-06-16). Recommendation: split the dreamer —
> deduction → qwen3:30b, induction → gemma4:26b. Awaiting checkpoint before
> Deriver/Summarizer/Dialectic rounds.**

## Why this exists

The earlier benchmarks (`honcho-import-benchmarks.md`) settled the **deriver** on
`qwen2.5:14b` and A/B'd the **dialectic**, then *standardized the whole stack on
qwen2.5:14b* for ops simplicity. Investigating why **dreams produced no peer card
and no inductive conclusions** revealed that decision had a hole: the **dreamer**
(an agentic tool-loop, unlike the single-call deriver) was never benchmarked, and
qwen2.5:14b sometimes fails to call its persistence tools mid-loop. This doc
re-opens the question for **every** model-using subsystem with a fresh, much
larger candidate pool (John pulled ~20 new models on 2026-06-15).

## Subsystems under test

| Subsystem | Call shape | Harness | Discriminating metric |
| --- | --- | --- | --- |
| **Deriver** | one structured-output call/batch | `bench_load`→`bench_audit_payload`→blind judges→`aggregate_bench` | % grounded, leakage, #concl, s/task, garbage |
| **Summarizer** | one direct call | *(new harness, TBD)* | faithfulness/coverage, latency |
| **Dreamer** | agentic tool-loop (deduction → induction) | `dream_sweep.sh` (this round) | **tool-loop completion / reliability**, #ded/#ind, peer-card write, latency, VRAM |
| **Dialectic** | agentic tool-loop, 5 reasoning tiers | `bench_dialectic.py` | grounding/quality, tool use, latency |
| ~~Embeddings~~ | — | *(deferred)* | see note below |

**Embeddings deferred:** the vector dimension is locked at **1024** on first boot
(`src/startup/embedding_validator.py`); A/B-ing embedders requires a throwaway
fresh deployment + full re-index of ~5.6 K msgs + ~5.3 K docs each. Treated as a
separate study, not part of this drop-in text-gen sweep.

## Methodology notes (important)

- **Single-shot dream results are nondeterministic.** The original "qwen2.5:14b
  can't do induction" finding was **n=1**: a re-run produced 2 inductive
  conclusions. Verdicts therefore use **multiple trials per model** and report a
  **reliability rate**, not a single binary. (Phase 1 = 3 trials; finalists get
  more.)
- **Isolation:** the dreamer sweep runs on the real `default/john-cc/john-cc`
  collection but only ever deletes/regenerates the **regenerable dream layer**
  (deductive + inductive docs + `peer_card`) between trials. The 5,324 explicit
  conclusions and all messages are never modified, so every trial starts from an
  identical, realistic state.
- **Clean latency:** model pulls and other GPU work must be idle during a run —
  concurrent downloads inflated dream latency ~25% in an early validation.
- **Quality grading is blind:** for aspects with a quality axis (deriver,
  dreamer-finalists, dialectic), conclusions are graded blind against source
  (the established `grades_*.json` method) — model identity hidden from the judge.
- **Config-swap procedure (validated):** edit the relevant `*_MODEL_CONFIG__MODEL`
  line(s) in Mando's `.env`, `docker compose up -d --no-deps --force-recreate
  <service>`, verify via `printenv`. Deriver/dreamer/summarizer live in the
  `deriver` container; dialectic in `api`. (A reconcile on 2026-06-15 also fixed a
  stale `.env` that diverged from the running system and pointed the whole stack
  at the broken `gemma4:26b-mlx`; see the setup runbook.)

## Candidate models (text-gen) — capability triage (2026-06-15)

All advertise the `tools` capability except the two ChatQA models. "Advertised
`tools` ≠ reliable in a multi-step loop" — that's the whole reason for the trials.

| Model | Arch | Params | Native ctx | tools | thinking | In sweep |
| --- | --- | ---: | ---: | :-: | :-: | :-: |
| qwen2.5:14b | qwen2 | 14.8B | 32K | ✅ | — | ✅ |
| qwen2.5:32b | qwen2 | 32.8B | 32K | ✅ | — | ✅ |
| qwen3:4b | qwen3 | 4.0B | 262K | ✅ | ✅ | ✅ |
| qwen3:8b | qwen3 | 8.2B | 40K | ✅ | ✅ | ✅ |
| qwen3:14b | qwen3 | 14.8B | 40K | ✅ | ✅ | ✅ |
| qwen3:30b | qwen3moe | 30.5B | 262K | ✅ | ✅ | ✅ |
| qwen3.5:4b | qwen35 | 4.7B | 262K | ✅ | ✅ | ✅ |
| qwen3.5:9b | qwen35 | 9.7B | 262K | ✅ | ✅ | ✅ |
| qwen3.5:27b | qwen35 | 27.8B | 262K | ✅ | ✅ | ✅ |
| qwen3.5:35b | qwen35moe | 36.0B | 262K | ✅ | ✅ | ✅ |
| qwen3.6:27b | qwen35 | 27.8B | 262K | ✅ | ✅ | ✅ |
| gemma4:12b | gemma4 | 11.9B | 262K | ✅ | ✅ | ✅ |
| gemma4:26b | gemma4 | 25.8B | 262K | ✅ | ✅ | ✅ |
| gemma4:31b | gemma4 | 31.3B | 262K | ✅ | ✅ | ✅ |
| llama3.1:8b | llama | 8.0B | 131K | ✅ | — | ✅ |
| llama3.1:70b | llama | 70.6B | 131K | ✅ | — | ✅ |
| llama3.3:70b | llama | 70.6B | 131K | ✅ | — | ✅ |
| nemotron3:33b | nemotron | 33.0B | 131K | ✅ | ✅ | ✅ |
| nemotron-3-nano:4b | nemotron | 4.0B | 262K | ✅ | ✅ | ✅ |
| nemotron-3-nano:30b | nemotron | 31.6B | 1M | ✅ | ✅ | ✅ |
| deepseek-r1:8b | qwen3 | 8.2B | 131K | ✅ | ✅ | ✅ |
| deepseek-r1:14b | qwen2 | 14.8B | 131K | ✅ | ✅ | ✅ |
| deepseek-r1:32b | qwen2 | 32.8B | 131K | ✅ | ✅ | ✅ |
| command-r | cohere | 32.3B | — | ✅ | — | ✅ (pulled) |
| mistral-small3.2 | mistral | 24.0B | — | ✅ | — | ✅ (pulled) |
| gpt-oss:20b | gptoss | 20.9B | — | ✅ | ✅ | ✅ (pulled) |
| ~~llama3-chatqa:8b~~ | llama | 8B | 8K | ❌ | — | excluded (no tools) |
| ~~llama3-chatqa:70b~~ | llama | 71B | 8K | ❌ | — | excluded (no tools) |

**26 models in the sweep.** Pulled on request to fill architectural gaps:
`command-r` (Cohere, built for agentic tool-use/RAG), `mistral-small3.2`,
`gpt-oss:20b` (OpenAI open-weight).

---

## Dreamer — Phase 1 screen (3 trials × 26 models)

**Harness:** `_jgh_/honcho-import/dream_sweep.sh` (runs on Mando). Per model:
swap `DREAM_DEDUCTION/INDUCTION_MODEL_CONFIG__MODEL`, recreate deriver, then 3×
(reset dream layer → fire one dream → wait → record). Raw output:
`dream_sweep_results.jsonl`.

**Metrics per trial:** `ind_n`/`ded_n` (conclusions persisted), tool-call counts,
`has_card` (peer-card written), `dur_ms` (dream duration), VRAM, errors.

**Verdict columns (aggregated over 3 trials):**

**Progress: 26 / 26 models fully screened — Phase 1 COMPLETE (78 trials).**
Sorted by quality tier.

Induction / Deduction cells read **persist-rate · avg-#conclusions** (e.g.
`3/3 · 3.3` = persisted on all 3 trials, 3.3 conclusions on average). ⭐ = Phase 2
finalist.

### Tier 1 — reliable on *both* layers (3/3 induction **and** 3/3 deduction)

| Model | Induction | Deduction | Card | Dur | VRAM | Notes |
| --- | :-: | :-: | :-: | ---: | ---: | --- |
| **qwen3:8b** ⭐ | **3/3** · 3.3 | **3/3** · 3.0 | 1/3 (268 ch) | 2m41s | 10 GB | **best all-rounder** — both layers 3/3, fastest lean model |
| **gemma4:26b** ⭐ | **3/3** · 4.0 | **3/3** · 2.7 | 0/3 | 2m31s | 17 GB | fastest reliable; clean runs; never writes the card |
| **qwen3:30b** ⭐ | **3/3** · 3.0 | **3/3** · 2.0 | 2/3 (109–140 ch) | 4m26s | 21 GB | both layers 3/3 **and** 2/3 card — best card/speed combo |
| **qwen3.5:35b** ⭐ | **3/3** · 7.0 | **3/3** · 6.3 | 0/3 | 4m39s | 23 GB | **richest fast output** (avg 7 ind / 6.3 ded) at <5 min. ~~error-prone~~ → **retracted**: the "7–14 errs" were content pollution (see Phase-2 error note); 0 real errors. But Phase-2 showed its deduction collapses at n=10 (see below) |
| **qwen3:14b** ⭐ | **3/3** · 3.3 | **3/3** · 7.3 | **3/3** (37–291 ch) | 11m44s | 14 GB | 🏆 only **3/3 peer card** + most #ded; ~4× slower |
| qwen3.5:27b | **3/3** · 6.3 | **3/3** · 6.0 | 1/3 (201 ch) | 13m49s | 18 GB | rich output (3/3 both) but **slowest** of all |

### Tier 2 — one layer reliable, the other flaky

| Model | Induction | Deduction | Card | Dur | VRAM | Notes |
| --- | :-: | :-: | :-: | ---: | ---: | --- |
| gpt-oss:20b | **3/3** · 7.3 | 2/3 · 4.3 | 0/3 | 2m20s | 12 GB | high #ind, fastest of all; deduction occasionally empties |
| qwen2.5:32b | **3/3** · 5.0 | 1/3 · 0.7 | 0/3 | 4m06s | 28 GB | reliable rich induction; thin/flaky deduction — far better than its 14b sibling |
| qwen3.5:9b | **3/3** · 3.3 | 2/3 · 3.7 | 0/3 | 7m40s | 6.8 GB | strong output but slow + error-prone (13 tool errs one trial) |
| qwen3.5:4b | **3/3** · 4.0 | 1/3 · 0.3 | 0/3 | 4m30s | 4.3 GB | most #ind for the footprint; thin deduction |
| qwen3:4b | **3/3** · 2.0 | 1/3 · 0.7 | 0/3 | 5m17s | 7.5 GB | reliable induction, thin deduction |
| nemotron-3-nano:30b | 2/3 · 3.7 | **3/3** · 4.7 | 0/3 | 13m05s | 24 GB | the 30B nano *does* persist (unlike the 4B); slow + heavy |
| gemma4:12b | 1/3 · 0.7 | **3/3** · 5.3 | 0/3 | 3m50s | 8.4 GB | strong deduction, weak induction (mirror of qwen3:4b) |

### Tier 3 — flaky / unreliable on both layers

| Model | Induction | Deduction | Card | Dur | VRAM | Notes |
| --- | :-: | :-: | :-: | ---: | ---: | --- |
| mistral-small3.2 | 2/3 · 4.3 | 2/3 · 1.0 | 0/3 | 7m57s | — | mid reliability; 1 fully-empty trial; slow |
| gemma4:31b | **3/3** · 3.7 | 2/3 · 4.7 | 1/3 (225 ch) | ~20m ⚠️ | 20 GB | persists when it finishes, but **1/3 timed out** + very slow |
| nemotron3:33b | 2/3 · 1.0 | 2/3 · 3.3 | 1/3 (162 ch) | 12m55s | 26 GB | mid reliability; 1 fully-empty trial; slow + heavy |
| qwen3.6:27b | 2/3 · 3.0 | 1/3 · 4.3 | 1/3 (266 ch) | — | 18 GB | ⚠️ **2/3 trials timed out** (25-min cap) + 12 errs one run |
| qwen2.5:14b | 1/3 · 1.3 | 0/3 · 0 | 0/3 | 5m22s | 15 GB | ⚠️ **current production** — flaky; 21 ded_calls one trial, 0 persisted |

### Tier 4 — ❌ completes the loop, **persists nothing** (0/0/0)

| Model | Induction | Deduction | Card | Dur | VRAM | Notes |
| --- | :-: | :-: | :-: | ---: | ---: | --- |
| llama3.1:8b | 0/3 · 0 | 0/3 · 0 | 1/3 (176 ch) | 0m38s | 9.2 GB | writes card once but skips all the real work |
| nemotron-3-nano:4b | 0/3 · 0 | 0/3 · 0 | 0/3 | 1m40s | 3.4 GB | reasoning-distill: loop completes, nothing persists |
| deepseek-r1:8b | 0/3 · 0 | 0/3 · 0 | 0/3 | 1m20s | 10 GB | R1-distill failure; fast but empty |
| deepseek-r1:14b | 0/3 · 0 | 0/3 · 0 | 0/3 | 1m16s | 15 GB | scaling the distill to 14B doesn't fix it |
| deepseek-r1:32b | 0/3 · 0 | 0/3 · 0 | 0/3 | 2m51s | 28 GB | **…nor to 32B** — R1-distill is a hard dead-end at every size |
| command-r | 0/3 · 0 | 0/3 · 0 | 0/3 | 1m50s | — | **calls all the right tools, nothing commits** — despite being built for agentic RAG |
| llama3.1:70b | 0/3 · 0 | 0/3 · 0 | 0/3 | 4m33s | 52 GB | llama family persists nothing even at 70B |
| llama3.3:70b | 0/3 · 0 | 0/3 · 0 | 0/3 | 4m20s | 52 GB | 3.3 no better than 3.1 — llama is out, all sizes |

> **FINAL (78 trials, 26 models).** The field falls into four clean tiers.
> **Six models are 3/3 on *both* layers**: qwen3:8b, gemma4:26b, qwen3:30b,
> qwen3.5:35b, qwen3:14b, qwen3.5:27b. The fast end of that club (qwen3:8b @ 2m41s,
> gemma4:26b @ 2m31s) are the standout day-to-day picks; qwen3:30b adds a 2/3 card;
> qwen3.5:35b is the richest sub-5-min producer (but error-prone); the two 27B/14B
> at 12–14 min are richer-still but too slow for routine dreams.
>
> **Peer card is model-dependent (hypothesis disproven):** reliable only on
> **qwen3:14b (3/3, 37→291 ch)**; partial writers are qwen3:8b, qwen3:30b (2/3),
> qwen3.5:27b, qwen3.6:27b, gemma4:31b, nemotron3:33b, llama3.1:8b (each 1/3).
> gemma4:26b — the fastest reliable producer — **never** writes it. So the card is a
> genuine discriminator, and a prompt nudge is the lever to lift it on the fast picks.
>
> **Two architectural dead-ends for agentic persistence, confirmed at full scale:**
> (1) **R1-distill** (deepseek-r1 8b/14b/**32b**) — empty at every size.
> (2) **llama** (3.1 8b/**70b**, 3.3 **70b**) — empty at every size; raw scale (70B,
> 52 GB) buys nothing. command-r is a third route to the same 0/0/0 end (fires every
> tool, commits nothing). **Advertised tool capability does not predict dream-loop
> success — only the empirical persist-rate does.**
>
> **qwen2.5:14b (current production) is the worst non-distill performer** — 1/3
> induction, 0/3 deduction despite firing 21 `create_observations_deductive` calls one
> trial. Its 32B sibling (qwen2.5:32b) is dramatically better (3/3 ind), confirming
> the production choice was both the wrong model *and* the wrong size. Vindicates the
> whole investigation.

## Dreamer — Phase 2 deep-dive (10 trials × 5 finalists) — COMPLETE

**Harness:** `_jgh_/honcho-import/dream_sweep_phase2.sh` (Mando). Same loop as
Phase 1 but `TRIALS=10` and it *also* captures the conclusion + card **text**
(`dream_phase2_content.jsonl`) for blind grading. 50 trials total.

**Quality grading:** `build_dream_audit.py` → blind judge bundles (each item gets
an opaque id; model/trial mapping kept in a private key; items shuffled; each
paired with its 12 most-relevant **explicit** conclusions as grounding evidence
via stdlib IDF retrieval) → **6 Claude judge agents** grade 404 items blind
(GROUNDED/PARTIAL/UNSUPPORTED + over-attribution leakage + inductive specificity)
→ `aggregate_dream_grades.py` un-blinds to per-model. Source of truth = the
explicit-conclusion corpus (the dreamer derives ded/ind *from* it). Judge =
Claude (independent of all five Ollama candidates).

### Reliability at n=10

| Model | Ded | Ind | Card (avg len) | avg #ded·#ind | wall | VRAM |
| --- | :-: | :-: | :-: | :-: | ---: | ---: |
| **gemma4:26b** | **10/10** | **10/10** | 0/10 (—) | 2.7 · 3.4 | 181s | 17 GB |
| **qwen3:30b** | **10/10** | **10/10** | 3/10 (8 ch) | 2.2 · 3.2 | 352s | 21 GB |
| **qwen3:14b** | **10/10** | **10/10** | 6/10 (148 ch) | **5.5** · 6.3 | 522s | 14 GB |
| qwen3:8b | 8/10 | **10/10** | **10/10** (279 ch) | 1.9 · 5.6 | 223s | 10 GB |
| qwen3.5:35b | **4/10** ⚠️ | 9/10 | 1/10 (23 ch) | 2.4 · **7.9** | 271s | 23 GB |

> **Error note (correction to Phase 1):** the per-trial `errors` field is a
> `grep -ciE 'error|exception|traceback'` over the whole dream log window, which
> **includes the model's own conclusion text**. john-cc's data is saturated with
> log/error troubleshooting, so conclusions like *"debug API error patterns",
> "400 errors in Docker logs"* inflate the count. Recounting the entire 4.5 h
> Phase-2 sweep with strict failure markers (`Traceback`/`ERROR:`/`CRITICAL`/
> `Retrying`/HTTP 5xx) → **0 real errors**. The Phase-1 `errors`/`avgErr` column
> and every "error-prone" annotation are **void** — they measured the subject's
> vocabulary, not model stability.

### Blind quality (grounding % / leakage % / inductive specificity H·M·L)

| Model | Deductive grnd% | Inductive grnd% (spec) | Card grnd% | Leakage |
| --- | :-: | :-: | :-: | :-: |
| **qwen3:30b** | **95%** (21/22) | 57% (3H·26M·1L) | 100% (n=2) | 0% |
| **qwen3:14b** | 69% | 68% (8H·47M·7L) | 25% (n=4) | ded 2% / ind 5% / card 25% |
| qwen3.5:35b | 67% | 67% (33H·44M·2L) | 0% (n=1) | 0% |
| **gemma4:26b** | 52% | **85%** (15H·19M·**0L**) | — | ind 3% |
| qwen3:8b | 42% | **85%** (2H·21M·**16L**) | 0% (n=6) | card 17% |

> **The grounding ranking inverts between layers** — no single model is best at
> both. **qwen3:30b owns deduction** (95% grounded, 10/10 reliable). **gemma4:26b
> owns induction** (85% grounded, 10/10, **zero low-specificity filler**, fastest).
> qwen3:8b matches gemma's 85% inductive grounding but with **16 LOW-specificity**
> items (vague boilerplate) vs gemma's 0 — same grounding, much worse usefulness.
> **Leakage (over-attribution) is ~0% on conclusions across the board** — the
> import over-attribution concern does **not** reproduce at the dream layer; only
> peer-cards leak (and only at tiny n).

### Verdict — split the dreamer (its two phases take separate model configs)

`DREAM_DEDUCTION_MODEL_CONFIG__MODEL` and `DREAM_INDUCTION_MODEL_CONFIG__MODEL`
are independent, so the right answer is **not one model** — it's the best engine
per phase:

- **Deduction → `qwen3:30b`** — 10/10 reliable, **95% grounded**, 0% leakage. No
  other finalist is close on deductive quality.
- **Induction → `gemma4:26b`** — 10/10 reliable, **85% grounded**, the only model
  with **zero low-specificity** inductive output, and the fastest (181s).

Both are 0% leakage. Combined this beats every single-model option on both axes.

- **Single-model fallback (if ops wants one model): `qwen3:14b`** — the only
  all-rounder that's 10/10 on both layers with balanced ~68% grounding on each and
  the best (still imperfect) peer-card rate (6/10). Cost: slowest at 522s/dream —
  acceptable only because dreams are scheduled, not interactive.
- **qwen3.5:35b is OUT** — its Phase-1 3/3 deduction was small-sample luck; at
  n=10 it's **4/10** reliable on deduction. Prolific high-specificity induction,
  but you can't depend on it for the deductive phase.
- **qwen3:8b** — great reliability story (and the only consistent card writer) but
  the **lowest deductive grounding (42%)** and vaguest induction; not a quality pick.

### Peer card — still unsolved (no model writes a *good* one)

Reliability and quality are anti-correlated on the card: qwen3:8b writes it 10/10
but **0% grounded + 17% leakage** (verbose, invented); qwen3:30b's are 100%
grounded but 8-char stubs (3/10); qwen3:14b is the least-bad balance (6/10, 25%
grounded). **No finalist produces a card worth shipping.** Next lever is the
**prompt-nudge experiment** — confirmed feasible via the per-peer reasoning /
custom-instructions config path (no Honcho `src/` edit), to be run on the chosen
induction model (gemma4:26b) as a follow-up, not a blocker for the split decision.

---

## Deriver — _(planned, after dreamer checkpoint)_
## Summarizer — _(planned)_
## Dialectic — _(planned)_

---

## Findings log

- **2026-06-15** — Dreamer was never benchmarked; the "standardize on qwen2.5:14b"
  decision left induction + peer-card silently broken in practice. gemma4:26b
  completes the induction loop where qwen2.5:14b is flaky.
- **2026-06-15** — **Single dreams are noisy**: qwen2.5:14b produced 0 inductive on
  one run, 2 on the next. Verdicts must be multi-trial. Drove the 3-trial Phase-1
  design.
- **2026-06-15** — No model has written the **peer card** yet (`update_peer_card`
  never fires). Tracking as a separate issue from model selection.
- **2026-06-15** — **New failure mode (reasoning-distill models):** `nemotron-3-nano:4b`
  and `deepseek-r1:8b` both report deduction+induction phases "completed" (`ded_done=1`,
  `ind_done=1`) yet make **zero** `create_observations_*` calls — they finish the tool
  loop without ever persisting. Non-distill `qwen3:4b` / `qwen3.5:4b` are 3/3 reliable
  at the smallest VRAM. Early signal: a 4B model may beat the 8B reasoning models here.
- **2026-06-16** — **Distill failure mode scales:** `deepseek-r1:14b` is also 0/0/0 —
  bumping the R1 distill from 8B→14B does not restore persistence. R1-distill models
  are out for any agentic-persistence job, full stop.
- **2026-06-16** — **Peer-card hypothesis fully disproven.** `qwen3:14b` writes the card
  **3/3** (37→140→291 ch); qwen3:8b / llama3.1:8b / qwen3.5:27b each 1/3. The card is
  model-dependent, not a source bug — and one model already does it reliably.
- **2026-06-16** — **Two fast both-layer winners emerge:** `qwen3:8b` (2m41s, 10 GB) and
  `gemma4:26b` (2m31s, 17 GB) are both 3/3 on deduction *and* induction at ~2.5 min.
  The richer 27B-class models (qwen3.5:27b, qwen3:14b) match the reliability but run
  5–6× slower (12–14 min/dream). `gpt-oss:20b` is a strong fast induction model (3/3,
  avg 7.3 #ind) but its deduction occasionally empties (2/3).
- **2026-06-16** — **Nemotron *scale* recovers persistence (but not the nano):** the
  4B `nemotron-3-nano:4b` persists nothing (distill failure), yet `nemotron-3-nano:30b`
  is 3/3 deduction / 2/3 induction — the larger nano clears the tool-loop. Still slow
  (13 min) and heavy (24 GB), so it's not a practical pick, but it shows the failure is
  capacity-linked for nemotron, not architectural like the R1 distills.
- **2026-06-16** — **qwen3.6:27b is unstable on the dream loop:** 2 of 3 trials hit the
  25-min timeout cap and one run logged 12 errors. When it does finish it produces good
  output (13 ded / 6 ind, 266-ch card) but the reliability isn't there. Demoted to the
  flaky tier despite the strong newer base model.
- **2026-06-16** — **qwen3:30b joins the top tier:** 3/3 on both layers, 2/3 peer card
  (109–140 ch), at 4m26s / 21 GB. Best card reliability among the fast 3/3 models — a
  real Phase-2 finalist alongside qwen3:8b and gemma4:26b (trade: 21 GB vs 10/17 GB).
- **2026-06-16** — **command-r is a clean counter-example to "agentic = persists".** It's
  marketed for tool-use/RAG and *does* call `create_observations_*` and `update_peer_card`
  every trial, yet **0/0/0 lands** — same end state as the R1 distills by a different
  route (tool calls fire but nothing commits). Confirms advertised tool capability is no
  predictor of dream-loop success; only the empirical persist-rate is.
- **2026-06-16** — **Phase 1 COMPLETE (26/26, 78 trials).** Final tier-4 tail closes the
  two dead-end families for good: **R1-distill** is empty at 8B/14B/**32B** (no size
  helps), and **llama** is empty at 3.1-8B/3.1-**70B**/3.3-**70B** — raw 70B scale (52 GB)
  buys nothing. Both join command-r in the "completes loop, persists nothing" bucket.
- **2026-06-16** — **qwen3.5:35b is a new top-tier finalist** — 3/3 on both layers with
  the **richest sub-5-min output** of the whole sweep (avg 7.0 ind / 6.3 ded @ 4m39s,
  23 GB). Caveat: error-prone (7–14 tool errors/run) and writes no card. Promoted into
  the 5-model finalist set.
- **2026-06-16** — **qwen2.5 is the wrong size, not just the wrong model:** the 14B
  (current production) is the worst non-distill performer (1/3 ind, 0/3 ded), but the
  **32B sibling is 3/3 induction** (avg 5.0 #ind, 4m06s). The family *can* dream — the
  production deployment just picked the size where it can't. Reinforces moving off
  qwen2.5:14b regardless of which finalist wins.
- **2026-06-16** — **Phase 2 finalists locked:** qwen3:8b, gemma4:26b, qwen3:30b,
  qwen3.5:35b, qwen3:14b (gpt-oss:20b alternate). Span fast/lean → rich/slow and 0/3 →
  3/3 card. Awaiting go-ahead before the 8–10-trial deep-dive + blind quality grading.
- **2026-06-16** — **Phase 2 COMPLETE (50 trials + 404 blind-graded conclusions).**
  Two headline results: (1) **the `errors` metric was an artifact** — strict-marker
  recount of the whole sweep = 0 real errors; the Phase-1 "error-prone" tags (esp.
  qwen3.5:35b) are retracted. (2) **Grounding quality inverts between layers, so the
  dreamer should be split:** deduction → qwen3:30b (95% grounded, 10/10), induction →
  gemma4:26b (85% grounded, 0 low-spec, 10/10, fastest). No single model wins both.
- **2026-06-16** — **qwen3.5:35b dropped from contention:** its Phase-1 3/3 deduction
  was small-sample luck — at n=10 deduction is **4/10**. It remains the most prolific
  high-specificity inductive producer (33 HIGH-spec items) but can't be trusted for the
  deductive phase. Vindicates running Phase 2 at higher n.
- **2026-06-16** — **qwen3:8b reliability ≠ quality:** held card 10/10 at n=10 (Phase-1's
  1/3 badly undersold it) and matched gemma's 85% inductive grounding — but with **16
  LOW-specificity** inductive items vs gemma's 0, the **lowest deductive grounding (42%)**,
  and 17% card leakage. A reliable tool-caller that produces vague/under-grounded content.
- **2026-06-16** — **Over-attribution does NOT reproduce at the dream layer:** blind
  leakage grading is ~0% on deductive/inductive conclusions for every finalist. The
  import over-attribution issue (`import-over-attribution`) is a *parse/explicit-derivation*
  problem, not a dreaming one — the dreamer reasoning over already-clean explicit
  conclusions doesn't re-introduce it. Only peer-cards leak, and only at tiny n.
- **2026-06-16** — **Peer card remains unsolved by model choice:** reliability and
  grounding are anti-correlated (qwen3:8b 10/10 written but 0% grounded; qwen3:30b 100%
  grounded but 8-char stubs). No finalist ships a usable card. Deferred to a prompt-nudge
  experiment on gemma4:26b via the per-peer custom-instructions config path (no src edit).

## Artifacts

| File (`_jgh_/honcho-import/`) | Purpose |
| --- | --- |
| `dream_sweep.sh` | dreamer Phase-1 screen harness |
| `dream_sweep_results.jsonl` | raw Phase-1 per-trial results (on Mando) |
| `dream_sweep_phase2.sh` | dreamer Phase-2 deep-dive harness (10 trials + content capture) |
| `dream_phase2_results.jsonl` / `dream_phase2_content.jsonl` | Phase-2 metrics + conclusion/card text (on Mando) |
| `build_dream_audit.py` | assembles blind judge bundles (IDF-retrieved evidence, private key) |
| `aggregate_dream_grades.py` | un-blinds judge grades → per-model grounding/leakage/specificity |
| `DREAM_GRADING_RUBRIC.md` | blind judge rubric (GROUNDED/PARTIAL/UNSUPPORTED + leakage + specificity) |
| `bench_load.py`, `bench_audit_payload.py`, `aggregate_bench.py` | deriver grounding-audit harness (reused for the deriver round) |
| `bench_dialectic.py` | dialectic timing/quality harness (reused for the dialectic round) |
