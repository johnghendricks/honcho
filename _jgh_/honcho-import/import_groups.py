"""Grouped kb-proto-1 import with derivation on john-cc only.

Splits the not-yet-loaded sessions into NGROUPS contiguous groups and imports one
group per invocation. Sets observe_me=true on john-cc (its user-authored turns
enqueue derivation -> builds John's representation) and observe_me=false on
claude-cc (assistant turns load as data but don't derive). Never resets config.

Idempotent on sessions/peers (get-or-create), but message-adds are NOT deduped:
already-loaded sessions are excluded via loaded_sessions.json, so run each group
exactly once.

Usage:
    python import_groups.py <group 1..N> [--dry-run]
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://192.168.0.140:8000"
WS = "default"
USER_PEER = "john-cc"
ASST_PEER = "claude-cc"
PREFIX = "full-"
SOURCE = "cc-transcript-import"
NGROUPS = 4

HERE = Path(__file__).parent
DRY = "--dry-run" in sys.argv
GROUP = int([a for a in sys.argv[1:] if not a.startswith("--")][0])  # 1-based


def api(method, path, body=None):
    if DRY and method != "GET":
        return {}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(f"{BASE}/v3{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail[:400]}")


def split_groups():
    payload = json.load(open(HERE / "full_kb.json", encoding="utf-8"))
    loaded = set(json.load(open(HERE / "loaded_sessions.json")))
    remaining = [s for s in payload["sessions"] if s["session_id"] not in loaded]
    remaining.sort(key=lambda s: s["session_id"])
    n = len(remaining)
    base, extra = divmod(n, NGROUPS)
    groups, i = [], 0
    for g in range(NGROUPS):
        size = base + (1 if g < extra else 0)
        groups.append(remaining[i:i + size])
        i += size
    return payload["project"], remaining, groups


def main():
    project, remaining, groups = split_groups()
    mine = groups[GROUP - 1]
    grp_msgs = sum(len(s["messages"]) for s in mine)
    grp_user = sum(sum(1 for m in s["messages"] if m["role"] == "user") for s in mine)
    print(f"== import group {GROUP}/{NGROUPS} | project={project} | dry={DRY} ==")
    print(f"remaining sessions (all groups): {len(remaining)}")
    print(f"this group: {len(mine)} sessions, {grp_msgs} msgs "
          f"({grp_user} user-authored -> derive on john-cc)")

    # Peers: john-cc observes (derives), claude-cc does not. Config is overwritten
    # on existing peers when different, so this reliably flips observe_me.
    api("POST", f"/workspaces/{WS}/peers",
        {"id": USER_PEER, "configuration": {"observe_me": True}})
    api("POST", f"/workspaces/{WS}/peers",
        {"id": ASST_PEER, "configuration": {"observe_me": False}})
    print(f"peers: {USER_PEER}=observe_me:true  {ASST_PEER}=observe_me:false")

    t0 = time.perf_counter()
    done_msgs = 0
    for n_s, s in enumerate(mine, 1):
        sid = PREFIX + s["session_id"]
        api("POST", f"/workspaces/{WS}/sessions", {
            "id": sid,
            "metadata": {"source": "claude-code", "project": project,
                         "import": "full-transcript", "group": GROUP},
            "peers": {USER_PEER: {}, ASST_PEER: {}},
        })
        batch = []
        for m in s["messages"]:
            peer = USER_PEER if m["role"] == "user" else ASST_PEER
            item = {"peer_id": peer, "content": m["content"],
                    "metadata": {"source": SOURCE, "project": project,
                                 "orig_session": s["session_id"]}}
            if m.get("ts"):
                item["created_at"] = m["ts"]
            batch.append(item)
        for i in range(0, len(batch), 100):
            api("POST", f"/workspaces/{WS}/sessions/{sid}/messages",
                {"messages": batch[i:i + 100]})
        done_msgs += len(batch)
        if n_s % 25 == 0 or n_s == len(mine):
            el = time.perf_counter() - t0
            rate = done_msgs / el if el else 0
            print(f"  [{n_s}/{len(mine)}] {sid[:20]}.. "
                  f"+{len(batch)} msgs | {done_msgs} total | {rate:.1f} msg/s", flush=True)

    el = round(time.perf_counter() - t0, 1)
    print(f"== group {GROUP} done: {len(mine)} sessions, {done_msgs} msgs in {el}s "
          f"({round(grp_user)} derivation tasks queued on {USER_PEER}) ==")
    if DRY:
        print("[dry-run] nothing written")


if __name__ == "__main__":
    main()
