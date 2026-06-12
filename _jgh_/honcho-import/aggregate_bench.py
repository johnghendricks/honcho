"""Aggregate blind-judge grade files for ONE benchmark workspace into the
grounding scorecard. Reads grades_<ws>_*.json, validates every conclusion in
audit_payload_<ws>.json was graded exactly once, prints grounded/partial/over/
hallucinated rates + leakage count, and writes bench_score_<ws>.json.

Usage:  python aggregate_bench.py --workspace default
"""

import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
GRADES = ("GROUNDED", "PARTIAL", "OVER", "HALLUCINATED")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    ws = args.workspace

    payload = json.loads((HERE / f"audit_payload_{ws}.json").read_text(encoding="utf-8"))
    expected = {b["orig"]: {c["id"] for c in b["conclusions"]} for b in payload}

    counts = Counter()
    leakage = 0
    seen: dict[str, set] = {}
    issues = []
    for gf in sorted(HERE.glob(f"grades_{ws}_*.json")):
        data = json.loads(gf.read_text(encoding="utf-8"))
        orig = data["orig"]
        s = seen.setdefault(orig, set())
        for it in data["items"]:
            g = it["grade"].upper()
            if g not in GRADES:
                issues.append(f"{gf.name}: bad grade {g!r} on {it['id']}")
                continue
            counts[g] += 1
            if it.get("leakage"):
                leakage += 1
            s.add(it["id"])

    for orig, exp in expected.items():
        got = seen.get(orig, set())
        if got - exp:
            issues.append(f"{orig}: {len(got - exp)} unknown ids graded")
        if exp - got:
            issues.append(f"{orig}: {len(exp - got)} ungraded ids")

    n = sum(counts[g] for g in GRADES)
    grounded = counts["GROUNDED"]
    over = counts["OVER"] + counts["HALLUCINATED"]
    rates = {
        "n": n,
        "grounded_pct": round(100 * grounded / n, 1) if n else 0,
        "partial_pct": round(100 * counts["PARTIAL"] / n, 1) if n else 0,
        "over_pct": round(100 * over / n, 1) if n else 0,
        "leakage": leakage,
    }

    print("=" * 60)
    print(f"GROUNDING SCORE — workspace={ws}  (fixed 8-session bench set)")
    print("=" * 60)
    print(f"  n graded     = {n}")
    print(f"  GROUNDED     = {counts['GROUNDED']:>3}  ({rates['grounded_pct']}%)")
    print(f"  PARTIAL      = {counts['PARTIAL']:>3}  ({rates['partial_pct']}%)")
    print(f"  OVER         = {counts['OVER']:>3}")
    print(f"  HALLUCINATED = {counts['HALLUCINATED']:>3}")
    print(f"  OVER+HALL    = {over:>3}  ({rates['over_pct']}%)")
    print(f"  leakage      = {leakage}   (target 0)")
    print(f"  -> grounded {rates['grounded_pct']}% vs target >=80%  "
          f"[{'PASS' if rates['grounded_pct'] >= 80 else 'BELOW'}]")
    if issues:
        print("\n[!] DATA ISSUES:")
        for i in issues:
            print("  -", i)
    else:
        print("\n[ok] all conclusions graded exactly once, no bad grades")

    (HERE / f"bench_score_{ws}.json").write_text(json.dumps(
        {"workspace": ws, "counts": dict(counts), "rates": rates,
         "issues": issues}, indent=2))
    print(f"\nwrote bench_score_{ws}.json")


if __name__ == "__main__":
    main()
