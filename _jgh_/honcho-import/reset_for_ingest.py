"""Reset Mando's Honcho to a clean pre-ingest state — WITHOUT touching live data.

Cleanup before the full john-cc transcript ingest:
  1. DELETE the throwaway grounding-bench workspaces entirely (bench-qwen32,
     bench-strip): delete every session (cascades messages + embeddings +
     conclusions + queue items), then delete the workspace (background cascade
     removes peers + anything left).
  2. PRESERVE the `default` workspace and, inside it, the LIVE /clear-hook memory
     (peers `john`/`claude`, `cc-*` sessions, (john,john) conclusions). These are
     John's real memory — never deleted here.
  3. RE-APPLY default's production `custom_instructions` (the live hook appears to
     re-PUT a minimal config that drops it). reasoning.enabled stays true; the
     import peers john-cc(observe_me=true)/claude-cc(observe_me=false) are ensured.

Idempotent and safe to re-run. API-only (no DB surgery), per the runbook boundary.

Usage:  python reset_for_ingest.py --dry-run     # show plan, no writes
        python reset_for_ingest.py --yes         # execute
"""

import argparse
import json
import time
import urllib.error
import urllib.request

BASE = "http://192.168.0.225:8000"
KEEP_WS = "default"
DROP_WS = ["bench-qwen32", "bench-strip"]
USER_PEER, ASST_PEER = "john-cc", "claude-cc"

# The proven anti-over-attribution instructions (same string as the 82.8%-PASS
# bench config in bench_load.py). Applied to `default` for the upcoming ingest.
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


def api_try(method, path, body=None, ok=(200, 201, 202, 404, 409)):
    try:
        return api(method, path, body), 200
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return None, e.code
        raise SystemExit(
            f"HTTP {e.code} {method} {path}: {e.read().decode('utf-8','replace')[:300]}")


def list_workspaces():
    r = api("POST", "/workspaces/list", {})
    return [w["id"] for w in (r.get("items") if isinstance(r, dict) else r)]


def list_sessions(ws):
    r = api("POST", f"/workspaces/{ws}/sessions/list", {})
    return [s["id"] for s in (r.get("items") if isinstance(r, dict) else r)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.yes):
        raise SystemExit("pass --dry-run to preview or --yes to execute")

    present = set(list_workspaces())
    print(f"workspaces present: {sorted(present)}")
    print(f"PRESERVE: {KEEP_WS} (incl. live john/cc-* memory)")
    print(f"DROP:     {[w for w in DROP_WS if w in present]}\n")

    # 1) Delete the throwaway bench workspaces.
    for ws in DROP_WS:
        if ws not in present:
            print(f"[skip] {ws} already gone")
            continue
        sids = list_sessions(ws)
        print(f"[{ws}] {len(sids)} sessions to delete, then drop workspace")
        if args.dry_run:
            continue
        for sid in sids:
            api_try("DELETE", f"/workspaces/{ws}/sessions/{sid}")
        # workspace delete 409s while sessions still draining -> retry a few times
        for attempt in range(12):
            _, code = api_try("DELETE", f"/workspaces/{ws}")
            if code in (200, 202, 404):
                print(f"[{ws}] workspace deletion accepted (code {code})")
                break
            time.sleep(5)
        else:
            print(f"[{ws}] WARN: still 409 (active sessions draining) — re-run later")

    # 2) Re-apply default's production config (reasoning + custom_instructions),
    #    ensure import peers. Live john/claude peers + cc-* sessions untouched.
    print(f"\n[{KEEP_WS}] re-applying reasoning + custom_instructions; ensuring peers")
    if not args.dry_run:
        api("PUT", f"/workspaces/{KEEP_WS}", {"configuration": {"reasoning": {
            "enabled": True, "custom_instructions": CUSTOM_INSTRUCTIONS}}})
        api_try("POST", f"/workspaces/{KEEP_WS}/peers",
                {"id": USER_PEER, "configuration": {"observe_me": True}})
        api_try("POST", f"/workspaces/{KEEP_WS}/peers",
                {"id": ASST_PEER, "configuration": {"observe_me": False}})
        cfg = api("POST", "/workspaces", {"id": KEEP_WS}).get("configuration", {})
        r = cfg.get("reasoning", {})
        ok = r.get("enabled") is True and bool(r.get("custom_instructions"))
        print(f"[{KEEP_WS}] reasoning.enabled={r.get('enabled')} "
              f"custom_instructions={'set' if r.get('custom_instructions') else 'MISSING'} "
              f"-> {'OK' if ok else 'CHECK'}")

    # 3) Verify.
    if not args.dry_run:
        remaining = set(list_workspaces())
        print(f"\nworkspaces now: {sorted(remaining)}")
        gone = [w for w in DROP_WS if w not in remaining]
        print(f"dropped: {gone}   still-present: {[w for w in DROP_WS if w in remaining]}")
    print("\ndone." if args.yes else "\n(dry-run — no changes made)")


if __name__ == "__main__":
    main()
