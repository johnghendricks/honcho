"""Purge the deriver-benchmark test peers' data from Mando Honcho.

Peers can't be hard-deleted via the v3 API, so this removes their *data*:
every self-conclusion they accumulated + the deriv-bench-* sessions that
produced them. Leaves empty peer stubs behind (harmless).

SAFETY: a conclusion is only deleted when BOTH observer_id and observed_id are
in TEST_PEERS. The real peers (john, claude, john-cc, claude-cc) are never
touched even if the server-side list filter returns extra rows.

Usage:
    python cleanup_test_peers.py            # dry-run: report only
    python cleanup_test_peers.py --apply    # actually delete
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://192.168.0.225:8000"
WS = "default"
TEST_PEERS = {"dbench", "dbench-fp", "dbench-fp2", "dbench-qwen"}
APPLY = "--apply" in sys.argv


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}/v3{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def all_conclusions():
    """Page through every workspace conclusion."""
    page, size = 1, 100
    while True:
        res = api("POST", f"/workspaces/{WS}/conclusions/list?page={page}&size={size}",
                  {"filters": {}})
        items = res.get("items", res) if isinstance(res, dict) else res
        if not items:
            break
        for it in items:
            yield it
        if len(items) < size:
            break
        page += 1


def main():
    to_delete, sessions, per_peer = [], set(), {}
    skipped = 0
    for c in all_conclusions():
        obs, obd = c.get("observer_id"), c.get("observed_id")
        # Guard: only test-peer self-conclusions.
        if obs in TEST_PEERS and obd in TEST_PEERS:
            to_delete.append(c["id"])
            per_peer[obs] = per_peer.get(obs, 0) + 1
            if c.get("session_id"):
                sessions.add(c["session_id"])
        else:
            skipped += 1

    print(f"scanned; protected (non-test) conclusions left alone: {skipped}")
    print(f"test-peer conclusions to delete: {len(to_delete)}")
    for p in sorted(per_peer):
        print(f"    {p}: {per_peer[p]}")
    bench_sessions = sorted(s for s in sessions if s.startswith("deriv-bench-"))
    other_sessions = sorted(s for s in sessions if not s.startswith("deriv-bench-"))
    print(f"bench sessions to delete: {len(bench_sessions)} {bench_sessions}")
    if other_sessions:
        print(f"  NOTE non-bench sessions referenced (NOT deleting): {other_sessions}")

    if not APPLY:
        print("\n[dry-run] re-run with --apply to delete")
        return

    dc = 0
    for cid in to_delete:
        try:
            api("DELETE", f"/workspaces/{WS}/conclusions/{cid}")
            dc += 1
        except urllib.error.HTTPError as e:
            print(f"  concl {cid}: HTTP {e.code}")
    print(f"deleted {dc}/{len(to_delete)} conclusions")

    ds = 0
    for sid in bench_sessions:
        try:
            api("DELETE", f"/workspaces/{WS}/sessions/{sid}")
            ds += 1
        except urllib.error.HTTPError as e:
            print(f"  session {sid}: HTTP {e.code}")
    print(f"deleted {ds}/{len(bench_sessions)} bench sessions")


if __name__ == "__main__":
    main()
