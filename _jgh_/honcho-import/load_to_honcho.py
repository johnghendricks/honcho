"""Load a parsed transcript payload into a Honcho deployment via the v3 REST API.

Reusable for the full 828-file import. Idempotent on peers/sessions (get-or-create),
but message-adds are NOT deduped -- only run once per payload.

Usage:
    python load_to_honcho.py <payload.json> [--dry-run]

Config via env or the constants below:
    HONCHO_BASE  default http://192.168.0.225:8000
    HONCHO_WS    default default
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HONCHO_BASE", "http://192.168.0.225:8000")
WS = os.environ.get("HONCHO_WS", "default")
USER_PEER = "john-cc"
ASST_PEER = "claude-cc"
SESSION_PREFIX = "full-"
SOURCE = "cc-transcript-import"

DRY = "--dry-run" in sys.argv
CONCLUSIONS_ONLY = "--conclusions-only" in sys.argv


def api(method: str, path: str, body: dict | None = None) -> dict | list:
    url = f"{BASE}/v3{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if DRY:
        print(f"  [dry] {method} {path} "
              f"({len(body.get('messages', body.get('conclusions', []))) if body else 0} items)")
        return {}
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail[:500]}")


def ensure_peer(peer_id: str):
    api("POST", f"/workspaces/{WS}/peers",
        {"id": peer_id, "configuration": {"observe_me": False}})


def ensure_session(session_id: str, project: str):
    api("POST", f"/workspaces/{WS}/sessions", {
        "id": session_id,
        "metadata": {"source": "claude-code", "project": project, "import": "full-transcript"},
        "peers": {USER_PEER: {}, ASST_PEER: {}},
    })


def add_messages(session_id: str, msgs: list[dict], project: str, orig: str):
    batch = []
    for m in msgs:
        peer = USER_PEER if m["role"] == "user" else ASST_PEER
        item = {
            "peer_id": peer,
            "content": m["content"],
            "metadata": {"source": SOURCE, "project": project, "orig_session": orig},
        }
        if m.get("ts"):
            item["created_at"] = m["ts"]
        batch.append(item)
    for i in range(0, len(batch), 100):
        chunk = batch[i:i + 100]
        api("POST", f"/workspaces/{WS}/sessions/{session_id}/messages", {"messages": chunk})


def main():
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    project = payload["project"]

    print(f"== loading project={project} into {BASE} ws={WS} "
          f"(dry={DRY} conclusions_only={CONCLUSIONS_ONLY}) ==")
    ensure_peer(USER_PEER)
    ensure_peer(ASST_PEER)
    print(f"peers ensured: {USER_PEER}, {ASST_PEER} (observe_me=false)")

    total_msgs = 0
    if not CONCLUSIONS_ONLY:
        for s in payload["sessions"]:
            sid = SESSION_PREFIX + s["session_id"]
            ensure_session(sid, project)
            add_messages(sid, s["messages"], project, s["session_id"])
            total_msgs += len(s["messages"])
            print(f"  session {sid}: +{len(s['messages'])} msgs")

    conclusions = payload.get("conclusions", [])
    if conclusions:
        body = {"conclusions": [
            {"content": c, "observer_id": USER_PEER, "observed_id": USER_PEER}
            for c in conclusions
        ]}
        api("POST", f"/workspaces/{WS}/conclusions", body)
        print(f"  conclusions: +{len(conclusions)} (observer={USER_PEER} observed={USER_PEER})")

    print(f"== done: {len(payload['sessions'])} sessions, {total_msgs} msgs, "
          f"{len(conclusions)} conclusions ==")


if __name__ == "__main__":
    main()
