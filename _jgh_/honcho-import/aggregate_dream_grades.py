"""Aggregate blind Dreamer grades back into a per-model quality comparison.

Reads:
  dream_audit_key.json         — PRIVATE {opaque_id: {model, trial, type}}
  dream_grades_*.json          — judge output. Each file maps a bundle name to
                                 {"items": [{id, grade, leakage, specificity, why}]}
                                 (the top-level bundle key is ignored; items carry ids).

Un-blinds via the key, then rolls up per (model, type):
  - grounding: GROUNDED / PARTIAL / UNSUPPORTED counts + grounded%
  - leakage rate (% of items flagged as over-attribution)
  - specificity distribution (inductive + card only)

Validates every graded id exists in the key and warns on ungraded / duplicate /
conflicting grades. Writes dream_audit_final.json.

Usage:  python aggregate_dream_grades.py
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
KEY_FILE = HERE / "dream_audit_key.json"
GRADES = ("GROUNDED", "PARTIAL", "UNSUPPORTED")
SPEC = ("HIGH", "MED", "LOW")


def main():
    if not KEY_FILE.exists():
        raise SystemExit("missing dream_audit_key.json — run build_dream_audit.py first")
    key = json.loads(KEY_FILE.read_text(encoding="utf-8"))

    graded: dict[str, dict] = {}
    issues: list[str] = []
    files = sorted(HERE.glob("dream_grades_*.json"))
    if not files:
        raise SystemExit("no dream_grades_*.json found — grade the bundles first")

    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        # tolerate both {bundle: {items:[...]}} and a bare {items:[...]} shape
        blocks = data.values() if not data.get("items") else [data]
        for block in blocks:
            for it in (block.get("items") or []):
                oid = it.get("id")
                if oid not in key:
                    issues.append(f"{f.name}: unknown id {oid!r}")
                    continue
                if oid in graded and graded[oid]["grade"] != it.get("grade"):
                    issues.append(f"conflicting grade on {oid}: "
                                  f"{graded[oid]['grade']} vs {it.get('grade')}")
                graded[oid] = it

    missing = set(key) - set(graded)
    if missing:
        issues.append(f"{len(missing)} ids never graded")

    # per (model, type) rollup
    g = defaultdict(Counter)          # grade counts
    leak = defaultdict(lambda: [0, 0])  # [flagged, total]
    spec = defaultdict(Counter)        # specificity counts
    for oid, info in key.items():
        if oid not in graded:
            continue
        m, typ = info["model"], info["type"]
        it = graded[oid]
        grade = str(it.get("grade", "")).upper()
        if grade not in GRADES:
            issues.append(f"{oid}: bad grade {grade!r}")
            continue
        g[(m, typ)][grade] += 1
        leak[(m, typ)][1] += 1
        if it.get("leakage") is True:
            leak[(m, typ)][0] += 1
        s = str(it.get("specificity") or "").upper()
        if s in SPEC:
            spec[(m, typ)][s] += 1

    models = sorted({m for m, _ in g})
    print("=" * 92)
    print("DREAMER PHASE-2 BLIND QUALITY AUDIT  (grounding vs explicit-conclusion corpus)")
    print("=" * 92)
    hdr = (f"{'model':14} {'type':10} {'n':>4} {'GRND':>5} {'PART':>5} {'UNSUP':>6} "
           f"{'grnd%':>6} {'leak%':>6} {'spec H/M/L':>12}")
    print(hdr)
    final = {}
    for m in models:
        for typ in ("deductive", "inductive", "card"):
            c = g.get((m, typ))
            if not c:
                continue
            n = sum(c[x] for x in GRADES)
            grnd_pct = 100 * c["GROUNDED"] / n if n else 0
            lf, lt = leak[(m, typ)]
            leak_pct = 100 * lf / lt if lt else 0
            sc = spec.get((m, typ), Counter())
            spec_str = f"{sc['HIGH']}/{sc['MED']}/{sc['LOW']}" if typ != "deductive" else "-"
            print(f"{m:14} {typ:10} {n:>4} {c['GROUNDED']:>5} {c['PARTIAL']:>5} "
                  f"{c['UNSUPPORTED']:>6} {grnd_pct:>5.0f}% {leak_pct:>5.0f}% {spec_str:>12}")
            final[f"{m}|{typ}"] = {
                "n": n, "grounded": c["GROUNDED"], "partial": c["PARTIAL"],
                "unsupported": c["UNSUPPORTED"], "grounded_pct": round(grnd_pct, 1),
                "leakage_pct": round(leak_pct, 1), "specificity": dict(sc),
            }
        print()

    if issues:
        print("[!] DATA ISSUES:")
        for i in issues[:40]:
            print("  -", i)
        if len(issues) > 40:
            print(f"  ... +{len(issues) - 40} more")
    else:
        print("[ok] all graded ids known, no bad grades")

    (HERE / "dream_audit_final.json").write_text(
        json.dumps({"per_model_type": final, "issues": issues}, indent=2),
        encoding="utf-8")
    print("\nwrote dream_audit_final.json")


if __name__ == "__main__":
    main()
