"""Parse-only volume scan of a project (no API calls). Reports transcript +
memory volume and parse wall-time, to project ingestion cost for big projects.

Usage:
    python scan_project.py <project_dir_name>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_transcripts import parse_transcript, chunk_body  # noqa: E402

ROOT = Path(r"C:\Users\John Hendricks\.claude\projects")
CHARS_PER_TOKEN = 3.4


def main():
    proj = ROOT / sys.argv[1]
    jfiles = sorted(proj.glob("*.jsonl"))

    t0 = time.perf_counter()
    n_sessions = n_msgs = n_user = n_asst = total_chars = 0
    empty = 0
    biggest = (None, 0)
    for jf in jfiles:
        msgs = parse_transcript(jf)
        if not msgs:
            empty += 1
            continue
        n_sessions += 1
        n_msgs += len(msgs)
        for m in msgs:
            total_chars += len(m["content"])
            if m["role"] == "user":
                n_user += 1
            else:
                n_asst += 1
        if len(msgs) > biggest[1]:
            biggest = (jf.name, len(msgs))
    parse_secs = time.perf_counter() - t0

    # Memory side
    memdir = proj / "memory"
    mem_files = [f for f in memdir.glob("*.md") if f.name != "MEMORY.md"] if memdir.is_dir() else []
    mem_chunks = mem_body_chars = 0
    for f in mem_files:
        raw = f.read_text(encoding="utf-8", errors="replace")
        body = raw.split("---", 2)[2].strip() if raw.startswith("---") else raw.strip()
        if body:
            mem_body_chars += len(body)
            mem_chunks += len(chunk_body(body, f.stem))

    print(f"== {proj.name} ==")
    print(f"transcript files on disk: {len(jfiles)} (empty after parse: {empty})")
    print(f"sessions w/ content:      {n_sessions}")
    print(f"messages (merged turns):  {n_msgs}  (user={n_user} asst={n_asst})")
    print(f"message content chars:    {total_chars:,}  (~{round(total_chars/CHARS_PER_TOKEN):,} tok)")
    print(f"avg msgs/session:         {round(n_msgs/n_sessions,1) if n_sessions else 0}")
    print(f"avg chars/msg:            {round(total_chars/n_msgs) if n_msgs else 0}")
    print(f"biggest session:          {biggest[0]} ({biggest[1]} msgs)")
    print(f"parse wall-time:          {parse_secs:.1f}s  ({round(len(jfiles)/parse_secs,1) if parse_secs else 0} files/s)")
    print(f"--- memory ---")
    print(f"memory files:             {len(mem_files)}")
    print(f"memory body chars:        {mem_body_chars:,}")
    print(f"memory -> conclusions:    {mem_chunks}  ({round(mem_chunks/len(mem_files),2) if mem_files else 0} chunks/file)")
    print(f"est message vector space: {n_msgs*1024*4/1024/1024:.1f} MB (+ content {total_chars/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
