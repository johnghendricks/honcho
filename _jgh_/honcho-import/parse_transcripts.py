"""Parse Claude Code transcripts + memory files into a Honcho-import payload.

Reusable across projects. Emits a single JSON file describing sessions/messages
and memory-derived conclusions, ready to be loaded into Honcho.

Usage:
    python parse_transcripts.py <project_transcript_dir> <out.json>

The <project_transcript_dir> is a folder under ~/.claude/projects/, e.g.
    ".../.claude/projects/D--Git-Open-Source-honcho"
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Harness / injection noise.
#
# Claude Code transcripts splice four kinds of content into "user" turns that
# John did NOT author. Keeping them was the cause of the john-cc over-attribution
# (audit: ~half of conclusions not grounded in John; skill-injection ~60% of it):
#   1. Slash-command SKILL EXPANSIONS — a "user" turn whose body is the skill's
#      own definition (begins "Base directory for this skill:"). Dropped here;
#      John's real intent is recovered from the paired <command-name>/
#      <command-args> turn via parse_command().
#   2. <system-reminder> / <task-notification> blocks — harness + background-job
#      injections. Stripped (spans removed, any surrounding prose kept).
#   3. local-command caveats/stdout, bash wrappers, tool results — dropped.
#   4. AUTO-COMPACT / CONTINUATION SUMMARIES — when a session is resumed, CC
#      injects a generated recap of the PRIOR (mostly assistant) work as a "user"
#      turn (begins "This session is being continued..."). The deriver read its
#      accomplishment prose as John's own actions (grounding bench: the single
#      biggest residual after the skill-injection fix — 2 such blocks drove ~10
#      OVER + 11 leakage in one session). The summary + the injected continuation-
#      prompt boilerplate are stripped; only John's appended resume message — the
#      text after the boilerplate's final sentence — is kept.

NOISE_PREFIXES = (
    "<local-command-stdout>",
    "<local-command-caveat>",
    "Caveat:",
    "[Request interrupted",
    "<bash-",
)

# A "user" turn whose body is an injected skill definition, not John's words.
SKILL_BODY_PREFIX = "Base directory for this skill:"

# A "user" turn that opens with CC's auto-compact / continuation recap.
CONTINUATION_PREFIX = (
    "This session is being continued from a previous conversation that ran out of "
    "context"
)
# The injected continuation prompt closes with one of these sentences; everything
# up to and including it is harness/auto-generated. John's typed resume message
# (if any) is whatever follows the LAST occurrence. (Corpus 2026-06-12: the first
# anchor covers 34/34 continuation turns; the others generalize to stock CC.)
CONTINUATION_END_ANCHORS = (
    "Pick up the last task as if the break never happened.",
    "Continue with the last task that you were asked to work on.",
    "Please continue the conversation from where we left it off "
    "without asking the user any further questions.",
)


def strip_continuation_summary(s: str) -> str | None:
    """Reduce a CC continuation-summary turn to just John's appended message.

    Returns `s` unchanged if it is not a continuation summary. If it is, returns
    only the text following the injected continuation-prompt boilerplate, or None
    when there is no recoverable John message (the whole turn was auto-generated).
    """
    if not s.lstrip().startswith(CONTINUATION_PREFIX):
        return s
    cut = -1
    for anchor in CONTINUATION_END_ANCHORS:
        i = s.rfind(anchor)
        if i != -1:
            cut = max(cut, i + len(anchor))
    if cut == -1:
        return None  # pure auto-summary, nothing John authored to keep
    return s[cut:].strip() or None

# Recover a slash-command invocation as a compact "/cmd args" line.
_CMD_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S | re.I)
_CMD_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S | re.I)

# Injected spans removed in place (genuine prose around them is preserved).
_INJECTED_SPAN_RE = re.compile(
    r"<(system-reminder|task-notification)>.*?</\1>", re.S | re.I
)


def clean_text(s: str) -> str:
    return s.strip()


def parse_command(s: str) -> str | None:
    """Render a slash-command turn as a compact "/cmd args" line.

    The args carry John's actual intent (path, instruction). A command with no
    args (bare /clear, /compact, ...) is session-management noise -> drop.
    Returns None if `s` is not a command invocation.
    """
    name = _CMD_NAME_RE.search(s)
    if not name:
        return None
    cmd = name.group(1).strip()
    args_m = _CMD_ARGS_RE.search(s)
    args = args_m.group(1).strip() if args_m else ""
    return f"{cmd} {args}".strip() if args else None


def is_noise(s: str) -> bool:
    st = s.strip()
    if not st:
        return True
    return st.startswith(NOISE_PREFIXES)


def extract_user(content) -> str | None:
    if isinstance(content, str):
        s = content
    else:
        # list form -> tool_result or injected blocks; keep only text parts
        texts = [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if not texts:
            return None
        s = "\n".join(texts)

    st = s.strip()
    if not st:
        return None
    # 1) Injected skill-definition body posing as a user turn -> drop entirely.
    if st.startswith(SKILL_BODY_PREFIX):
        return None
    # 2) Auto-compact/continuation recap -> keep only John's appended resume
    #    message; drop the generated summary + continuation boilerplate.
    if st.startswith(CONTINUATION_PREFIX):
        recovered = strip_continuation_summary(st)
        if not recovered:
            return None
        st = recovered
    # 3) Slash-command invocation -> recover John's intent as "/cmd args".
    if "<command-name>" in st:
        return parse_command(st)
    # 4) Strip injected <system-reminder>/<task-notification> spans, keep prose.
    st = _INJECTED_SPAN_RE.sub("", st).strip()
    if not st or is_noise(st):
        return None
    return clean_text(st)


def extract_assistant(content) -> str | None:
    if isinstance(content, str):
        return clean_text(content) or None
    texts, tools = [], []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            texts.append(b["text"].strip())
        elif t == "tool_use":
            tools.append(b.get("name", "tool"))
    parts = []
    if texts:
        parts.append("\n".join(texts))
    if tools:
        # de-dup preserving order
        seen = {}
        ordered = [seen.setdefault(x, x) for x in tools if x not in seen]
        parts.append(f"[tools: {', '.join(ordered)}]")
    return "\n".join(parts) if parts else None


# Honcho rejects message content over MAX_MESSAGE_SIZE (25000). Split with margin,
# preferring a newline boundary near the cut so turns stay readable.
MAX_MSG_CHARS = 24000


def split_oversized(msgs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in msgs:
        c = m["content"]
        if len(c) <= MAX_MSG_CHARS:
            out.append(m)
            continue
        start = 0
        while start < len(c):
            end = start + MAX_MSG_CHARS
            if end < len(c):
                nl = c.rfind("\n", start + MAX_MSG_CHARS // 2, end)
                if nl != -1:
                    end = nl
            out.append({"role": m["role"], "content": c[start:end], "ts": m.get("ts")})
            start = end
    return out


def merge_consecutive(msgs: list[dict]) -> list[dict]:
    """Collapse runs of same-role messages into one turn.

    A single agentic response fragments across many transcript lines
    (text, tool_use, text...). Merging restores one assistant turn per
    user prompt, which is far higher signal for a memory store.
    """
    merged: list[dict] = []
    for m in msgs:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n" + m["content"]
        else:
            merged.append(dict(m))
    return merged


def parse_transcript(path: Path) -> list[dict]:
    msgs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        if d.get("isSidechain"):  # skip subagent side-conversations
            continue
        m = d.get("message", {})
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            text = extract_user(content)
        elif role == "assistant":
            text = extract_assistant(content)
        else:
            continue
        if not text:
            continue
        msgs.append({"role": role, "content": text, "ts": d.get("timestamp")})
    return split_oversized(merge_consecutive(msgs))


# Conclusions are embedded; bge-large caps at 512 tokens. Pack paragraphs
# greedily under a conservative char budget (~3.4 chars/token observed -> aim ~450 tok).
CHUNK_CHARS = 1450


def chunk_body(body: str, prefix: str) -> list[str]:
    """Split a memory body into <512-token conclusions, greedily by paragraph."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        # A single oversized paragraph: hard-split on lines.
        if len(p) > CHUNK_CHARS:
            if cur:
                chunks.append(cur); cur = ""
            for line in p.splitlines():
                if len(cur) + len(line) + 1 > CHUNK_CHARS and cur:
                    chunks.append(cur); cur = ""
                cur += (("\n" if cur else "") + line)
            continue
        if len(cur) + len(p) + 2 > CHUNK_CHARS and cur:
            chunks.append(cur); cur = ""
        cur += (("\n\n" if cur else "") + p)
    if cur:
        chunks.append(cur)
    # Safety net: force-split any chunk still over budget (e.g. one giant
    # newline-free line) on a hard char boundary so it never trips the
    # 512-token embedding limit.
    safe: list[str] = []
    for c in chunks:
        if len(c) <= CHUNK_CHARS:
            safe.append(c)
        else:
            for j in range(0, len(c), CHUNK_CHARS):
                safe.append(c[j:j + CHUNK_CHARS])
    chunks = safe
    # Prefix multi-chunk facts so each remains self-describing.
    if len(chunks) > 1:
        return [f"[{prefix}] {c}" for c in chunks]
    return chunks


def parse_memory(mem_dir: Path) -> list[str]:
    out = []
    if not mem_dir.is_dir():
        return out
    for f in sorted(mem_dir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        # strip frontmatter
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) == 3:
                body = parts[2]
        body = body.strip()
        if body:
            out.extend(chunk_body(body, f.stem))
    return out


def main():
    proj = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    sessions = []
    for jf in sorted(proj.glob("*.jsonl")):
        msgs = parse_transcript(jf)
        if not msgs:
            continue
        sessions.append({"session_id": jf.stem, "messages": msgs})
    conclusions = parse_memory(proj / "memory")
    payload = {
        "project": proj.name,
        "sessions": sessions,
        "conclusions": conclusions,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tot = sum(len(s["messages"]) for s in sessions)
    print(f"project={proj.name}")
    print(f"sessions={len(sessions)} messages={tot} conclusions={len(conclusions)}")
    for s in sessions:
        roles = {}
        for m in s["messages"]:
            roles[m["role"]] = roles.get(m["role"], 0) + 1
        print(f"  {s['session_id']}: {len(s['messages'])} msgs {roles}")


if __name__ == "__main__":
    main()
