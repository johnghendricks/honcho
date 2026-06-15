# Honcho History-Import Benchmarks — Time & Space to Ingest Claude Code History

> Personal log for John (jgh). Measured cost of importing Claude Code
> transcripts + memory files into the self-hosted Mando/Ollama Honcho stack, used
> to project what it takes to ingest the big **kb-proto-1** project. Companion to
> `honcho-mando-ollama-setup.md`. Living doc — append each run.
>
> Tooling lives in the repo at `_jgh_/honcho-import/` (`parse_transcripts.py`,
> `bench_memory_import.py`, `scan_project.py`, and the current ingestion tool
> **`ingest_batch.py`**). Target: `http://192.168.0.140:8000` (Mando), ws
> `default`, import peers `john-cc` / `claude-cc`.
>
> **Note (2026-06-12):** ingestion is now done by `ingest_batch.py` (resumable,
> batched, SQLite ledger, `john-cc observe_me=true`). The older one-shot
> `load_to_honcho.py` / `import_groups.py` referenced in the runs below are
> superseded; their results stand as historical measurements.

## TL;DR

- **Embedding is synchronous on POST for both paths** in this deployment
  (pgvector + `EMBED_MESSAGES=true`): conclusions via `crud/document.py:800`,
  messages via `crud/message.py:282` (`batch_embed`, which chunks >512-tok
  content into multiple `MessageEmbedding` rows). So a load's wall-time *is* the
  Ollama bge-large embedding cost — there is **no async background phase**. (The
  Reconciler only re-embeds for *external* vector stores; in pgvector mode it just
  cleans soft-deletes — `reconciler/sync_vectors.py:596`.)
- **The memory/conclusion side is cheap** (~1 MB, seconds for kb-proto-1). **The
  transcript side dominates: ~25 min and ~74 MB of vectors** for kb-proto-1
  (measured-then-projected, Run 2).
- **Messages cost ~4.5× a conclusion each** (0.237 s/msg vs 0.053 s/concl) — they
  average ~1,200 tok and fan out into **~3 embed rows each**.
- **Always batch** (≤100/request): batched conclusion throughput is ~22–24/s vs
  ~10/s one-at-a-time. Memory files *and* messages routinely exceed bge's
  **512-token cap** → chunking is mandatory.
- **Derivation: blocked → fixed.** Run 3 found the deriver barely processing
  (~27 s/task floor, ~48 h, 0 conclusions). Capping `num_ctx` unblocked it (Run 4,
  ~17 s/task) but gemma4:26b produced ~15–25 % degenerate conclusions on dense
  content (Runs 4–5). **Swapping the deriver model to qwen2.5:14b
  (+`frequency_penalty=0.3`) cleared all garbage (0 %) and ran 2.6× faster
  (~7.7 s/task → ~14–16 h)** — Run 6, the green light for the full import.

## Method

`bench_memory_import.py` walks the memory-only projects, parses each `.md`
(strips frontmatter, chunks the body under the embedding cap), and POSTs the
chunks as conclusions on `john-cc` (self-conclusions), timing each project with
`time.perf_counter()`. Because embedding is inline, the timing is end-to-end real
cost. `scan_project.py` does a parse-only (no-API) volume scan of any project.

bge-large was already resident during the run (no `keep_alive` cold-load spike
observed); a cold first call would add a one-time model-load stall — see
`dialectic-latency-tuning.md`.

---

## Run 1 — Memory-only projects (set "b"), 2026-06-11

9 projects, 35 memory files → **50 conclusions in 2.67 s** (0.053 s/conclusion,
18.7/s aggregate).

| Project | files | concl | load (s) | s/concl | concl/s | split files | types | est space |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| kb-prototype-2 | 10 | 18 | 0.83 | 0.046 | 21.6 | 8 | feedback, project, user | 72 KB vec + 18.8 KB |
| VPIT-Core-2026 | 13 | 16 | 0.67 | 0.042 | 23.7 | 3 | feedback, project, reference, user | 64 KB vec + 15.7 KB |
| Personal-Second-Brain | 3 | 5 | 0.32 | 0.063 | 15.9 | 2 | project, user | 20 KB vec + 4.2 KB |
| Personal-Knowledgebase-Source | 3 | 4 | 0.20 | 0.051 | 19.7 | 1 | feedback, project | 16 KB vec + 3.5 KB |
| Desktop-Obsidian-Vaults | 1 | 2 | 0.20 | 0.100 | 10.0 | 1 | feedback | 8 KB vec + 2.3 KB |
| Claude-Code-Tooling | 2 | 2 | 0.14 | 0.071 | 14.1 | 0 | project | 8 KB vec + 1.4 KB |
| vpit-scenario-editor | 1 | 1 | 0.11 | 0.106 | 9.5 | 0 | project | 4 KB vec + 0.6 KB |
| John-Hendricks-Vault | 1 | 1 | 0.10 | 0.095 | 10.5 | 0 | feedback | 4 KB vec + 0.5 KB |
| Desktop-Obsidian (john-vault) | 1 | 1 | 0.10 | 0.102 | 9.8 | 0 | feedback | 4 KB vec + 0.5 KB |
| **Total** | **35** | **50** | **2.67** | **0.053** | **18.7** | **15** | — | **200 KB vec + 47.6 KB** |

**Findings**

1. **Batch amortization is real and large.** The four single-conclusion projects
   land at ~10/s (per-request overhead dominates); the 16- and 18-chunk batches
   hit 21.6–23.7/s. Throughput roughly doubles once a batch fills. Load big
   projects in full 100-conclusion batches.
2. **~43% of memory files needed splitting** (15/35). Curated `feedback_*` /
   `project_*` files are frequently 1.5–2.5 KB (~450–720 tok) — over the 512-tok
   embedding cap. Chunking is mandatory, not optional.
3. **Storage per conclusion is dominated by the vector**: 1024-dim float32 =
   4,096 B fixed, vs ~950 B avg content. So conclusion count, not text length,
   drives vector space.
4. **Per-conclusion embed cost is tiny** (~45–50 ms in-batch) — the memory side
   of any project is a rounding error on time.

---

## kb-proto-1 volume scan (parse-only, no API), 2026-06-11

| Metric | Value |
| --- | ---: |
| Transcript files on disk | 649 (14 empty after parse) |
| Sessions with content | 635 |
| Messages (merged turns) | **6,058** (user 3,033 / asst 3,025) |
| Message content | **27.35 M chars (~8.04 M tok)** |
| Avg msgs/session | 9.5 |
| Avg chars/msg | **4,515 (~1,330 tok)** |
| Biggest session | 96 msgs |
| Parse wall-time | **2.2 s** (290 files/s) |
| Memory files | 73 → **225 conclusions** (3.08 chunks/file) |
| Memory body | 223 K chars |

The memory files here are **denser** than the Run-1 average (3.08 chunks/file vs
1.43), so extrapolating chunk counts from file counts alone undershoots — scan,
don't guess.

---

## Run 2 — kb-proto-1 25-session slice (message path), 2026-06-11

No real-world project sits between honcho (3.2 MB) and kb-proto-1 (554 MB) — the
corpus is bimodal — so the only representative rehearsal is a slice of kb-proto-1
itself. `bench_transcript_slice.py` takes a **stratified-by-size** sample (even
strides across sessions sorted by message count) so the per-message rate reflects
the real size mix, loads it under `full-<sessionId>` (peers `john-cc`/`claude-cc`,
derivation deferred), and times every `/messages` POST.

| Metric | Value |
| --- | ---: |
| Sample | 25 sessions / **320 messages** / 1.30 M chars |
| Avg message | ~1,197 tok (project-wide ~1,330) |
| POST wall-time | **76.0 s** over 26 batches |
| Throughput | **4.21 msgs/s · 0.237 s/msg · 17.1 K chars/s** |
| Embed fan-out | **~2.98 `MessageEmbedding` rows/msg** (avg msg > 512-tok cap) |

**Findings**

1. **A message costs ~4.5× a conclusion** (0.237 vs 0.053 s) — driven by size
   (~1,200 tok) and the ~3× embed fan-out, all paid synchronously on the POST.
2. **Fan-out confirmed by code + data**: `batch_embed` splits >512-tok content,
   and the Reconciler keys multi-chunk vectors as `{message_id}_{chunk_position}`
   (`sync_vectors.py:371`) — so one long message = **N vector rows**, ~2.98 here.
3. **Throughput is embedding-bound and serial** — one Ollama/bge-large instance on
   Mando. Parallelizing the *client* (multiple POSTers) won't help much; the
   embedder is the bottleneck. (Could be sped up by a smaller/faster embedder or
   batching more aggressively, not by client concurrency.)
4. Two parser hardenings were forced by real data and now matter for the full
   import: **split messages >24 K chars** (Honcho rejects >25 K — skill-doc
   injections hit this) and the existing **<512-tok conclusion chunking**.

> Note: the >24 K split raised kb-proto-1's parsed message count from 6,058
> (pre-split scan) to **6,386**. Projections below use 6,386.

## kb-proto-1 full ingest projection (derivation deferred) — updated

Message path is now **measured** (Run 2), not guessed.

| Phase | Quantity | Rate basis | Projected | Confidence |
| --- | ---: | --- | ---: | --- |
| Parse (local) | 649 files | measured 290 files/s | **~2 s** | High |
| Memory → conclusions | 225 concl | Run-1 ~22/s | **~10 s** + ~1.1 MB | High |
| **Message POST + embed** | **6,386 msgs** | **Run-2 0.237 s/msg** | **~25 min** | **High** |
| Message vector storage | ~19 K rows | 4 KB/row | **~74 MB vectors** | High |
| Message content storage | 27.4 M chars | raw | **~27 MB** | High |
| Derivation (if later enabled) | 6,386 msgs | gemma4:26b deriver | **hours of LLM compute** | Deferred |

**Bottom line:** importing all of kb-proto-1 *with derivation deferred* is a
**~25-minute, ~100 MB** job (74 MB vectors + 27 MB content; HNSW index overhead
on top, not yet measured). It's serial and embedding-bound, so it just runs on
Mando for ~25 min — no need to babysit. The **real** cost remains the **deriver**
(turning 6 K messages into representations), still intentionally out of scope
until separately benchmarked.

This slice also **partially completed the real import**: 25 kb-proto-1 sessions
(320 msgs) are now live under `full-<sessionId>`. The remaining ~610 sessions can
be loaded with `load_to_honcho.py` over a full parse.

---

## Run 3 — Derivation (BLOCKED: deriver barely processing), 2026-06-11

Attempted to cost the Deriver by enqueuing a representative 12-message kb-proto-1
session for an observing peer (`dbench`, `observe_me=true`) and timing the
representation tasks. **Could not get a clean per-message number — the deriver is
alive but pathologically slow and not draining its backlog.**

Evidence (authoritative via `mcp__honcho__get_queue_status` + a corrected poller):

| Signal | Observation |
| --- | --- |
| My 12 tasks | **pending the entire ~13 min**, never claimed |
| Backlog | global **pending pinned at 14** throughout — not being claimed |
| Throughput | in-progress went **3→2→1**: just **2 tasks completed, ~27 s apart**, then stalled at 1 |
| Conclusions produced | **0** for the observed peer |

Interpretation: a worker cleared 2 of its already-claimed tasks (~27 s each) then
**stopped claiming new work** — the 14 pending (incl. my 12) just sit there. So
it's not stone-dead, but it is **not processing the queue**. Probable cause: the
gemma4:26b **256K loaded context** flagged in `dialectic-latency-tuning.md` makes
each deriver call extremely slow and may be hanging the claim loop. **Operational
consequence: new representations are effectively not being formed** — nothing
imported (slice, `/clear` captures) becomes conclusions in this state.

Best-case per-task floor from the only two completions observed: **~27 s/task**
(and that's while *not* keeping the pipeline full).

> Tooling bug found & fixed in passing: the REST `/queue/status` returns
> `snake_case` fields (`pending_work_units`); the MCP tool returns `camelCase`.
> The pollers were reading camelCase off REST → false "empty queue" 0-readings.

### Structural cost model (what derivation *would* cost when healthy)

From code, not measurement (so: order-of-magnitude only):

- The minimal deriver makes **one gemma4:26b structured-output call per batch**.
- Batch cap `REPRESENTATION_BATCH_MAX_TOKENS = 1024` (default); kb-proto-1 averages
  ~1,200 tok/msg **> 1024**, so most batches = **1 message → ~6,400 LLM calls** for
  the full project. (`WORKERS=1` default → serial.)
- Per-call gemma4:26b time: the only two completions observed in Run 3 were
  **~27 s/task** — consistent with the dialectic doc's "tens of seconds/call" on
  this model.
- Envelope at the observed floor: 6,400 calls × ~27 s ≈ **~48 hours** serial — and
  that's optimistic, since the worker isn't even keeping itself fed. Capping
  `num_ctx` (the 256K pathology) and/or a smaller deriver model could cut the
  per-call time substantially. **Config-dominated.**

So derivation plausibly dwarfs the ~25-min import by **~100×**. This is why
deferral was the right call — and why it needs a healthy, tuned deriver (capped
`num_ctx`, possibly a smaller deriver model) before ever running on 6 K messages.

### Mando diagnostic (needs host access — can't reach from Bossk)

```bash
docker compose ps                      # is the `deriver` service up?
docker compose logs deriver --tail 100 # errors, or hung mid gemma call?
```
The 3 stuck in-progress imply a worker crashed/hung mid-task (stale
`ActiveQueueSession` claims). Restart the deriver; apply the
`OLLAMA_CONTEXT_LENGTH` cap + `keep_alive` from `dialectic-latency-tuning.md` to
the deriver model; then re-run `bench_deriver.py` to get the real per-call number.

---

## Run 4 — Deriver UNBLOCKED (num_ctx capped), 2026-06-11

The fix from `deriver-tuning.md` was applied on Mando and confirmed via
`/api/ps`: `gemma4:26b` now loads at **`ctx=32768`** (was 262144 / 256K), 16.4 GB,
`expires_at` year 2318 (`OLLAMA_KEEP_ALIVE=-1` in effect). The Run-3 stall is
gone — the global queue was **69/69 drained** before the test, and the worker
claimed the full 12-task bench batch immediately. Deriver health: **resolved.**

Clean 12-message benchmark (source session `e021ba3d…`, peer `dbench`):

| Metric | Value |
| --- | ---: |
| Work units | 12 in **242.8 s** |
| First claim | t=37.5 s (deriver idle-poll backoff — not per-task) |
| First completion | 57.7 s |
| **Per-task, steady** | **~16.8 s/unit** |
| Per-task, overall | ~20.2 s/unit |
| **Projection, 6,386 msgs** | **~29.8 h steady / ~35.9 h overall** |

**vs Run 3:** ~27 s/task floor → ~17 s/task. The `num_ctx` cap bought **~1.6×**
per-task and pulled the full-import envelope from the theoretical ~48 h to
**~30 h** serial. The 26B decode is now the floor.

**Findings**

1. **Trust the aggregate, not per-task granularity.** The `completed` counter is
   **bursty** — it sat at 2 from t=57→142 s, then jumped 4→11 in one 4 s poll.
   Seven 17 s tasks can't finish in 4 s, so completions commit in *groups*. Only
   total-wall-time ÷ tasks is reliable; per-poll deltas are an artifact.
2. **The 37.5 s first-claim latency won't recur under sustained load** — it's the
   deriver's adaptive idle-poll backoff (queue was empty; sleep had grown toward
   30 s). During a continuous import the loop stays at the base interval. The
   steady-state projection already excludes it.
3. **🔴 Conclusion QUALITY is the new blocker — repetition loops persist.** Of the
   84 conclusions formed, **~15–20% degenerated** into the loop from
   `deriver-repetition-and-sampling.md` (`//note: //note:…`, `(is)s (is)s…`,
   `part number ascending within that stem,` ×hundreds). Clean ones were
   accurate; garbage clustered on **dense, structured source** (build-plan skill
   docs) — pervasive in kb-proto-1. `frequency_penalty=0.3` (Option B) is either
   not deployed or too weak. **Move to Option A (`repeat_penalty 1.15`) before the
   import.** This likely also speeds the deriver up — degenerate calls run to the
   max-output-token cap, inflating the ~17 s number above.
4. **Tooling bug fixed:** `bench_deriver.py` read camelCase
   (`completedWorkUnits`) off the REST `/queue/status`, which returns snake_case
   (`completed_work_units`) → false `total=0` "no work units" abort (the first
   attempt died at 62 s despite 12 live units). Same Run-3 casing bug that was
   fixed in the pollers but missed here; now patched.

> **Import readiness:** speed ✅ (≈30 h, acceptable for a one-time serial job),
> health ✅, **quality ⛔ until `repeat_penalty` lands.** Do not run the full
> 6,386-message import until Run 5 confirms clean conclusions.

---

## Run 5 — Option B genuinely live (gemma4:26b + `frequency_penalty=0.3`), 2026-06-11

Run 4 was a **no-penalty baseline** — `frequency_penalty` had never reached the
deployed config (the `docker compose restart` ≠ `up -d` trap;
see `deriver-repetition-and-sampling.md`). For Run 5 the penalty was recreated
into the container via `docker compose up -d deriver` and **verified live**:
`docker exec honcho-deriver-1 printenv` showed
`DERIVER_MODEL_CONFIG__FREQUENCY_PENALTY=0.3`, container freshly created, model
resident at `ctx=32768`. Fresh peer `dbench-fp2` to isolate from baseline data.

| Metric | Value |
| --- | ---: |
| Work units | 12 in **251.0 s** |
| First completion (cold) | 33.5 s |
| **Per-task, steady** | **~19.8 s/unit** |
| **Projection, 6,386 msgs** | **~35.1 h steady / ~37.1 h overall** |
| Conclusions formed | 8 |
| Degenerate / garbage | **~25 % (2/8)** |

**Findings**

1. **Speed unchanged** vs Run 4 (~20 → ~19.8 s/unit). The theory that killing
   runaway-to-token-cap loops would also speed the deriver up did **not** show at
   the aggregate.
2. **✅ The classic single-token repetition loop is gone.** Zero
   `much/ much/…` or `//note: //note:…` in the set — `frequency_penalty` does
   what it targets.
3. **❌ But two _new_ degradation modes appeared (~25 %):** (a) **chain-of-thought
   bleed** — the stored conclusion literally contained the model reasoning to
   itself (*"Wait, checking messages… let me re-read"*); (b) **grammar breakdown +
   mid-sentence truncation** on the dense build-plan doc, run to the max-token cap
   and cut off. Plus peer-name typos (`dbess-fp2`, `dberch-fp2`).
4. **Verdict:** the penalty fixed the symptom it can fix, but the **root problem —
   the 26B model degrading on dense, structured source** — persisted in a new
   shape. `0.3` is not clean enough to import; bumping to `0.5` wouldn't help
   (these failures aren't repetition). Points at **model capability**, not
   sampling → swap the model (Run 6).

---

## Run 6 — Deriver model swapped to qwen2.5:14b (+ `frequency_penalty=0.3`), 2026-06-11 ✅ GREEN LIGHT

Swapped the deriver model `gemma4:26b → qwen2.5:14b` on Mando (older but more
proven for clean structured output), `frequency_penalty=0.3` retained. Verified
resident over the LAN (`GET 192.168.0.140:11434/api/ps`): `qwen2.5:14b`,
`ctx=32768`, 15.3 GB VRAM, `keep_alive=-1`. Fresh peer `dbench-qwen`, same
12-message source session as every prior run (directly comparable).

| Metric | Value |
| --- | ---: |
| Work units | 12 in **106.6 s** |
| First completion (cold) | 22.1 s |
| **Per-task, steady** | **~7.7 s/unit** |
| **Projection, 6,386 msgs** | **~13.7 h steady / ~15.8 h overall** |
| Conclusions formed | **24** |
| Degenerate / garbage | **0 %** |

**Scorecard — same 12 messages, both with `frequency_penalty=0.3`:**

| | gemma4:26b (Run 5) | **qwen2.5:14b (Run 6)** |
| --- | ---: | ---: |
| Steady speed | 19.8 s/unit | **7.7 s/unit** |
| Full-import projection | ~35 h | **~14–16 h** |
| Conclusions (12 msgs) | 8 | **24** |
| Degenerate / garbage | ~25 % | **0 %** |
| VRAM | 17.7 GB | **15.3 GB** |

**Findings**

1. **2.6× faster and leaner.** 7.7 vs 19.8 s/unit, 15.3 vs 17.7 GB — the
   full-import envelope drops from ~35 h to **~14–16 h** serial.
2. **✅ Zero degeneration across 24 conclusions** — no repetition loops, no
   CoT bleed, no truncation, no grammar breakdown, no peer-name typos. The exact
   dense build-plan message that broke gemma (CoT bleed in Run 5, run-on
   truncation in Run 4) produced **clean, specific facts** here (e.g. *"widening
   the inspector's dual-site display… expanding `_CHUNK_METADATA_SURFACE_KEYS`…
   chat.py, post_stream_loop.py, test_inspector_surface.py"*).
3. **More facts _and_ cleaner** — 24 well-formed conclusions vs gemma's 8 (2 of
   them garbage). Only nitpick: one low-value/vague extraction (*"has a tool or
   resource related to skills"*), not garbage.
4. **Root cause confirmed.** The failures were model capability on dense content,
   not sampling — swapping the model fixed both the loops (already handled) and the
   new degradation modes at once. `frequency_penalty=0.3` is retained as cheap
   insurance.

> **Import readiness: ✅ GREEN LIGHT.** Speed ✅ (~14–16 h, fine for a one-time
> serial job), health ✅, **quality ✅ (0 % garbage on the content type that
> blocked every prior run).** qwen2.5:14b + `frequency_penalty=0.3` clears the bar
> for the full 6,386-message kb-proto-1 import.

---

## Dialectic model A/B — picking the model for the *non-deriver* agents, 2026-06-11

Separate question from the deriver runs above: with the **deriver** settled on
`qwen2.5:14b` (Run 6), what should the **other** agents — Dialectic (chat),
Summarizer, Dreamer — run on? They were all on `gemma4:26b`. This benchmarks the
**Dialectic** specifically (the user-facing, tool-using recall path) because it's
the one that's latency-sensitive and exercises the tool loop.

`bench_dialectic.py` fires 6 fixed recall probes at peer `dbench-qwen` (which has
24 real conclusions from Run 6) at `high` reasoning (4 tool iterations), timing
each `/chat` end-to-end and capturing the full response for quality comparison.
Each model was swapped in on Mando (`up -d api`, verified via `printenv`) before
its run.

| Metric | gemma4:26b | qwen2.5:32b | **qwen2.5:14b** |
| --- | ---: | ---: | ---: |
| **Steady mean / query** | 26.7 s | 42.7 s | **21.5 s** |
| Steady range | 21–31 s | 33–50 s | 12–30 s |
| Cold-load (probe 1) | 34.1 s | 73.1 s | 28.9 s |
| Avg response | 1113 ch | 919 ch | **1331 ch** |
| Grounding quality | well-grounded | well-grounded | well-grounded |

**Findings**

1. **Bigger was worse.** `qwen2.5:32b` was **~60 % slower** than gemma (42.7 vs
   26.7 s) for the same grounding and *shorter* answers — and slightly less
   nuanced (asserted "Phase 7" flatly where gemma flagged the Phase 6 in-progress
   /completed contradiction in the representation). No payoff for the extra params.
2. **`qwen2.5:14b` won outright** — fastest (**21.5 s, ~20 % under gemma**) *and*
   the most detailed (1331 ch), well-grounded, no degeneration. Probe 1 cleanly
   reconciled the phase data (*"resumed Phase 7 following the completion of Phase
   6"*).
3. **The Dialectic was never broken on gemma.** Every quality failure we chased
   was deriver-specific (structured extraction). Gemma answered chat well; this
   A/B is an *optimization*, and the win is mostly **ops consolidation**, not a fix.
4. **One model for the whole stack.** `qwen2.5:14b` is already the deriver model
   and already resident, so using it everywhere means **a single resident model**
   for deriver + dialectic + summary + dream — and `gemma4:26b` (17.7 GB) and
   `qwen2.5:32b` can be **evicted to reclaim VRAM**.

> **Decision: standardize on `qwen2.5:14b` across all agents** (deriver, dialectic
> ×5 tiers, summary, dream); embeddings stay `bge-large`. Since 32b was *worse*
> here, bigger is not the answer for this workload.

**`max`-tier follow-up (10 tool iterations).** Re-ran the same 6 probes on
`qwen2.5:14b` at `max`: **steady mean 20.5 s/query** (vs 21.5 s at `high`),
avg 1231 ch, still well-grounded, **no wandering or degeneration** with the deeper
cap available. `MAX_TOOL_ITERATIONS` is a *cap*, not a quota — the 14b converges in
a few tool calls and answers, so `max` costs ~the same as `high` here. (Caveat:
these probes converge quickly and don't *force* deep loops; a genuinely hard
multi-hop query could still stress a 14b further — but the "small model wanders at
max" worry didn't show.)

---

## Grounding sweep — deriver model 14b vs 32b (fixed 8-session bench), 2026-06-12

After the clean reinstall, a **source-verified grounding audit** replaces the
degeneration/speed metrics above as the quality bar: a fixed, size-stratified
**8-session set** (`bench_set.json`, 2→28 msgs/session) is loaded *identically*
into a per-config workspace (`bench_load.py` — reasoning ON + the same
anti-over-attribution `custom_instructions`, `john-cc observe_me=true`), derived
under one deriver model, then every `john-cc` explicit conclusion is blind-graded
GROUNDED / PARTIAL / OVER / HALLUCINATED against its source transcript
(`bench_audit_payload.py` → 8 blind judges → `aggregate_bench.py`). Only the
**deriver model** varies; everything else is held constant. **Pass bar: ≥80%
grounded, 0 leakage.**

| config (deriver model + parser) | conclusions | **grounded** | partial | over+hall | leakage |
| --- | ---: | ---: | ---: | ---: | ---: |
| **qwen2.5:14b**, skill-strip only (ws `default`) | 84 | 75.0 % | 11.9 % | 13.1 % | 4 |
| **qwen2.5:32b**, skill-strip only (ws `bench-qwen32`) | 119 | 73.9 % | 8.4 % | 17.6 % | 21 |
| **qwen2.5:14b + continuation-strip** (ws `bench-strip`) | 93 | **82.8 %** ✅ | 11.8 % | 5.4 % | 4 |

**Findings**

1. **Bigger did not help — slightly worse.** 32b held the same grounding rate but
   extracted **40 % more conclusions** (119 vs 84) and **leaked 5× more** in
   absolute terms (21 vs 4). More output, same signal, more noise. (Consistent with
   the Dialectic A/B above, where 32b also lost to 14b.)
2. **The failure was concentrated, not diffuse.** In *both* model runs ~19 of 21
   OVER and ~20 of 21 leakage came from the **same two sessions** (`40c12239`,
   `f1a03124`) — the ones where John pasted CC **continuation/auto-compact summary
   blocks** into a user turn. The deriver reads the *assistant-narrative prose
   inside those pasted blocks* ("verified both routing legs", commits, bug fixes)
   as John's own actions. The other six sessions grade ~95 %+ grounded throughout.
3. **`custom_instructions` already forbids this and neither model honors it.** The
   instruction explicitly excludes "tool output… task notifications… pasted command
   output" — but the bleed survived at both sizes. Model selection is not the lever.

> **Decision (settled):** model size is a dead end — **reverted the deriver to
> `qwen2.5:14b`** (`.env` line 7, `up -d --force-recreate deriver api`, verified via
> `printenv`; `.env.bak.qwen32-revert` kept). The lever was the **parser**.

**Parser fix cleared the bar (ws `bench-strip`, 2026-06-12).** `parse_transcripts.py`
now strips CC continuation/auto-compact summaries from user turns — drop the
auto-generated recap + the injected continuation boilerplate, keep only John's
appended resume message (boundary anchor `"Pick up the last task as if the break
never happened."`, present in 34/34 corpus blocks; standard CC `"Please continue…"`
endings handled as fallbacks). Corpus-wide these blocks were **1.2 % of user turns
but 11.5 % of user char volume**; after the strip: **0 leakage, John's words
preserved** (e.g. f1a03124's two 13.5 K-char poison turns → 642 / 175 chars of
genuine prose). Re-derived the same 8 sessions under the **unchanged 14b** config:

- **75.0 % → 82.8 % grounded** (+7.8 pts, clears the ≥80 % bar) with **no model change**.
- **Over-attribution halved:** 13.1 % → 5.4 %.
- **`f1a03124`: 6 OVER / 4 leakage → 0 / 0.** The worst session is now clean.
- Residual (4 leakage, 5 OVER+HALL) is small and **diffuse**, not one poisoned
  session: assistant-bleed on an Alembic explanation (`5ce6403b`), the pasted
  `Context:` block in `40c12239`, and a couple of injected timestamps. These are
  the softer mode-2 (John-pasted, ambiguous-authorship) cases — left to
  `custom_instructions`, not worth mid-turn surgery on John's real intent.

> **The deriver config is settled: qwen2.5:14b + the patched parser.** Quality bar
> (≥80 % grounded) met. Proceed to the full batched ingest (`ingest_batch.py`).

Artifacts: `bench_set.json`, `bench_load.py`, `bench_audit_payload.py`,
`aggregate_bench.py`, `audit_payload_{default,bench-qwen32,bench-strip}.json`,
`grades_{default,bench-qwen32,bench-strip}_*.json`,
`bench_score_{default,bench-qwen32,bench-strip}.json`.

---

## Open questions / next benchmarks

1. **True DB space** — projections exclude HNSW index overhead. Ground-truth on
   Mando:
   ```bash
   docker compose exec database psql -U postgres -d honcho -c \
     "select pg_size_pretty(pg_total_relation_size('documents')) as docs,
             pg_size_pretty(pg_total_relation_size('message_embeddings')) as msg_emb;"
   ```
2. **Deriver health** — ✅ RESOLVED (Run 4, num_ctx cap). **Conclusion quality** —
   ✅ RESOLVED (Run 6): `frequency_penalty=0.3` killed the repetition loops but
   exposed CoT-bleed/truncation on dense content (Run 5, gemma); **swapping the
   deriver model to qwen2.5:14b** cleared all degeneration (0 % garbage) *and* ran
   2.6× faster (~14–16 h projected). Neither Option A (`repeat_penalty`) nor a
   stronger `frequency_penalty` was needed — root cause was model capability, not
   sampling. **Import is unblocked.**
3. **Faster embed path?** — at 0.237 s/msg the full import is ~25 min; a smaller
   embedder or larger embed batches could cut it, worth testing if we scale to all
   projects.

## Tooling reference (`_jgh_/honcho-import/`)

| Script | Purpose |
| --- | --- |
| `parse_transcripts.py <proj_dir> <out.json>` | transcripts→messages + memory→chunked conclusions (splits >24 K-char msgs) |
| **`ingest_batch.py --source <dir\|file\|corpus.json> [--batch-size N] [--batches K] [--drain-between-batches] [--dry-run\|--status\|--reset]`** | **current ingest tool** — resumable/pausable batched load, SQLite tally (`ingest_ledger.db`), `john-cc observe_me=true` |
| `load_to_honcho.py <payload.json> [--dry-run\|--conclusions-only]` | *(superseded)* one-shot POST a parsed payload to Honcho |
| `bench_memory_import.py [--dry-run]` | timed memory-only import → `bench_results.json` |
| `scan_project.py <proj_dir>` | parse-only volume scan (no API) |
| `bench_transcript_slice.py <proj_dir> [N] [--reset] [--load]` | timed stratified N-session slice → `bench_slice_results.json` |
| `bench_deriver.py [session_stem] [N]` | enqueue an observed batch to time derivation (needs healthy worker) |
| `bench_dialectic.py <model_tag> [level] [peer]` | time the /chat (Dialectic) path + capture responses → `bench_dialectic_<tag>.json` |
| `poll_deriver.py <internal_session_id> <N>` | track a session's representation tasks draining (frozen-aware) |

Raw data: `bench_results.json` (Run 1), `bench_slice_results.json` (Run 2),
`bench_deriver_results.json` (latest deriver run — overwritten each run; Run 6 =
qwen2.5:14b on peer `dbench-qwen`), `bench_dialectic_{gemma4-26b,qwen2.5-32b,qwen2.5-14b,qwen2.5-14b-max}.json`
(the Dialectic A/B, one file per model; `-max` = the max-tier follow-up).
