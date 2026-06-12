"""Aggregate the 6 judge group files into the A/B/C grounding comparison.

Reads group_1..6.json (each {bundle_basename: {items:[{id,grade,why}]}}), maps
each bundle to its arm via the basename suffix (_a/_b/_c), recomputes counts from
the per-item grades, validates every graded conclusion exists in the bundle, and
prints per-arm grounded vs over-interpretation rates + the verdict.
"""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
BD = HERE / "audit_bundles"
GRADES = ("GROUNDED", "PARTIAL", "OVER", "HALLUCINATED")

# Expected conclusion ids per bundle (ground truth from the bundle files).
expected = {}
for f in BD.glob("bundle_*.json"):
    b = json.loads(f.read_text(encoding="utf-8"))
    expected[f.stem] = {c["id"] for c in b["conclusions"]}

per_arm = {a: Counter() for a in ("a", "b", "c")}
issues = []

# Collect best (most-complete) grading per bundle across all group files.
best: dict[str, dict] = {}
for g in sorted(BD.glob("group_*.json")):
    data = json.loads(g.read_text(encoding="utf-8"))
    for bundle, v in data.items():
        if bundle not in best or len(v["items"]) > len(best[bundle]["items"]):
            best[bundle] = v
graded_bundles = set(best)

for bundle, v in best.items():
    if True:
        arm = bundle.rsplit("_", 1)[1]
        ids_seen = set()
        for it in v["items"]:
            grade = it["grade"].upper()
            if grade not in GRADES:
                issues.append(f"{bundle}: bad grade {grade!r} on {it['id']}")
                continue
            per_arm[arm][grade] += 1
            ids_seen.add(it["id"])
        exp = expected.get(bundle, set())
        missing = exp - ids_seen
        extra = ids_seen - exp
        if missing:
            issues.append(f"{bundle}: {len(missing)} ungraded ids")
        if extra:
            issues.append(f"{bundle}: {len(extra)} unknown ids graded")

missing_bundles = set(expected) - graded_bundles
if missing_bundles:
    issues.append(f"bundles never graded: {sorted(missing_bundles)}")

ARM_NAME = {"a": "A john+claude verbatim", "b": "B john solo",
            "c": "C john+claude, [tools] stripped"}
print("=" * 72)
print("GROUNDING AUDIT — assistant-context ablation (8 matched sessions/arm)")
print("=" * 72)
hdr = f"{'arm':36} {'n':>4} {'GROUND':>7} {'PART':>5} {'OVER':>5} {'HALL':>5}"
print(hdr)
rates = {}
for a in ("a", "b", "c"):
    c = per_arm[a]
    n = sum(c[g] for g in GRADES)
    over = c["OVER"] + c["HALLUCINATED"]          # not grounded in user's own words
    grounded = c["GROUNDED"]
    rates[a] = {"n": n, "grounded_pct": 100 * grounded / n if n else 0,
                "over_pct": 100 * over / n if n else 0,
                "partial_pct": 100 * c["PARTIAL"] / n if n else 0}
    print(f"{ARM_NAME[a]:36} {n:>4} {c['GROUNDED']:>7} {c['PARTIAL']:>5} "
          f"{c['OVER']:>5} {c['HALLUCINATED']:>5}")

print("\nrates (% of graded conclusions):")
print(f"{'arm':36} {'GROUNDED':>9} {'PARTIAL':>8} {'OVER+HALL':>10}")
for a in ("a", "b", "c"):
    r = rates[a]
    print(f"{ARM_NAME[a]:36} {r['grounded_pct']:>8.1f}% {r['partial_pct']:>7.1f}% "
          f"{r['over_pct']:>9.1f}%")

dA, dB, dC = rates["a"]["over_pct"], rates["b"]["over_pct"], rates["c"]["over_pct"]
print("\nover-interpretation (OVER+HALLUCINATED):")
print(f"  A (paired)        = {dA:.1f}%")
print(f"  B (solo)          = {dB:.1f}%")
print(f"  C (paired, no [tools]) = {dC:.1f}%")
print(f"  A - B = {dA - dB:+.1f} pts   (assistant-context effect)")
print(f"  A - C = {dA - dC:+.1f} pts   ([tools:] marker effect)")

if issues:
    print("\n[!] DATA ISSUES:")
    for i in issues:
        print("  -", i)
else:
    print("\n[ok] all bundles graded, all ids covered, no bad grades")

(HERE / "audit_final.json").write_text(json.dumps(
    {"per_arm": {a: dict(per_arm[a]) for a in per_arm}, "rates": rates,
     "issues": issues}, indent=2))
print("\nwrote audit_final.json")
