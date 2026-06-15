"""A/B-benchmark the Dialectic (chat) path on Mando across deriver-model swaps.

The Dialectic is the user-facing, tool-using agent. Unlike the deriver bench
(which times queue drain), this times the synchronous /chat endpoint end-to-end:
per-query wall-time + the full response text for side-by-side quality comparison.

Protocol: run once per deployed model (e.g. gemma4:26b, then qwen2.5:32b after the
swap). Each run writes a model-tagged JSON so the two can be diffed.

Usage:
    python bench_dialectic.py <model_tag> [reasoning_level] [peer]
      model_tag       free label for the file, e.g. gemma4-26b / qwen2.5-32b
      reasoning_level  minimal|low|medium|high|max   (default: high)
      peer             observer/target peer          (default: dbench-qwen)
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

# Mando responses contain non-cp1252 chars; force UTF-8 on the Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://192.168.0.140:8000"
WS = "default"

MODEL_TAG = sys.argv[1] if len(sys.argv) > 1 else "unknown"
LEVEL = sys.argv[2] if len(sys.argv) > 2 else "high"
PEER = sys.argv[3] if len(sys.argv) > 3 else "dbench-qwen"
OUT = Path(__file__).parent / f"bench_dialectic_{MODEL_TAG}.json"

# Probes chosen to require recall from dbench-qwen's 24 conclusions (F0.23
# Phase 6, 18/18 tests, ruff, git worktrees/commits, the build-plan skill,
# Phase 7 BOSSK acceptance). A model that isn't grounding will be vague here.
PROBES = [
    "What project and phase is this person currently working on?",
    "What testing and code-quality practices does this person follow? Be specific.",
    "Describe this person's git workflow — branches, worktrees, commit habits.",
    "What is the 'build-plan' skill, and how does it differ from their other skills?",
    "What is coming up next in their work, and what does it involve?",
    "Summarize who this person is and what they're building, in 3 sentences.",
]


def chat(query):
    body = json.dumps({"query": query, "reasoning_level": LEVEL,
                       "stream": False}).encode()
    r = urllib.request.Request(
        f"{BASE}/v3/workspaces/{WS}/peers/{PEER}/chat", data=body,
        method="POST", headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(r, timeout=300) as resp:
        data = json.loads(resp.read() or "{}")
    return round(time.perf_counter() - t0, 2), data.get("content") or ""


def main():
    print(f"dialectic bench: model={MODEL_TAG} level={LEVEL} peer={PEER} "
          f"probes={len(PROBES)}", flush=True)
    rows = []
    for i, q in enumerate(PROBES, 1):
        secs, content = chat(q)
        cold = " (cold-load?)" if i == 1 else ""
        rows.append({"q": q, "secs": secs, "chars": len(content),
                     "content": content})
        print(f"  [{i}/{len(PROBES)}] {secs:>6.2f}s  {len(content):>4} chars{cold}  "
              f"{q[:48]}", flush=True)
        print(f"        -> {content[:160].replace(chr(10), ' ')}", flush=True)
        Path(OUT).write_text(json.dumps(
            {"model": MODEL_TAG, "level": LEVEL, "peer": PEER, "rows": rows},
            indent=2), encoding="utf-8")

    # Steady-state excludes probe 1 (carries any model cold-load).
    steady = [r["secs"] for r in rows[1:]] or [rows[0]["secs"]]
    summary = {
        "model": MODEL_TAG, "level": LEVEL, "peer": PEER,
        "n_probes": len(rows),
        "total_secs": round(sum(r["secs"] for r in rows), 1),
        "first_secs": rows[0]["secs"],
        "steady_mean_secs": round(sum(steady) / len(steady), 2),
        "steady_min_secs": min(steady),
        "steady_max_secs": max(steady),
        "mean_chars": round(sum(r["chars"] for r in rows) / len(rows)),
        "rows": rows,
    }
    Path(OUT).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nDONE [{MODEL_TAG}]: {len(rows)} probes | first={rows[0]['secs']}s "
          f"| steady mean={summary['steady_mean_secs']}s "
          f"(min {summary['steady_min_secs']} / max {summary['steady_max_secs']}) "
          f"| avg {summary['mean_chars']} chars", flush=True)
    print(f"results → {OUT.name}", flush=True)


if __name__ == "__main__":
    main()
