"""Assemble the Phase-2 grounding-audit payload (single-arm: default/john-cc/full-*).

Adapts the ablation's build_audit_payload.py to the clean-reinstall import:
pulls every EXPLICIT john-cc conclusion + the source transcript for a SEEDED
RANDOM sample of full-* sessions, and writes one judge bundle per session:
{orig, honcho_session, transcript:[{speaker,text}], conclusions:[{id,text}]}.

Random sample of SESSIONS (seed fixed for reproducibility); all john-cc explicit
conclusions in each chosen session are graded. Sessions are drawn until the
conclusion budget (TARGET_MIN..TARGET_MAX) is met, so n ~= 50-70 conclusions.

Speakers are relabelled USER (john-cc, the profiled peer) / ASSISTANT (claude-cc),
so the judge sees who actually authored each turn -- a conclusion is GROUNDED
only if the profiled peer personally said/did it. source_ids is null on
minimal-deriver output, so grounding is judged against the whole session.

Read-only: SELECTs over the live Mando Postgres via ssh+psql (same access the
ablation used; no writes, no src/ changes).

Usage:  python build_audit_payload_p2.py
Output: audit_payload_p2.json   (list of bundles)
"""

import json
import random
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
WS = "default"
PEER = "john-cc"
SEED = 1337
TARGET_MIN, TARGET_MAX = 55, 75   # conclusion budget for the sample


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


# All explicit john-cc self-conclusions (observer==observed==john-cc).
concl = psql_json(f"""
SELECT COALESCE(json_agg(json_build_object(
  'session', session_name, 'id', id, 'text', content)), '[]')
FROM documents
WHERE workspace_name='{WS}' AND observer='{PEER}' AND observed='{PEER}'
  AND level='explicit' AND deleted_at IS NULL;
""")

by_session: dict[str, list[dict]] = {}
for c in concl:
    by_session.setdefault(c["session"], []).append({"id": c["id"], "text": c["text"]})

print(f"explicit john-cc conclusions={len(concl)} across {len(by_session)} sessions")

# Seeded random sample of sessions until the conclusion budget is met.
sessions = sorted(by_session)            # deterministic base order
random.seed(SEED)
random.shuffle(sessions)
chosen: list[str] = []
n_concl = 0
for s in sessions:
    if n_concl >= TARGET_MIN:
        break
    chosen.append(s)
    n_concl += len(by_session[s])
print(f"sampled sessions={len(chosen)}  conclusions={n_concl}")

IN = ",".join("'" + s + "'" for s in chosen)
msgs = psql_json(f"""
SELECT COALESCE(json_agg(json_build_object(
  'session', session_name, 'seq', seq_in_session,
  'peer', peer_name, 'text', content)
  ORDER BY session_name, seq_in_session), '[]')
FROM messages
WHERE workspace_name='{WS}' AND session_name IN ({IN});
""")

bundles: dict[str, dict] = {}
for s in chosen:
    bundles[s] = {"honcho_session": s, "orig": s.replace("full-", "", 1),
                  "transcript": [], "conclusions": by_session[s]}
for m in msgs:
    b = bundles[m["session"]]
    speaker = "USER" if m["peer"] == PEER else "ASSISTANT"
    txt = m["text"]
    if len(txt) > 4000:            # bound very long turns; keep head+tail
        txt = txt[:2600] + "\n…[truncated]…\n" + txt[-1200:]
    b["transcript"].append({"speaker": speaker, "text": txt})

out = [b for b in bundles.values() if b["conclusions"]]
out.sort(key=lambda b: b["orig"])
(HERE / "audit_payload_p2.json").write_text(json.dumps(out, indent=2))
print(f"wrote audit_payload_p2.json  bundles={len(out)}  "
      f"total conclusions={sum(len(b['conclusions']) for b in out)}")
