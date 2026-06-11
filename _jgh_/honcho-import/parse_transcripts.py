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

# User strings that are harness noise, not real prompts.
NOISE_PREFIXES = (
    "<command-name>",
    "<local-command-stdout>",
    "Caveat:",
    "[Request interrupted",
    "<bash-",
)
NOISE_RE = re.compile(r"</?(command-name|command-message|command-args|local-command)")


def clean_text(s: str) -> str:
    return s.strip()


def is_noise(s: str) -> bool:
    st = s.strip()
    if not st:
        return True
    if st.startswith(NOISE_PREFIXES):
        return True
    # Pure command/system-reminder wrappers with no prose.
    if NOISE_RE.search(st) and len(st) < 400:
        return True
    return False


def extract_user(content) -> str | None:
    if isinstance(content, str):
        return None if is_noise(content) else clean_text(content)
    # list form -> almost always tool_result; not a real user turn
    texts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
    if not texts:
        return None
    joined = "\n".join(texts)
    return None if is_noise(joined) else clean_text(joined)


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
