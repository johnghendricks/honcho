"""Benchmark: load a representative 25-session slice of a big project's
transcripts into Honcho, timing message-POST throughput.

In pgvector mode with EMBED_MESSAGES=true, create_messages embeds SYNCHRONOUSLY
inside the POST (crud/message.py:282 -> batch_embed, which chunks >512-tok
messages into multiple MessageEmbedding rows). So POST wall-time = insert + embed
+ chunk fan-out -- the true combined cost. We sample stratified by session size
so the per-message rate reflects the real size mix, then project to the full
project by message count.

Usage:
    python bench_transcript_slice.py <project_dir_name> [N] [--dry-run]
"""

import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_transcripts import parse_transcript  # noqa: E402

ROOT = Path(r"C:\Users\John Hendricks\.claude\projects")
BASE = "http://192.168.0.225:8000"
WS = "default"
USER_PEER, ASST_PEER = "john-cc", "claude-cc"
SESSION_PREFIX = "full-"
SOURCE = "cc-transcript-import"
CHARS_PER_TOKEN = 3.4
EMBED_MAX_TOKENS = 512  # bge-large cap -> rows per msg = ceil(tokens / 512)

DRY = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv
pos = [a for a in sys.argv[1:] if not a.startswith("--")]
PROJECT = pos[0]
N = int(pos[1]) if len(pos) > 1 else 25

# Honcho-project full- sessions to preserve during reset (everything else
# starting with full- is a kb-proto-1 slice/import artifact).
KEEP_FULL = {
    "full-45d5b056-f14d-4e89-a50b-0023455b9d9a",
    "full-9cbfca0c-3fe3-40a0-8431-96e5ad8c22c2",
    "full-a991cafd-6bf1-4b31-8b96-afdd95db2925",
    "full-d2ab8255-4fff-4fd1-a775-c9c62882c516",
    "full-e29e23af-3a0a-4998-a1f1-337380c3be41",
}


def post(path: str, body: dict) -> tuple[object, float]:
    req = urllib.request.Request(
        f"{BASE}/v3{path}", data=json.dumps(body).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read() or "{}")
    return out, time.perf_counter() - t0


def ensure_peer(pid: str):
    if not DRY:
        post(f"/workspaces/{WS}/peers", {"id": pid, "configuration": {"observe_me": False}})


def est_rows(chars: int) -> int:
    return max(1, math.ceil((chars / CHARS_PER_TOKEN) / EMBED_MAX_TOKENS))


def list_sessions() -> list[str]:
    out, _ = post(f"/workspaces/{WS}/sessions/list", {})
    items = out.get("items", out) if isinstance(out, dict) else out
    return [s["id"] for s in items]


def reset_slice_sessions():
    """Delete all full- slice artifacts (keeping the honcho-project ones),
    then poll until they fully drain from the session list."""
    orphans = [s for s in list_sessions() if s.startswith("full-") and s not in KEEP_FULL]
    print(f"reset: deleting {len(orphans)} orphan full- sessions")
    for sid in orphans:
        req = urllib.request.Request(f"{BASE}/v3/workspaces/{WS}/sessions/{sid}",
                                     method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code not in (404,):
                print(f"  warn {sid}: {e.code}")
    # Poll until gone (deletion marks inactive + async hard-delete).
    for _ in range(60):
        remaining = [s for s in list_sessions()
                     if s.startswith("full-") and s not in KEEP_FULL]
        if not remaining:
            print("reset: drained, all orphans gone")
            time.sleep(3)  # small buffer for async hard-delete to finish
            return
        time.sleep(2)
    raise SystemExit(f"reset: {len(remaining)} sessions still present after polling")


def main():
    if RESET:
        reset_slice_sessions()
        if "--load" not in sys.argv:
            print("reset complete (pass --load to also run the benchmark)")
            return

    proj = ROOT / PROJECT
    # Parse all sessions, collect size.
    sessions = []
    for jf in sorted(proj.glob("*.jsonl")):
        msgs = parse_transcript(jf)
        if msgs:
            chars = sum(len(m["content"]) for m in msgs)
            sessions.append({"sid": jf.stem, "msgs": msgs, "n": len(msgs), "chars": chars})
    sessions.sort(key=lambda s: s["n"])
    total_sessions = len(sessions)
    total_msgs = sum(s["n"] for s in sessions)

    # Stratified-by-size sample: evenly spaced indices across the sorted list.
    if total_sessions <= N:
        picks = sessions
    else:
        idxs = sorted({round(i * (total_sessions - 1) / (N - 1)) for i in range(N)})
        picks = [sessions[i] for i in idxs]

    sampled_msgs = sum(s["n"] for s in picks)
    sampled_chars = sum(s["chars"] for s in picks)
    sampled_rows = sum(est_rows(len(m["content"])) for s in picks for m in s["msgs"])

    print(f"== slice benchmark: {PROJECT} ==")
    print(f"project: {total_sessions} sessions / {total_msgs} msgs")
    print(f"sample:  {len(picks)} sessions / {sampled_msgs} msgs / {sampled_chars:,} chars "
          f"/ ~{sampled_rows} embed rows (dry={DRY})")

    ensure_peer(USER_PEER)
    ensure_peer(ASST_PEER)

    post_secs = 0.0
    n_batches = 0
    if not DRY:
        for s in picks:
            sid = SESSION_PREFIX + s["sid"]
            post(f"/workspaces/{WS}/sessions", {
                "id": sid,
                "metadata": {"source": "claude-code", "project": PROJECT, "import": "slice-bench"},
                "peers": {USER_PEER: {}, ASST_PEER: {}},
            })
            batch = []
            for m in s["msgs"]:
                item = {
                    "peer_id": USER_PEER if m["role"] == "user" else ASST_PEER,
                    "content": m["content"],
                    "metadata": {"source": SOURCE, "project": PROJECT, "orig_session": s["sid"]},
                }
                if m.get("ts"):
                    item["created_at"] = m["ts"]
                batch.append(item)
            for i in range(0, len(batch), 100):
                try:
                    _, secs = post(f"/workspaces/{WS}/sessions/{sid}/messages",
                                   {"messages": batch[i:i + 100]})
                except urllib.error.HTTPError as e:
                    raise SystemExit(f"{sid}: POST failed: {e.read().decode()[:300]}")
                post_secs += secs
                n_batches += 1

    # Rates + projection to full project.
    msgs_per_sec = round(sampled_msgs / post_secs, 2) if post_secs else 0
    secs_per_msg = round(post_secs / sampled_msgs, 4) if sampled_msgs else 0
    chars_per_sec = round(sampled_chars / post_secs) if post_secs else 0
    proj_post_secs = round(total_msgs * secs_per_msg, 1)
    proj_rows = round(sampled_rows * (total_msgs / sampled_msgs)) if sampled_msgs else 0
    # Storage: 1 MessageEmbedding row = 1024-dim float32 vector (4096 B) + content.
    proj_vec_mb = round(proj_rows * 1024 * 4 / 1024 / 1024, 1)

    result = {
        "project": PROJECT, "total_sessions": total_sessions, "total_msgs": total_msgs,
        "sample_sessions": len(picks), "sample_msgs": sampled_msgs,
        "sample_chars": sampled_chars, "sample_est_embed_rows": sampled_rows,
        "post_secs": round(post_secs, 3), "n_batches": n_batches,
        "msgs_per_sec": msgs_per_sec, "secs_per_msg": secs_per_msg,
        "chars_per_sec": chars_per_sec, "rows_per_msg": round(sampled_rows / sampled_msgs, 2) if sampled_msgs else 0,
        "PROJECTION": {
            "full_post_secs": proj_post_secs, "full_post_min": round(proj_post_secs / 60, 1),
            "full_embed_rows": proj_rows, "full_vector_mb": proj_vec_mb,
        },
    }
    Path(__file__).parent.joinpath("bench_slice_results.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")

    print(f"\nPOST: {sampled_msgs} msgs in {post_secs:.1f}s over {n_batches} batches")
    print(f"  {msgs_per_sec} msgs/s | {secs_per_msg}s/msg | {chars_per_sec:,} chars/s")
    print(f"  fan-out: ~{result['rows_per_msg']} embed rows/msg (avg msg ~{round(sampled_chars/sampled_msgs/CHARS_PER_TOKEN)} tok)")
    print(f"PROJECTION to {total_msgs} msgs:")
    print(f"  POST+embed time: ~{proj_post_secs}s (~{result['PROJECTION']['full_post_min']} min)")
    print(f"  embed rows: ~{proj_rows:,} -> ~{proj_vec_mb} MB vectors (+ content)")
    print("wrote bench_slice_results.json")


if __name__ == "__main__":
    main()
