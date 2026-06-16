"""Assemble the blind grading payload for the Phase-2 Dreamer deep-dive.

The Dreamer derives *deductive* and *inductive* conclusions (and a peer card)
from john-cc's body of *explicit* conclusions. So grounding is judged against
the explicit-conclusion corpus, not raw transcripts.

This script:
  1. Pulls (read-only, via ssh+psql) the full john-cc explicit-conclusion corpus
     once and caches it locally (john_cc_explicit.json).
  2. Pulls the Phase-2 content file (dream_phase2_content.jsonl) from Mando.
  3. For every dreamer item (deductive / inductive / card) across all
     models+trials, retrieves the top-K most relevant explicit conclusions as
     grounding evidence (stdlib IDF bag-of-words — no deps).
  4. BLINDS the items: each gets an opaque id; the model/trial/type mapping is
     written to a private key file the judge never sees. Items are shuffled so
     ordering can't leak model identity. Bundles mix models together.

Outputs:
  dream_audit_bundles/bundle_NN.json  — judge-facing, blind:
        {items:[{id, type, claim, evidence:[{id,text}, ...]}]}
  dream_audit_key.json                — PRIVATE: {opaque_id: {model,trial,type}}
  DREAM_GRADING_RUBRIC.md             — judge instructions (written once)

Usage:
  python build_dream_audit.py                # fetch fresh from Mando, then build
  python build_dream_audit.py --no-fetch     # rebuild from local caches only
  python build_dream_audit.py --k 15 --bundle-size 25

Pure stdlib. HTTP/DB access is read-only.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
WS = "default"
OBS = "john-cc"
SEED = 1234  # deterministic blinding/shuffle so re-runs (and resume) are stable

EXPLICIT_CACHE = HERE / "john_cc_explicit.json"
CONTENT_CACHE = HERE / "dream_phase2_content.jsonl"
BUNDLE_DIR = HERE / "dream_audit_bundles"
KEY_FILE = HERE / "dream_audit_key.json"
RUBRIC_FILE = HERE / "DREAM_GRADING_RUBRIC.md"

STOP = set(
    "the a an and or but if then else of to in on at for with by from as is are was "
    "were be been being it its this that these those he she they them his her their i "
    "you your we our us me my mine not no do does did has have had will would can could "
    "should about into over under than so such not also more most some any all".split()
)


# --------------------------------------------------------------------------- fetch
def psql_json(sql: str):
    sql = " ".join(sql.split())  # one line: psql treats \n-prefixed tokens as meta-cmds
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


def fetch_explicit():
    rows = psql_json(f"""
        SELECT COALESCE(json_agg(json_build_object('id', id, 'text', content)), '[]')
        FROM documents
        WHERE workspace_name='{WS}' AND observer='{OBS}' AND observed='{OBS}'
          AND level='explicit' AND deleted_at IS NULL;
    """)
    EXPLICIT_CACHE.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"fetched {len(rows)} explicit conclusions -> {EXPLICIT_CACHE.name}")
    return rows


def fetch_content():
    out = subprocess.run(
        ["ssh", "mando", "cat ~/honcho/dream_phase2_content.jsonl"],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise SystemExit(f"content fetch failed: {out.stderr[:500]}")
    CONTENT_CACHE.write_text(out.stdout, encoding="utf-8")
    n = sum(1 for _ in out.stdout.splitlines() if _.strip())
    print(f"fetched {n} content lines -> {CONTENT_CACHE.name}")


# ------------------------------------------------------------------- retrieval (IDF)
def toks(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower())
            if len(t) >= 3 and t not in STOP]


class Retriever:
    """Lightweight IDF bag-of-words retriever over the explicit corpus."""

    def __init__(self, corpus: list[dict]):
        self.corpus = corpus
        self.doc_toks = [set(toks(c["text"])) for c in corpus]
        df = Counter()
        for ts in self.doc_toks:
            df.update(ts)
        n = max(1, len(corpus))
        self.idf = {t: math.log(n / (1 + dfc)) + 1.0 for t, dfc in df.items()}

    def top(self, claim: str, k: int) -> list[dict]:
        q = set(toks(claim))
        if not q:
            return []
        scored = []
        for c, ts in zip(self.corpus, self.doc_toks):
            shared = q & ts
            if not shared:
                continue
            score = sum(self.idf.get(t, 1.0) for t in shared)
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:k]]


# ------------------------------------------------------------------------- build
def load_items() -> list[dict]:
    """Flatten the content file into one record per dreamer item, deduped per
    (model, type, text) so identical conclusions across trials are graded once
    but reliability of *production* is still attributable per model."""
    seen: set[tuple[str, str, str]] = set()
    items: list[dict] = []
    for line in CONTENT_CACHE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        model, trial = rec["model"], rec["trial"]
        data = rec.get("data") or {}
        for typ in ("deductive", "inductive"):
            for txt in (data.get(typ) or []):
                if not txt:
                    continue
                key = (model, typ, txt.strip())
                if key in seen:
                    continue
                seen.add(key)
                items.append({"model": model, "trial": trial, "type": typ, "claim": txt})
        card = data.get("card")
        if card:
            key = (model, "card", card.strip())
            if key not in seen:
                seen.add(key)
                items.append({"model": model, "trial": trial, "type": "card", "claim": card})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="use local caches, skip ssh")
    ap.add_argument("--k", type=int, default=15, help="evidence conclusions per item")
    ap.add_argument("--bundle-size", type=int, default=25, help="items per judge bundle")
    args = ap.parse_args()

    if not args.no_fetch:
        fetch_explicit()
        fetch_content()
    if not EXPLICIT_CACHE.exists() or not CONTENT_CACHE.exists():
        raise SystemExit("missing caches; run without --no-fetch first")

    corpus = json.loads(EXPLICIT_CACHE.read_text(encoding="utf-8"))
    retr = Retriever(corpus)
    items = load_items()
    if not items:
        raise SystemExit("no dreamer items found in content file (sweep not far enough?)")

    rng = random.Random(SEED)
    rng.shuffle(items)

    key: dict[str, dict] = {}
    blind: list[dict] = []
    for i, it in enumerate(items):
        oid = f"d{i:04d}"
        key[oid] = {"model": it["model"], "trial": it["trial"], "type": it["type"]}
        ev = retr.top(it["claim"], args.k)
        blind.append({
            "id": oid,
            "type": it["type"],
            "claim": it["claim"],
            "evidence": [{"id": e["id"], "text": e["text"]} for e in ev],
        })

    BUNDLE_DIR.mkdir(exist_ok=True)
    for f in BUNDLE_DIR.glob("bundle_*.json"):
        f.unlink()
    nb = 0
    for start in range(0, len(blind), args.bundle_size):
        chunk = blind[start:start + args.bundle_size]
        (BUNDLE_DIR / f"bundle_{nb:02d}.json").write_text(
            json.dumps({"items": chunk}, indent=2), encoding="utf-8")
        nb += 1

    KEY_FILE.write_text(json.dumps(key, indent=2), encoding="utf-8")
    write_rubric()

    per_model = defaultdict(Counter)
    for v in key.values():
        per_model[v["model"]][v["type"]] += 1
    print(f"\nitems={len(blind)} (deduped per model+type+text)  bundles={nb}  k={args.k}")
    for m in sorted(per_model):
        c = per_model[m]
        print(f"  {m:14}  ded={c['deductive']:3}  ind={c['inductive']:3}  card={c['card']}")
    print(f"\nwrote {BUNDLE_DIR.name}/ , {KEY_FILE.name} (PRIVATE), {RUBRIC_FILE.name}")


def write_rubric():
    RUBRIC_FILE.write_text(RUBRIC, encoding="utf-8")


RUBRIC = """# Dreamer conclusion grading rubric (blind)

You are grading conclusions a memory system inferred about a person ("the
subject"). You are **blind** to which model produced each item. For each item
you get a `claim`, its `type` (deductive / inductive / card), and `evidence` —
a set of explicit, directly-observed conclusions about the same subject. The
evidence is the *only* ground truth you may use.

For **each** item, output one object:

```json
{"id": "d0007", "grade": "GROUNDED", "leakage": false, "specificity": "HIGH", "why": "<cite evidence ids verbatim>"}
```

## grade  (grounding of the claim in the evidence)
- **GROUNDED** — the claim follows from the evidence. For `deductive`: a valid
  logical inference from one or more evidence items. For `inductive`: a fair
  generalization the evidence supports. For `card`: every assertion is backed.
- **PARTIAL** — partly supported; one clause is grounded but another is a
  stretch, or the claim overreaches the evidence's strength.
- **UNSUPPORTED** — not derivable from the evidence (fabricated, contradicted,
  or a leap with no basis).

## leakage  (boolean)
`true` if the claim attributes to the subject something that actually belongs to
the *assistant/other party* — e.g. describing tool output, skill-injection text,
or the assistant's reasoning as if it were the subject's own trait or action.
This is the known over-attribution failure mode; flag it whenever you see it.

## specificity  ("HIGH" | "MED" | "LOW" | null)
Only for `inductive` and `card` items (use null for `deductive`). Is the
generalization *useful and specific* ("prefers PowerShell for Windows infra
automation") or vague boilerplate that would be true of almost anyone
("works with computers", "communicates with others")? HIGH = specific+actionable,
LOW = generic filler.

## why
One sentence. Cite the evidence ids you relied on (e.g. "supported by abc123,
def456") or state what's missing. Keep it terse and factual.

## Output
Return a single JSON object mapping the bundle filename you graded to its items:
```json
{"bundle_03": {"items": [ {grade obj}, {grade obj}, ... ]}}
```
Grade **every** item in the bundle. Do not invent ids. Do not guess the model.
"""


if __name__ == "__main__":
    main()
