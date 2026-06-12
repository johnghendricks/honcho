"""Load the fixed 8-session benchmark set into a per-config workspace.

Model-config sweep helper: creates/ensures a bench workspace configured IDENTICALLY
to the qwen2.5:14b baseline (reasoning.enabled=true + the same anti-over-attribution
custom_instructions), ensures peers (john-cc observe_me=true / claude-cc
observe_me=false), then loads the 8 sessions from bench_set.json out of full_kb.json
as 'full-<orig>' sessions. The deriver (configured via .env to the model under test)
then derives them; audit with bench_audit_payload.py --workspace <ws>.

Only the deriver MODEL (.env) varies across the sweep; everything here is held
constant so the comparison is controlled.

Usage:  python bench_load.py --workspace bench-qwen32
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BASE = "http://192.168.0.225:8000"
USER_PEER, ASST_PEER = "john-cc", "claude-cc"
SOURCE_TAG = "cc-transcript-import"
BENCH = json.loads((HERE / "bench_set.json").read_text(encoding="utf-8"))["sessions"]

# Identical anti-over-attribution instructions used for the baseline (canonical
# clean em-dash form). Held constant across every config in the sweep.
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


def api(method, path, body=None, timeout=120):
    url = f"{BASE}/v3{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or "{}")


def api_ok(method, path, body=None, ok=(200, 201, 409)):
    try:
        return api(method, path, body)
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return None
        raise SystemExit(f"HTTP {e.code} {method} {path}: {e.read().decode('utf-8','replace')[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    ws = args.workspace

    # Workspace with reasoning on + identical custom_instructions (PUT replaces, so
    # send the full reasoning block in one shot).
    api_ok("POST", "/workspaces", {"id": ws})
    api("PUT", f"/workspaces/{ws}", {"configuration": {"reasoning": {
        "enabled": True, "custom_instructions": CUSTOM_INSTRUCTIONS}}})
    cfg = api("POST", "/workspaces", {"id": ws}).get("configuration", {})
    r = cfg.get("reasoning", {})
    assert r.get("enabled") is True and r.get("custom_instructions"), f"reasoning not set: {r}"

    api_ok("POST", f"/workspaces/{ws}/peers",
           {"id": USER_PEER, "configuration": {"observe_me": True}})
    api_ok("POST", f"/workspaces/{ws}/peers",
           {"id": ASST_PEER, "configuration": {"observe_me": False}})

    corpus = json.loads((HERE / "full_kb.json").read_text(encoding="utf-8"))
    by = {s["session_id"]: s for s in corpus["sessions"]}

    print(f"workspace={ws}  reasoning.enabled=True  custom_instructions set")
    print(f"peers: {USER_PEER}(observe_me=true) {ASST_PEER}(observe_me=false)")
    total_msgs = 0
    t0 = time.perf_counter()
    for orig in BENCH:
        s = by[orig]
        sid = "full-" + orig
        api_ok("POST", f"/workspaces/{ws}/sessions", {
            "id": sid, "metadata": {"source": "claude-code", "bench": True,
                                    "orig_session": orig},
            "peers": {USER_PEER: {}, ASST_PEER: {}}})
        batch = []
        for m in s["messages"]:
            item = {"peer_id": USER_PEER if m["role"] == "user" else ASST_PEER,
                    "content": m["content"],
                    "metadata": {"source": SOURCE_TAG, "orig_session": orig}}
            if m.get("ts"):
                item["created_at"] = m["ts"]
            batch.append(item)
        for i in range(0, len(batch), 100):
            api("POST", f"/workspaces/{ws}/sessions/{sid}/messages",
                {"messages": batch[i:i + 100]}, timeout=600)
        total_msgs += len(batch)
        print(f"  loaded {sid}  ({len(batch)} msgs)")
    print(f"\nloaded {len(BENCH)} sessions, {total_msgs} msgs in "
          f"{time.perf_counter()-t0:.0f}s -> deriver will process")


if __name__ == "__main__":
    main()
