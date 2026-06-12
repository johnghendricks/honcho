"""Ablation: does claude-cc (assistant) context drive john-cc over-interpretation?

Question (see _jgh_/docs/clean-reinstall-and-batched-ingest.md + the
import-over-attribution memory): the contaminated john-cc store audited at ~41%
grounded / ~51% over-interpreted. The runbook attributes the over-interpretation
to source contamination. But john-cc's OWN turns carry no tool/thinking artifacts
(the parser strips thinking; [tools:] markers land only in claude-cc turns). The
ONLY way assistant/tool content reaches john's self-representation is as labeled
session CONTEXT: the deriver formats every turn as "<ts> <speaker>: <content>"
and is told to "Extract ALL observations from {peer_id} messages, using others as
context" (src/deriver/prompts.py:67). Contamination = the model turning a
"claude-cc: I refactored X with [tools: Edit]" turn into a john fact.

This script loads the SAME 50 stratified source sessions into three arms in a
dedicated `ablation` workspace, varying ONLY what context john's deriver sees:

  Arm A  john user turns + claude assistant turns (verbatim, incl. [tools:])   <- current import shape
  Arm B  john user turns ONLY (no claude peer in the session)                  <- structural isolation
  Arm C  john user turns + claude assistant PROSE with [tools:] lines stripped <- isolates the tool markers

Model / penalty / reasoning / custom_instructions are held constant across arms
(workspace-level), so the only variable is session composition. After draining,
run the source-verified grounding audit per arm and compare over-interpretation.

Boundary rule: API + operator config only; nothing here touches Honcho src/.

Usage:
    python ablation_assistant_context.py --setup [N]   # load arms (default N=50)
    python ablation_assistant_context.py --reset        # delete the ablation workspace data
    python ablation_assistant_context.py --status       # per-arm session/msg/task counts
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("HONCHO_BASE", "http://192.168.0.225:8000")
WS = os.environ.get("ABLATION_WS", "ablation")
KB = Path(__file__).parent / "full_kb.json"
SOURCE = "ablation-assistant-context"

# Held constant across all arms (the thing Phase-2 will validate).
CUSTOM_INSTRUCTIONS = (
    "When forming conclusions about this peer, attribute a fact to them only if "
    "they personally authored or stated it in their own messages. Do not attribute "
    "content from assistant replies, system notifications, task notifications, tool "
    "output, or injected skill/command definitions to the peer. Do not infer the "
    "peer's preferences, ownership, habits, or identity from incidental data such as "
    "file paths, directory listings, process lists, or pasted command output — only "
    "from what the peer explicitly says or does. When you do form a conclusion, stay "
    "close to what the message supports: prefer specific, source-traceable facts over "
    "broad generalizations about the peer's character, expertise, or intentions."
)

# arm -> (john_peer, claude_peer_or_None, session_prefix)
ARMS = {
    "A": ("abl-john-a", "abl-claude-a", "abl-a-"),
    "B": ("abl-john-b", None, "abl-b-"),
    "C": ("abl-john-c", "abl-claude-c", "abl-c-"),
}

_TOOLS_LINE = re.compile(r"^\[tools:.*\]$")


def strip_tools(content: str) -> str:
    """Arm C: drop the [tools: ...] marker lines, keep assistant prose."""
    kept = [ln for ln in content.splitlines() if not _TOOLS_LINE.match(ln.strip())]
    return "\n".join(kept).strip()


def api(method: str, path: str, body: dict | None = None) -> dict | list:
    url = f"{BASE}/v3{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail[:600]}")


# Floor on session size: pure 2-turn exchanges give john ~1 assistant turn of
# context (Arm A) vs 0 (Arm B) -> no room for the contamination gap to show.
# Sampling from sessions with >= this many messages concentrates discriminating
# power while still spanning the full size range (381 of 640 sessions qualify).
MIN_MSGS = int(os.environ.get("ABLATION_MIN_MSGS", "6"))


def sample_sessions(n: int) -> list[dict]:
    """Even-stride stratified sample over sessions sorted by message count.

    Same method as bench_transcript_slice.py, but floored at MIN_MSGS so every
    pick carries real multi-turn claude context for the A-vs-B comparison.
    """
    kb = json.load(open(KB, encoding="utf-8"))
    sessions = [s for s in kb["sessions"]
                if s.get("messages") and len(s["messages"]) >= MIN_MSGS]
    sessions.sort(key=lambda s: len(s["messages"]))
    total = len(sessions)
    if total <= n:
        return sessions
    idxs = sorted({round(i * (total - 1) / (n - 1)) for i in range(n)})
    return [sessions[i] for i in idxs]


def ensure_workspace():
    api("POST", "/workspaces", {
        "id": WS,
        "configuration": {
            "reasoning": {"enabled": True, "custom_instructions": CUSTOM_INSTRUCTIONS},
        },
    })
    # POST get-or-creates; PUT to be sure config is applied even if it pre-existed.
    api("PUT", f"/workspaces/{WS}", {
        "configuration": {
            "reasoning": {"enabled": True, "custom_instructions": CUSTOM_INSTRUCTIONS},
        },
    })


def ensure_peer(pid: str, observe_me: bool):
    api("POST", f"/workspaces/{WS}/peers",
        {"id": pid, "configuration": {"observe_me": observe_me}})


def load_arm(arm: str, picks: list[dict]) -> dict:
    john, claude, prefix = ARMS[arm]
    ensure_peer(john, observe_me=True)        # self-observe -> (john, john) collection
    if claude:
        ensure_peer(claude, observe_me=False)  # context only; not self-observed

    peers = {john: {}} if not claude else {john: {}, claude: {}}
    john_msgs = 0
    sessions_loaded = 0
    for s in picks:
        sid = prefix + s["session_id"]
        api("POST", f"/workspaces/{WS}/sessions",
            {"id": sid, "metadata": {"source": SOURCE, "arm": arm,
                                     "orig_session": s["session_id"]}, "peers": peers})
        batch = []
        for m in s["messages"]:
            if m["role"] == "user":
                content, peer = m["content"], john
            else:
                if not claude:
                    continue  # Arm B: drop assistant turns entirely
                content = strip_tools(m["content"]) if arm == "C" else m["content"]
                if not content:
                    continue
                peer = claude
            item = {"peer_id": peer, "content": content,
                    "metadata": {"source": SOURCE, "arm": arm,
                                 "orig_session": s["session_id"]}}
            if m.get("ts"):
                item["created_at"] = m["ts"]
            if peer == john:
                john_msgs += 1
            batch.append(item)
        for i in range(0, len(batch), 100):
            api("POST", f"/workspaces/{WS}/sessions/{sid}/messages",
                {"messages": batch[i:i + 100]})
        sessions_loaded += 1
    return {"arm": arm, "john_peer": john, "sessions": sessions_loaded,
            "john_msgs": john_msgs}


def reset():
    """Delete every abl-* session + peer in the ablation workspace."""
    out = api("POST", f"/workspaces/{WS}/sessions/list", {})
    items = out.get("items", out) if isinstance(out, dict) else out
    sids = [s["id"] for s in items if s["id"].startswith("abl-")]
    for sid in sids:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{BASE}/v3/workspaces/{WS}/sessions/{sid}", method="DELETE"),
                timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"  warn delete {sid}: {e.code}")
    print(f"reset: deleted {len(sids)} abl-* sessions in ws={WS}")
    for john, claude, _ in ARMS.values():
        for pid in (john, claude):
            if not pid:
                continue
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"{BASE}/v3/workspaces/{WS}/peers/{pid}", method="DELETE"),
                    timeout=60).read()
            except urllib.error.HTTPError:
                pass


def status():
    out = api("POST", f"/workspaces/{WS}/sessions/list", {})
    items = out.get("items", out) if isinstance(out, dict) else out
    by_arm: dict[str, int] = {}
    for s in items:
        for arm, (_, _, prefix) in ARMS.items():
            if s["id"].startswith(prefix):
                by_arm[arm] = by_arm.get(arm, 0) + 1
    print(f"ws={WS} sessions by arm: {by_arm}")
    try:
        q = api("GET", f"/workspaces/{WS}/queue/status")  # type: ignore[arg-type]
        if isinstance(q, dict):
            print(f"queue: pending={q.get('pending_work_units')} "
                  f"in_progress={q.get('in_progress_work_units')} "
                  f"completed={q.get('completed_work_units')}")
    except SystemExit as e:
        print(f"(queue status unavailable: {e})")


def main():
    if "--reset" in sys.argv:
        reset()
        return
    if "--status" in sys.argv:
        status()
        return
    if "--setup" not in sys.argv:
        raise SystemExit(__doc__)

    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(pos[0]) if pos else 50
    picks = sample_sessions(n)
    msgs = sum(len(s["messages"]) for s in picks)
    print(f"== ablation setup: ws={WS} base={BASE} ==")
    print(f"source: {len(picks)} stratified sessions / {msgs} msgs (from {KB.name})")
    print(f"custom_instructions: {len(CUSTOM_INSTRUCTIONS)} chars (held constant)\n")

    ensure_workspace()
    results = [load_arm(arm, picks) for arm in ("A", "B", "C")]
    print()
    for r in results:
        print(f"  Arm {r['arm']}: {r['sessions']} sessions, "
              f"john peer={r['john_peer']} john_msgs(=rep tasks)={r['john_msgs']}")
    total_tasks = sum(r["john_msgs"] for r in results)
    print(f"\nloaded. ~{total_tasks} john representation tasks queued across 3 arms.")
    print("next: start the deriver on Mando, then poll:")
    print(f"  python ablation_assistant_context.py --status")
    Path(__file__).parent.joinpath("ablation_setup.json").write_text(
        json.dumps({"ws": WS, "n_sessions": len(picks), "src_msgs": msgs,
                    "arms": results}, indent=2), encoding="utf-8")
    print("wrote ablation_setup.json")


if __name__ == "__main__":
    main()
