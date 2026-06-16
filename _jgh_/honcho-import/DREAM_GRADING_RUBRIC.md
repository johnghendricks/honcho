# Dreamer conclusion grading rubric (blind)

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
