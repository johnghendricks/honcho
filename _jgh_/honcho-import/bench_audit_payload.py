"""Build the grounding-audit payload for ONE deriver-config benchmark workspace.

Model-config sweep: the SAME fixed 8 sessions (bench_set.json) are loaded into a
per-config workspace and derived under that config; this pulls every explicit
conclusion + the source transcript for those 8 sessions and writes one judge
bundle per session -> audit_payload_<workspace>.json. Blind judges then grade
each conclusion GROUNDED/PARTIAL/OVER/HALLUCINATED against its source transcript.

Speakers relabelled USER (the profiled peer) / ASSISTANT (the other), so the
judge sees who actually authored each turn -- a conclusion is GROUNDED only if
the profiled peer personally said/did it. Grading is against the whole session
(source_ids is null on minimal-deriver output).

Read-only psql over the live Mando Postgres (no writes, no src/ changes).

Usage:  python bench_audit_payload.py --workspace default       # qwen2.5:14b baseline
        python bench_audit_payload.py --workspace bench-qwen32  # a sweep config
Output: audit_payload_<workspace>.json
"""

import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
PEER = "john-cc"
BENCH = json.loads((HERE / "bench_set.json").read_text(encoding="utf-8"))["sessions"]
SESSIONS = ["full-" + s for s in BENCH]


def psql_json(sql: str):
    sql = " ".join(sql.split())   # one line: psql treats \n-prefixed tokens as meta-cmds
    out = subprocess.run(
        ["ssh", "mando",
         "docker exec honcho-database-1 psql -U postgres -d postgres -t -A -c "
         + json.dumps(sql)],
        capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:500]}")
    raw = out.stdout.strip()
    return json.loads(raw) if raw else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    ws = args.workspace
    IN = ",".join("'" + s + "'" for s in SESSIONS)

    concl = psql_json(f"""
    SELECT COALESCE(json_agg(json_build_object(
      'session', session_name, 'id', id, 'text', content)), '[]')
    FROM documents
    WHERE workspace_name='{ws}' AND observer='{PEER}' AND observed='{PEER}'
      AND level='explicit' AND deleted_at IS NULL
      AND session_name IN ({IN});
    """)
    by_session: dict[str, list[dict]] = {}
    for c in concl:
        by_session.setdefault(c["session"], []).append({"id": c["id"], "text": c["text"]})

    msgs = psql_json(f"""
    SELECT COALESCE(json_agg(json_build_object(
      'session', session_name, 'seq', seq_in_session,
      'peer', peer_name, 'text', content)
      ORDER BY session_name, seq_in_session), '[]')
    FROM messages
    WHERE workspace_name='{ws}' AND session_name IN ({IN});
    """)

    bundles: dict[str, dict] = {}
    for s in SESSIONS:
        bundles[s] = {"workspace": ws, "honcho_session": s,
                      "orig": s.replace("full-", "", 1),
                      "transcript": [], "conclusions": by_session.get(s, [])}
    for m in msgs:
        b = bundles[m["session"]]
        speaker = "USER" if m["peer"] == PEER else "ASSISTANT"
        txt = m["text"]
        if len(txt) > 4000:            # bound very long turns; keep head+tail
            txt = txt[:2600] + "\n…[truncated]…\n" + txt[-1200:]
        b["transcript"].append({"speaker": speaker, "text": txt})

    out = [b for b in bundles.values() if b["conclusions"]]
    out.sort(key=lambda b: b["orig"])
    payload = HERE / f"audit_payload_{ws}.json"
    payload.write_text(json.dumps(out, indent=2))
    n_concl = sum(len(b["conclusions"]) for b in out)
    print(f"workspace={ws}  sessions_with_conclusions={len(out)}/{len(SESSIONS)}  "
          f"total_conclusions={n_concl}")
    print(f"wrote {payload.name}")


if __name__ == "__main__":
    main()
