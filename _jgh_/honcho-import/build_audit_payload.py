"""Assemble the grounding-audit payload for the assistant-context ablation.

Reads the chosen matched sessions (audit_sessions.json), pulls — straight from
the live Mando Postgres via ssh+psql (read-only) — every explicit john
conclusion and the full per-arm transcript for those sessions, and writes one
judge bundle per (arm, session): {transcript, conclusions}.

Speakers are relabelled generically (USER = the profiled person, ASSISTANT =
the other peer) so the judge is blind to the arm/hypothesis; the transcript
composition itself is the independent variable and the judge legitimately sees
it. source_ids is null on minimal-deriver output, so grounding is judged against
the whole session the conclusion was derived from.

Usage:  python build_audit_payload.py
Output: audit_payload.json  (list of bundles)
"""

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
ORIGS = json.loads((HERE / "audit_sessions.json").read_text())
IN = ",".join("'" + o + "'" for o in ORIGS)


def psql_json(sql: str):
    sql = " ".join(sql.split())   # one line: psql treats \n-prefixed tokens as meta-cmds
    out = subprocess.run(
        ["ssh", "mando",
         "docker exec honcho-database-1 psql -U postgres -d postgres -t -A -c "
         + json.dumps(sql)],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:500]}")
    raw = out.stdout.strip()
    return json.loads(raw) if raw else []


concl = psql_json(f"""
SELECT COALESCE(json_agg(json_build_object(
  'arm', substring(observed from 'abl-john-(.)'),
  'orig', substring(session_name from 'abl-.-(.*)'),
  'id', id, 'text', content)), '[]')
FROM documents
WHERE workspace_name='ablation' AND level='explicit' AND deleted_at IS NULL
  AND substring(session_name from 'abl-.-(.*)') IN ({IN});
""")

msgs = psql_json(f"""
SELECT COALESCE(json_agg(json_build_object(
  'arm', substring(session_name from 'abl-(.)-'),
  'orig', substring(session_name from 'abl-.-(.*)'),
  'seq', seq_in_session, 'peer', peer_name, 'text', content)
  ORDER BY session_name, seq_in_session), '[]')
FROM messages
WHERE workspace_name='ablation'
  AND substring(session_name from 'abl-.-(.*)') IN ({IN});
""")

# Bundle by (arm, orig).
bundles: dict[tuple[str, str], dict] = {}
for m in msgs:
    key = (m["arm"], m["orig"])
    b = bundles.setdefault(key, {"arm": m["arm"], "orig": m["orig"],
                                 "transcript": [], "conclusions": []})
    speaker = "USER" if m["peer"].startswith("abl-john") else "ASSISTANT"
    txt = m["text"]
    if len(txt) > 4000:            # bound very long turns; keep head+tail
        txt = txt[:2600] + "\n…[truncated]…\n" + txt[-1200:]
    b["transcript"].append({"speaker": speaker, "text": txt})

for c in concl:
    key = (c["arm"], c["orig"])
    b = bundles.setdefault(key, {"arm": c["arm"], "orig": c["orig"],
                                 "transcript": [], "conclusions": []})
    b["conclusions"].append({"id": c["id"], "text": c["text"]})

# Keep only bundles that actually have conclusions to grade.
out = [b for b in bundles.values() if b["conclusions"]]
out.sort(key=lambda b: (b["arm"], b["orig"]))
(HERE / "audit_payload.json").write_text(json.dumps(out, indent=2))

per_arm: dict[str, int] = {}
for b in out:
    per_arm[b["arm"]] = per_arm.get(b["arm"], 0) + len(b["conclusions"])
print(f"bundles={len(out)}  conclusions/arm={per_arm}")
print(f"total conclusions to grade={sum(per_arm.values())}")
print("wrote audit_payload.json")
