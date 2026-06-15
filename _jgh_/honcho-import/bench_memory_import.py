"""Benchmark harness: import the memory-only Claude Code projects into Honcho
as conclusions, timing each project and collecting per-file stats.

Conclusion creation embeds SYNCHRONOUSLY (src/crud/document.py:800), so each
POST's wall-clock time IS the Ollama bge-large embedding cost on Mando. This
lets us model the time/space to ingest the big kb-proto-1 project later.

Writes bench_results.json (machine-readable) and prints a summary table.

Usage:
    python bench_memory_import.py [--dry-run]
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_transcripts import chunk_body  # noqa: E402

PROJECTS_ROOT = Path(r"C:\Users\John Hendricks\.claude\projects")
BASE = "http://192.168.0.140:8000"
WS = "default"
OBSERVER = "john-cc"
OBSERVED = "john-cc"
CHARS_PER_TOKEN = 3.4  # observed: 1757 chars -> 514 bge tokens
DRY = "--dry-run" in sys.argv


def post(path: str, body: dict) -> tuple[dict | list, float]:
    url = f"{BASE}/v3{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        payload = json.loads(r.read() or "{}")
    return payload, time.perf_counter() - t0


def ensure_peer(peer_id: str):
    if DRY:
        return
    try:
        post(f"/workspaces/{WS}/peers",
             {"id": peer_id, "configuration": {"observe_me": False}})
    except urllib.error.HTTPError as e:
        raise SystemExit(f"peer create failed: {e.read().decode()[:300]}")


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    meta = {"name": None, "type": None, "description": None}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            fm, body = parts[1], parts[2]
            for line in fm.splitlines():
                m = re.match(r"\s*(name|type|description):\s*(.+)", line)
                if m:
                    meta[m.group(1)] = m.group(2).strip()
    return meta, body.strip()


def memory_only_projects() -> list[Path]:
    out = []
    for d in sorted(PROJECTS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        if list(d.glob("*.jsonl")):
            continue
        memdir = d / "memory"
        if memdir.is_dir() and any(
            f.name != "MEMORY.md" for f in memdir.glob("*.md")
        ):
            out.append(d)
    return out


def process_project(proj: Path) -> dict:
    memdir = proj / "memory"
    files_stat = []
    conclusions = []
    for f in sorted(memdir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(raw)
        if not body:
            continue
        chunks = chunk_body(body, f.stem)
        files_stat.append({
            "file": f.name,
            "name": meta["name"],
            "type": meta["type"],
            "raw_chars": len(raw),
            "body_chars": len(body),
            "est_tokens": round(len(body) / CHARS_PER_TOKEN),
            "n_chunks": len(chunks),
        })
        conclusions.extend(chunks)

    body_chars = sum(fs["body_chars"] for fs in files_stat)
    chunk_chars = [len(c) for c in conclusions]

    # Time the load (batches of <=100). Embeds synchronously on Mando.
    load_secs = 0.0
    if not DRY and conclusions:
        for i in range(0, len(conclusions), 100):
            batch = conclusions[i:i + 100]
            body_req = {"conclusions": [
                {"content": c, "observer_id": OBSERVER, "observed_id": OBSERVED}
                for c in batch
            ]}
            try:
                _, secs = post(f"/workspaces/{WS}/conclusions", body_req)
            except urllib.error.HTTPError as e:
                raise SystemExit(
                    f"{proj.name}: conclusion POST failed: {e.read().decode()[:300]}")
            load_secs += secs

    n = len(conclusions)
    return {
        "project": proj.name,
        "n_files": len(files_stat),
        "n_conclusions": n,
        "body_chars": body_chars,
        "est_body_tokens": round(body_chars / CHARS_PER_TOKEN),
        "chunk_chars_min": min(chunk_chars) if chunk_chars else 0,
        "chunk_chars_max": max(chunk_chars) if chunk_chars else 0,
        "chunk_chars_avg": round(sum(chunk_chars) / n) if n else 0,
        "n_files_split": sum(1 for fs in files_stat if fs["n_chunks"] > 1),
        "load_secs": round(load_secs, 3),
        "secs_per_conclusion": round(load_secs / n, 4) if n else 0,
        "conclusions_per_sec": round(n / load_secs, 2) if load_secs else 0,
        "chars_per_sec": round(body_chars / load_secs) if load_secs else 0,
        # Storage estimate: 1024-dim float32 vector + content bytes (HNSW edge
        # overhead excluded; measure true size with pg_total_relation_size on Mando).
        "est_vector_bytes": n * 1024 * 4,
        "est_content_bytes": sum(len(c.encode("utf-8")) for c in conclusions),
        "types": sorted({fs["type"] for fs in files_stat if fs["type"]}),
        "files": files_stat,
    }


def main():
    projects = memory_only_projects()
    print(f"== memory-only import benchmark: {len(projects)} projects (dry={DRY}) ==")
    ensure_peer(OBSERVER)

    results = []
    for proj in projects:
        r = process_project(proj)
        results.append(r)
        print(f"  {r['project'][:48]:48} files={r['n_files']:>2} "
              f"concl={r['n_conclusions']:>3} {r['load_secs']:>7.2f}s "
              f"{r['secs_per_conclusion']:>6.3f}s/c {r['conclusions_per_sec']:>5}/s")

    totals = {
        "n_projects": len(results),
        "n_files": sum(r["n_files"] for r in results),
        "n_conclusions": sum(r["n_conclusions"] for r in results),
        "body_chars": sum(r["body_chars"] for r in results),
        "load_secs": round(sum(r["load_secs"] for r in results), 3),
        "est_vector_bytes": sum(r["est_vector_bytes"] for r in results),
        "est_content_bytes": sum(r["est_content_bytes"] for r in results),
    }
    tot_c = totals["n_conclusions"]
    totals["secs_per_conclusion"] = round(totals["load_secs"] / tot_c, 4) if tot_c else 0
    totals["conclusions_per_sec"] = round(
        tot_c / totals["load_secs"], 2) if totals["load_secs"] else 0

    out = {"base": BASE, "ws": WS, "observer": OBSERVER,
           "chars_per_token": CHARS_PER_TOKEN, "totals": totals, "projects": results}
    Path(__file__).parent.joinpath("bench_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n== totals: {totals['n_files']} files, {tot_c} conclusions, "
          f"{totals['load_secs']:.1f}s, {totals['secs_per_conclusion']:.3f}s/concl, "
          f"{totals['conclusions_per_sec']}/s ==")
    print("wrote bench_results.json")


if __name__ == "__main__":
    main()
