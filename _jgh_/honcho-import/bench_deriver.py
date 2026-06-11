"""Benchmark the Deriver (representation formation) cost on Mando/gemma4:26b.

Derivation is the dominant deferred cost of the import. It only runs for peers
with observe_me=true, and is triggered at message-create time. So we create a
dedicated observing peer, feed it a representative kb-proto-1 session's messages,
and poll the session-scoped queue status until the representation tasks drain --
timing the whole thing (including the first-call gemma4:26b cold-load).

Writes bench_deriver_results.json incrementally (one rewrite per poll) so progress
can be watched mid-run.

Usage:
    python bench_deriver.py [session_file_stem] [N]
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_transcripts import parse_transcript  # noqa: E402

KB = Path(r"C:\Users\John Hendricks\.claude\projects\D--Git-Be-Sentient-kb-proto-1")
BASE = "http://192.168.0.225:8000"
WS = "default"
PEER = "dbench-qwen"  # Run 6: deriver model swapped to qwen2.5:14b (frequency_penalty=0.3 still live)
OUT = Path(__file__).parent / "bench_deriver_results.json"

pos = [a for a in sys.argv[1:] if not a.startswith("--")]
SESSION_FILE = pos[0] if pos else "e021ba3d-2799-47a1-b918-93172436e405"
N = int(pos[1]) if len(pos) > 1 else 12
POLL_SECS = 4
MAX_WAIT = 2400  # 40 min safety cap


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}/v3{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read() or "{}")


def main():
    sid = f"deriv-bench-{int(time.time())}"
    msgs = parse_transcript(KB / f"{SESSION_FILE}.jsonl")[:N]
    n = len(msgs)
    total_chars = sum(len(m["content"]) for m in msgs)

    # Observing peer (forms a self-representation from its messages).
    req("POST", f"/workspaces/{WS}/peers", {"id": PEER, "configuration": {"observe_me": True}})
    req("POST", f"/workspaces/{WS}/sessions", {"id": sid, "peers": {PEER: {}},
        "metadata": {"bench": "deriver"}})

    # Enqueue: all turns authored by the observing peer.
    batch = [{"peer_id": PEER, "content": m["content"]} for m in msgs]
    t0 = time.perf_counter()
    req("POST", f"/workspaces/{WS}/sessions/{sid}/messages", {"messages": batch})
    enqueue_secs = time.perf_counter() - t0

    print(f"deriver bench: session={sid} msgs={n} chars={total_chars:,} "
          f"(~{round(total_chars/3.4)} tok); enqueued in {enqueue_secs:.1f}s", flush=True)

    timeline = []
    first_completion = None
    result = {"session": sid, "n_msgs": n, "total_chars": total_chars,
              "model": "gemma4:26b (Mando)", "source_session": SESSION_FILE}
    deadline = time.perf_counter() + MAX_WAIT
    while time.perf_counter() < deadline:
        time.sleep(POLL_SECS)
        try:
            st = req("GET", f"/workspaces/{WS}/queue/status?session_id={sid}")
        except urllib.error.HTTPError as e:
            print(f"status err {e.code}", flush=True); continue
        el = round(time.perf_counter() - t0, 1)
        # REST /queue/status returns snake_case (the MCP tool returns camelCase).
        # Reading camelCase here caused false total=0 "no work units" aborts.
        comp = st.get("completed_work_units", 0)
        inprog = st.get("in_progress_work_units", 0)
        pend = st.get("pending_work_units", 0)
        tot = st.get("total_work_units", 0)
        timeline.append({"t": el, "total": tot, "completed": comp,
                         "in_progress": inprog, "pending": pend})
        if first_completion is None and comp > 0:
            first_completion = el
        print(f"  t={el:>6}s total={tot} completed={comp} inprog={inprog} pending={pend}",
              flush=True)

        result.update({
            "enqueue_secs": round(enqueue_secs, 2),
            "timeline": timeline,
            "first_completion_secs": first_completion,
            "work_units_total": tot,
        })
        OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")

        if tot > 0 and pend == 0 and inprog == 0:
            break
        if el > 60 and tot == 0:
            print("ABORT: no work units after 60s — derivation not firing "
                  "(reasoning disabled? observe_me not set?)", flush=True)
            result["error"] = "no_work_units"
            OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return

    total_secs = round(timeline[-1]["t"], 1) if timeline else 0
    done = timeline[-1]["completed"] if timeline else 0
    # Steady-state: exclude the first completion (carries gemma cold-load).
    steady_per_unit = (round((total_secs - first_completion) / (done - 1), 1)
                       if first_completion and done > 1 else None)
    result.update({
        "total_secs": total_secs, "work_units_done": done,
        "secs_per_work_unit_overall": round(total_secs / done, 1) if done else None,
        "secs_per_work_unit_steady": steady_per_unit,
        "PROJECTION_full_kb_proto_1": {
            "msgs": 6386,
            "hours_overall": round(6386 * (total_secs / done) / 3600, 1) if done else None,
            "hours_steady": round(6386 * steady_per_unit / 3600, 1) if steady_per_unit else None,
        },
    })
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nDONE: {done} work units in {total_secs}s | "
          f"first(cold)={first_completion}s | steady={steady_per_unit}s/unit", flush=True)
    pj = result["PROJECTION_full_kb_proto_1"]
    print(f"PROJECTION 6,386 msgs: ~{pj['hours_overall']}h overall / "
          f"~{pj['hours_steady']}h steady-state", flush=True)


if __name__ == "__main__":
    main()
