# `_jgh_/` — John's personal tooling (isolated from Honcho)

Everything under `_jgh_/` is personal tooling and experiments for running/testing a
self-hosted Honcho on the home LAN (Mando). It is **compartmentalized on purpose**:

> **Boundary rule:** nothing in `_jgh_/` is ever imported by `src/`, and nothing in
> `src/` depends on anything here. This folder talks to Honcho **only over the HTTP
> API** (`http://192.168.0.140:8000/v3`). Deleting `_jgh_/` has zero effect on Honcho.

Lives on the `_jgh_` branch. Companion personal docs/runbooks are in `_jgh_/docs/`.

## `honcho-import/` — Claude Code history → Honcho ingestion + benchmarking

Tooling to parse Claude Code transcripts/memory and POST them into Honcho, plus the
deriver/dialectic benchmark harness. All scripts use `urllib` against the REST API
(no Honcho imports) and resolve data files relative to their own location
(`Path(__file__).parent`), so the folder is self-contained and portable.

| Script | Purpose |
| --- | --- |
| `parse_transcripts.py <proj_dir> <out.json>` | transcripts → messages + memory → chunked conclusions |
| `load_to_honcho.py <payload.json> [--dry-run\|--conclusions-only]` | POST a parsed payload to Honcho |
| `import_groups.py <group 1..N> [--dry-run]` | grouped import of not-yet-loaded sessions (derive on `john-cc`) |
| `scan_project.py <proj_dir>` | parse-only volume scan (no API) |
| `bench_memory_import.py` / `bench_transcript_slice.py` | timed import benchmarks |
| `bench_deriver.py` / `bench_dialectic.py` / `poll_deriver.py` | deriver + dialectic benchmarking |
| `cleanup_test_peers.py` | remove throwaway benchmark peers |

Large/regenerable artifacts (`full_kb.json`, `honcho_payload.json`) are git-ignored —
regenerate with `parse_transcripts.py`. Small `bench_*_results.json` records are tracked.

> **Known issue (see `_jgh_/docs/` notes):** `parse_transcripts.py` currently keeps
> injected slash-command/skill bodies as if they were user turns and drops the real
> `<command-name>`/`<command-args>` — the cause of the `john-cc` over-attribution
> (~half of conclusions not grounded in John). Fix is scoped; apply before re-importing.
