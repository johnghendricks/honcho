"""Resumable, pausable, batched ingestion of Claude Code transcripts into Honcho.

Serves BOTH the Phase-2 indicative test (run ONE batch, then evaluate) and the
final ingest (run all batches, John-controlled). Phase 2 is literally the first
batch of the final ingest -- same code path, same shape.

Design (see _jgh_/docs/clean-reinstall-and-batched-ingest.md):

  * Unit of work = one session = one source .jsonl transcript file.
  * Source (--source): a single .jsonl, a folder of transcripts (parsed
    recursively), or a pre-parsed .json corpus ({project,sessions,conclusions}).
    Default = the user's Claude Code projects folder (~/.claude/projects).
  * Batch size (--batch-size, default 25) divides a STABLE stratified global
    order into fixed batches; batch numbers are stable across runs (for a given
    source + batch-size), so "batch N still pending" is meaningful. Batch 0 is a
    size-stratified sample of the whole source -> the Phase-2 indicative set.
  * Idempotent + crash-safe: a file is recorded `done` in the SQLite ledger ONLY
    after all its message-POSTs succeed. On resume, a file that is not-done (or
    done-but-missing in Honcho) is DELETED then reloaded clean -> message-adds are
    never duplicated, no matter where a previous run died.
  * Resume after a Bossk reboot: just re-run the same command; the on-disk SQLite
    ledger drives the skip. No daemon.
  * Pause: a `PAUSE` sentinel file OR Ctrl-C (SIGINT) -> finish the current file,
    flush the ledger, exit cleanly. Re-run to continue.
  * Free up Mando: --drain-between-batches waits for the queue to empty at each
    batch boundary, so a pause point leaves Mando idle. (Pausing the loader alone
    does not stop the deriver chewing already-queued work -- that is the separate
    reasoning-flag / process-stop lever.)
  * observe_me: john-cc=true (self-representation), claude-cc=false.

Tally / troubleshooting (SQLite ledger, ingest_ledger.db):
  * files   -- one row per session: status, n_msgs/chars, parse_secs,
               transfer_secs (POST wall-time), timestamps, error.
  * batches -- one row per loaded batch: transfer_secs vs drain_secs
               (derivation wall-time), so slow TRANSFER vs slow DERIVATION is
               distinguishable.
  * runs    -- one row per invocation: config + totals.
  `--status` prints the tally (done/pending/error per batch) + timing stats.

Usage:
    python ingest_batch.py --status                       # tally, do nothing
    python ingest_batch.py --source <dir|file|corpus.json> --dry-run
    python ingest_batch.py --source full_kb.json --batches 1   # Phase-2 batch
    python ingest_batch.py --source full_kb.json               # all batches
    python ingest_batch.py --batch-size 50 --source <dir>      # smaller chunks
    python ingest_batch.py --batch-size 25 --deriver false     # load, no derivation
    python ingest_batch.py --reset --source <...>         # DANGER: wipe full-* + ledger
"""

import argparse
import json
import math
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_transcripts import parse_transcript  # noqa: E402

HERE = Path(__file__).parent
BASE = "http://192.168.0.225:8000"
WS = "default"
USER_PEER = "john-cc"      # the profiled person -> self-observe ON
ASST_PEER = "claude-cc"    # context only -> self-observe OFF
SESSION_PREFIX = "full-"
SOURCE_TAG = "cc-transcript-import"
DEFAULT_SOURCE = Path.home() / ".claude" / "projects"   # user's CC folder
DB_PATH = HERE / "ingest_ledger.db"
PAUSE_SENTINEL = HERE / "PAUSE"

_STOP = False  # set by SIGINT; honored at the next file boundary


def _on_sigint(_sig, _frm):
    global _STOP
    _STOP = True
    print("\n[SIGINT] finishing current file, then pausing cleanly...", flush=True)


signal.signal(signal.SIGINT, _on_sigint)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


TS_FORMAT = "%I:%M:%S %p | %m-%d-%Y"   # e.g. "09:54:28 PM | 06-12-2026"


def stamp() -> str:
    """Local wall-clock for human-scannable scrollback."""
    return datetime.now().astimezone().strftime(TS_FORMAT)


def fmt_local(iso: str) -> str:
    """Render a stored UTC iso timestamp as local wall-clock."""
    return datetime.fromisoformat(iso).astimezone().strftime(TS_FORMAT)


def fmt_dur(secs: float) -> str:
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def api(method: str, path: str, body: dict | None = None, timeout: int = 120):
    url = f"{BASE}/v3{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or "{}")


def api_ok(method: str, path: str, body: dict | None = None, ok=(200, 201, 409)):
    try:
        return api(method, path, body)
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return None
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail[:500]}")


def list_full_sessions() -> set[str]:
    out = api("POST", f"/workspaces/{WS}/sessions/list", {})
    items = out.get("items", out) if isinstance(out, dict) else out
    return {s["id"] for s in items if str(s["id"]).startswith(SESSION_PREFIX)}


def queue_status() -> dict:
    return api("GET", f"/workspaces/{WS}/queue/status", timeout=60)


def workspace_reasoning_on() -> bool | None:
    try:  # no GET-by-id; POST get-or-creates and returns the existing workspace
        out = api("POST", "/workspaces", {"id": WS})
    except Exception:
        return None
    cfg = out.get("configuration", {}) if isinstance(out, dict) else {}
    r = cfg.get("reasoning")
    return bool(r.get("enabled")) if isinstance(r, dict) else None


# --------------------------------------------------------------------------- #
# SQLite ledger
# --------------------------------------------------------------------------- #
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files(
        session_id      TEXT PRIMARY KEY,
        honcho_session  TEXT,
        project         TEXT,
        source_path     TEXT,
        n_msgs          INTEGER,
        n_chars         INTEGER,
        batch_idx       INTEGER,
        status          TEXT,            -- 'done' | 'error'
        parse_secs      REAL,
        transfer_secs   REAL,
        started_at      TEXT,
        completed_at    TEXT,
        error           TEXT
    );
    CREATE TABLE IF NOT EXISTS batches(
        run_id          TEXT,
        batch_idx       INTEGER,
        n_files         INTEGER,
        n_msgs          INTEGER,
        transfer_secs   REAL,
        drain_secs      REAL,
        started_at      TEXT,
        finished_at     TEXT,
        PRIMARY KEY (run_id, batch_idx)
    );
    CREATE TABLE IF NOT EXISTS runs(
        run_id          TEXT PRIMARY KEY,
        started_at      TEXT,
        finished_at     TEXT,
        source          TEXT,
        batch_size      INTEGER,
        files_loaded    INTEGER,
        msgs_loaded     INTEGER,
        transfer_secs   REAL,
        status          TEXT             -- 'running' | 'completed' | 'paused' | 'error'
    );
    """)
    con.commit()
    return con


def done_ids(con: sqlite3.Connection) -> set[str]:
    return {r["session_id"] for r in
            con.execute("SELECT session_id FROM files WHERE status='done'")}


def skipped_ids(con: sqlite3.Connection) -> set[str]:
    # Terminal-but-not-loaded: source file vanished from the live ~/.claude/projects
    # dir between scan and load. Can never be loaded, so don't retry it on resume.
    return {r["session_id"] for r in
            con.execute("SELECT session_id FROM files WHERE status='skipped'")}


def record_file(con, *, session_id, honcho_session, project, source_path, n_msgs,
                n_chars, batch_idx, status, parse_secs, transfer_secs,
                started_at, error=None):
    con.execute("""
        INSERT INTO files(session_id,honcho_session,project,source_path,n_msgs,
            n_chars,batch_idx,status,parse_secs,transfer_secs,started_at,
            completed_at,error)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(session_id) DO UPDATE SET
            honcho_session=excluded.honcho_session, project=excluded.project,
            source_path=excluded.source_path, n_msgs=excluded.n_msgs,
            n_chars=excluded.n_chars, batch_idx=excluded.batch_idx,
            status=excluded.status, parse_secs=excluded.parse_secs,
            transfer_secs=excluded.transfer_secs, started_at=excluded.started_at,
            completed_at=excluded.completed_at, error=excluded.error
    """, (session_id, honcho_session, project, source_path, n_msgs, n_chars,
          batch_idx, status, parse_secs, transfer_secs, started_at, now_iso(),
          error))
    con.commit()


# --------------------------------------------------------------------------- #
# Source discovery
# --------------------------------------------------------------------------- #
def discover(source: Path) -> tuple[list[dict], list[str]]:
    """Return (items, conclusions). Each item: session_id, project, source_path,
    size (for stratification), and either preparsed `messages` or None (parse on
    load). `size` = msg count for a pre-parsed corpus, else file bytes (proxy)."""
    items: list[dict] = []
    if source.is_file() and source.suffix == ".json":          # pre-parsed corpus
        data = json.loads(source.read_text(encoding="utf-8"))
        proj = data.get("project", "corpus")
        for s in data["sessions"]:
            if s.get("messages"):
                items.append({"session_id": s["session_id"], "project": proj,
                              "source_path": str(source), "messages": s["messages"],
                              "size": len(s["messages"])})
        return items, data.get("conclusions", [])

    if source.is_file() and source.suffix == ".jsonl":
        files = [source]
    elif source.is_dir():
        # NON-recursive: top-level *.jsonl are the main-thread session transcripts.
        # Subagent side-conversations and tool dumps live in subdirs (subagents/,
        # tool-results/) and must NOT be ingested -- they are not the peer's words
        # (parse_transcripts.py likewise globs *.jsonl non-recursively + skips
        # isSidechain). A project dir has *.jsonl directly; the CC projects ROOT
        # has none at top level, so descend exactly ONE level into each project.
        files = sorted(source.glob("*.jsonl"))
        if not files:
            files = sorted(f for d in source.iterdir() if d.is_dir()
                           for f in d.glob("*.jsonl"))
    else:
        raise SystemExit(f"--source not found / unsupported: {source}")

    for f in files:
        items.append({"session_id": f.stem, "project": f.parent.name,
                      "source_path": str(f), "messages": None,
                      "size": f.stat().st_size})
    return items, []


def stratified_order(items: list[dict], batch_size: int) -> list[dict]:
    """Stable global order whose first batch_size items are a size-stratified
    sample of the whole source. Sort by size (tie-break session_id), pick batch 0
    by even strides, then append the remainder in size order."""
    by_size = sorted(items, key=lambda s: (s["size"], s["session_id"]))
    n = len(by_size)
    if n <= batch_size:
        return by_size
    idxs = sorted({round(i * (n - 1) / (batch_size - 1)) for i in range(batch_size)})
    chosen = set(idxs)
    first = [by_size[i] for i in idxs]
    rest = [by_size[i] for i in range(n) if i not in chosen]
    return first + rest


# --------------------------------------------------------------------------- #
# Loading one file (idempotent + timed)
# --------------------------------------------------------------------------- #
def delete_session_and_wait(sid: str, tries: int = 40):
    api_ok("DELETE", f"/workspaces/{WS}/sessions/{sid}", ok=(200, 204, 404))
    for _ in range(tries):
        if sid not in list_full_sessions():
            time.sleep(2)  # buffer for async hard-delete
            return
        time.sleep(2)
    raise SystemExit(f"session {sid} still present after delete; aborting to avoid dupes")


def ensure_session(sid: str, project: str, orig: str, derive: bool = True):
    # reasoning.enabled is set per-SESSION here (resolution order is
    # workspace -> session -> message, src/utils/config_helpers.get_configuration),
    # so --deriver false skips derivation for THIS ingest's full-* sessions only and
    # leaves the workspace-level reasoning flag (live john/claude data) untouched.
    # Disabling is not retroactive: with reasoning off, no representation queue items
    # are ever created for these messages (src/deriver/enqueue.py returns early), so
    # flipping it back on later will NOT backfill -- re-ingest to derive them.
    api_ok("POST", f"/workspaces/{WS}/sessions", {
        "id": sid,
        "metadata": {"source": "claude-code", "project": project,
                     "import": "batched-ingest", "orig_session": orig},
        "peers": {USER_PEER: {}, ASST_PEER: {}},
        "configuration": {"reasoning": {"enabled": derive}},
    })


def post_messages(sid: str, msgs: list[dict], project: str, orig: str) -> tuple[int, int]:
    batch = []
    for m in msgs:
        item = {
            "peer_id": USER_PEER if m["role"] == "user" else ASST_PEER,
            "content": m["content"],
            "metadata": {"source": SOURCE_TAG, "project": project, "orig_session": orig},
        }
        if m.get("ts"):
            item["created_at"] = m["ts"]
        batch.append(item)
    n_chars = sum(len(m["content"]) for m in msgs)
    for i in range(0, len(batch), 100):
        api("POST", f"/workspaces/{WS}/sessions/{sid}/messages",
            {"messages": batch[i:i + 100]}, timeout=600)
    return len(batch), n_chars


def load_file(con, item: dict, existing: set[str], batch_idx: int,
              derive: bool = True) -> int:
    """Load one session idempotently, recording timings. Returns message count."""
    orig = item["session_id"]
    project = item["project"]
    started = now_iso()

    t = time.perf_counter()
    msgs = item["messages"]
    if msgs is None:
        msgs = parse_transcript(Path(item["source_path"]))
    parse_secs = round(time.perf_counter() - t, 3)

    if not msgs:   # all-noise transcript -> nothing to load, but mark processed
        record_file(con, session_id=orig, honcho_session=None, project=project,
                    source_path=item["source_path"], n_msgs=0, n_chars=0,
                    batch_idx=batch_idx, status="done", parse_secs=parse_secs,
                    transfer_secs=0.0, started_at=started)
        return 0

    sid = SESSION_PREFIX + orig
    t = time.perf_counter()
    if sid in existing:                       # orphan/partial from a crashed run
        delete_session_and_wait(sid)
    ensure_session(sid, project, orig, derive)
    n_msgs, n_chars = post_messages(sid, msgs, project, orig)
    transfer_secs = round(time.perf_counter() - t, 3)

    record_file(con, session_id=orig, honcho_session=sid, project=project,
                source_path=item["source_path"], n_msgs=n_msgs, n_chars=n_chars,
                batch_idx=batch_idx, status="done", parse_secs=parse_secs,
                transfer_secs=transfer_secs, started_at=started)
    existing.add(sid)
    return n_msgs


# --------------------------------------------------------------------------- #
# Drain
# --------------------------------------------------------------------------- #
def drain_queue(max_wait: int = 4 * 3600, poll: int = 10, frozen_after: int = 600) -> float | None:
    """Block until global pending+in_progress hit 0. Returns drain seconds, or
    None if frozen/timeout/paused."""
    t0 = time.perf_counter()
    last_sig, last_change = None, 0.0
    print("  draining queue...", flush=True)
    while time.perf_counter() - t0 < max_wait:
        st = queue_status()
        pend = st.get("pending_work_units", 0)
        inprog = st.get("in_progress_work_units", 0)
        el = round(time.perf_counter() - t0, 1)
        print(f"    t={el:>7}s pending={pend} in_progress={inprog}", flush=True)
        if pend == 0 and inprog == 0:
            print(f"  drained in {el}s", flush=True)
            return el
        sig = (pend, inprog)
        if sig != last_sig:
            last_sig, last_change = sig, el
        elif el - last_change > frozen_after:
            print(f"  [!] FROZEN: no queue movement {frozen_after}s "
                  f"(pending={pend} in_progress={inprog}) -- deriver stalled?", flush=True)
            return None
        if _STOP or PAUSE_SENTINEL.exists():
            print("  [pause] requested during drain; leaving queue to settle.", flush=True)
            return None
        time.sleep(poll)
    print(f"  [!] drain timed out after {max_wait}s", flush=True)
    return None


# --------------------------------------------------------------------------- #
# Status / tally
# --------------------------------------------------------------------------- #
def print_status(con, order: list[dict], batch_size: int):
    done = done_ids(con)
    skipped = skipped_ids(con)
    resolved = done | skipped   # terminal: loaded or vanished — won't be retried
    total = len(order)
    n_done = sum(1 for s in order if s["session_id"] in done)
    n_skipped = sum(1 for s in order if s["session_id"] in skipped)
    by_batch: dict[int, list[int]] = {}
    for i, s in enumerate(order):
        bi = i // batch_size
        d = 1 if s["session_id"] in resolved else 0
        agg = by_batch.setdefault(bi, [0, 0])
        agg[0] += 1
        agg[1] += d
    n_err = con.execute("SELECT COUNT(*) c FROM files WHERE status='error'").fetchone()["c"]

    print(f"\n== TALLY (as of {stamp()}) ==  total={total}  done={n_done}  "
          f"pending={total - n_done - n_skipped}  skipped={n_skipped}  error={n_err}")
    rr = con.execute("SELECT MIN(started_at) s, COUNT(*) n FROM runs").fetchone()
    if rr and rr["s"]:
        elapsed = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(rr["s"])).total_seconds()
        print(f"started {fmt_local(rr['s'])}  |  elapsed {fmt_dur(elapsed)}"
              + (f"  (across {rr['n']} runs incl. resumes)" if rr["n"] > 1 else ""))
    print("per batch (done/total):")
    for bi in sorted(by_batch):
        tot, dn = by_batch[bi][0], by_batch[bi][1]
        mark = "ok " if dn == tot else "...."
        print(f"  batch {bi:>3}: {dn:>4}/{tot:<4} {mark}")

    row = con.execute("""SELECT COUNT(*) c, SUM(n_msgs) m, SUM(transfer_secs) ts,
                         AVG(transfer_secs) ats, SUM(parse_secs) ps
                         FROM files WHERE status='done' AND n_msgs>0""").fetchone()
    if row and row["c"]:
        ts = row["ts"] or 0.0
        m = row["m"] or 0
        print("\ntiming (loaded files):")
        print(f"  files={row['c']}  msgs={m}  parse={row['ps']:.1f}s  "
              f"transfer={ts:.1f}s  avg/file={row['ats']:.2f}s  "
              f"msgs/s={m/ts:.1f}" if ts else "")
    brows = con.execute("""SELECT run_id,batch_idx,n_files,n_msgs,transfer_secs,
                          drain_secs FROM batches ORDER BY started_at""").fetchall()
    if brows:
        print("\nper-batch transfer vs derivation-drain:")
        for b in brows:
            dr = f"{b['drain_secs']:.0f}s" if b["drain_secs"] is not None else "n/a"
            print(f"  batch {b['batch_idx']:>3}: {b['n_files']} files, "
                  f"transfer={b['transfer_secs']:.0f}s  drain={dr}")
    erows = con.execute("""SELECT session_id,error FROM files WHERE status='error'
                         LIMIT 10""").fetchall()
    if erows:
        print("\nerrors:")
        for e in erows:
            print(f"  {e['session_id']}: {str(e['error'])[:160]}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def paused() -> bool:
    return _STOP or PAUSE_SENTINEL.exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE),
                    help=".jsonl file, folder of transcripts, or pre-parsed "
                         ".json corpus (default: ~/.claude/projects)")
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--batches", type=int, default=0,
                    help="max batches this run (0 = all remaining)")
    ap.add_argument("--deriver", choices=("true", "false"), default="true",
                    help="run the deriver on this batch (default: true). false sets "
                         "reasoning.enabled=false per-session on the full-* sessions "
                         "so messages load WITHOUT derivation (not retroactive); the "
                         "workspace-level reasoning flag / live data is untouched.")
    ap.add_argument("--drain-between-batches", action=argparse.BooleanOptionalAction,
                    default=None,
                    help="wait for the deriver queue to fully drain after each batch "
                         "before loading the next (each batch fully processes before a "
                         "new one is ingested, so a PAUSE leaves at most one batch "
                         "deriving). Default: AUTO — on when --deriver is on, off when "
                         "it's off. Override with --drain-between-batches / "
                         "--no-drain-between-batches.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="DANGER: delete all full-* sessions and drop the ledger")
    args = ap.parse_args()

    print(f"=== honcho-ingest @ {stamp()} ===")
    derive = args.deriver == "true"
    # AUTO: drain follows the deriver unless explicitly overridden. Draining is only
    # meaningful when the deriver is on (otherwise the queue is always empty).
    drain = args.drain_between_batches if args.drain_between_batches is not None else derive
    drain_explicit = args.drain_between_batches is not None
    source = Path(args.source).expanduser()
    items, conclusions = discover(source)
    order = stratified_order(items, args.batch_size)
    total = len(order)
    nb = max(1, math.ceil(total / args.batch_size))
    con = db()

    print(f"source={source}")
    print(f"sessions={total}  batch_size={args.batch_size}  batches={nb}"
          + (f"  conclusions={len(conclusions)}" if conclusions else ""))

    if args.reset:
        existing = list_full_sessions()
        print(f"RESET: deleting {len(existing)} full-* sessions + dropping ledger")
        if not args.dry_run:
            for sid in sorted(existing):
                api_ok("DELETE", f"/workspaces/{WS}/sessions/{sid}", ok=(200, 204, 404))
            con.close()
            DB_PATH.unlink(missing_ok=True)
        print("RESET done." if not args.dry_run else "RESET (dry-run).")
        return

    done = done_ids(con)
    skipped = skipped_ids(con)
    terminal = done | skipped   # never retry done-or-vanished files
    print(f"ledger: {len(done)} done, {total - len(terminal)} remaining"
          + (f" ({len(skipped)} skipped/missing)" if skipped else ""))

    if args.status:
        print_status(con, order, args.batch_size)
        return

    # Pending files grouped by their STABLE global batch index.
    pending_by_batch: dict[int, list[dict]] = {}
    for i, s in enumerate(order):
        if s["session_id"] not in terminal:
            pending_by_batch.setdefault(i // args.batch_size, []).append(s)
    pending_batches = sorted(pending_by_batch)
    limit = args.batches if args.batches > 0 else len(pending_batches)
    run_batches = pending_batches[:limit]
    print(f"this run: batches {run_batches if run_batches else '(none — all done)'}"
          f"  deriver={'on' if derive else 'off'}"
          + (" [DRY-RUN]" if args.dry_run else ""))

    if args.dry_run:
        for bi in run_batches:
            grp = pending_by_batch[bi]
            sizes = [s["size"] for s in grp]
            print(f"  batch {bi}: {len(grp)} files, size min/med/max="
                  f"{min(sizes)}/{sorted(sizes)[len(sizes)//2]}/{max(sizes)}")
        return
    if not run_batches:
        return

    r = workspace_reasoning_on()
    if derive:
        print(f"deriver = ON (per-session reasoning.enabled=true). "
              f"workspace reasoning.enabled = {r}" +
              ("  [!] workspace reasoning is OFF — derivation still won't run; "
               "enable it on the workspace first" if r is False else ""))
    else:
        print("deriver = OFF (per-session reasoning.enabled=false): messages will "
              "load WITHOUT derivation; not retroactive. Live data untouched.")
    _src = "explicit override" if drain_explicit else ("auto: deriver on" if derive
                                                        else "auto: deriver off")
    if drain:
        print(f"drain = ON ({_src}): each batch fully derives before the next loads; "
              "a PAUSE leaves at most one batch's work deriving.")
    else:
        print(f"drain = OFF ({_src}): all batches load back-to-back; the deriver "
              "queue backs up and drains on its own. PAUSE only stops the loader — "
              "already-queued derivation keeps running on the server.")

    api_ok("POST", f"/workspaces/{WS}/peers",
           {"id": USER_PEER, "configuration": {"observe_me": True}})
    api_ok("POST", f"/workspaces/{WS}/peers",
           {"id": ASST_PEER, "configuration": {"observe_me": False}})
    print(f"peers ensured: {USER_PEER}(observe_me=true) {ASST_PEER}(observe_me=false)")

    run_id = now_iso()
    con.execute("INSERT INTO runs(run_id,started_at,source,batch_size,files_loaded,"
                "msgs_loaded,transfer_secs,status) VALUES(?,?,?,?,0,0,0,'running')",
                (run_id, now_iso(), str(source), args.batch_size))
    con.commit()

    existing = list_full_sessions()
    loaded_files = loaded_msgs = 0
    run_transfer = 0.0
    final_status = "completed"
    consec_errors = 0           # circuit breaker: abort only on a run of failures
    MAX_CONSEC_ERRORS = 5       # (a systemic outage), not on isolated bad files
    for n, bi in enumerate(run_batches):
        if paused():
            print(f"[pause] before batch {bi}; stopping. Re-run to continue.")
            final_status = "paused"
            break
        grp = pending_by_batch[bi]
        print(f"\n== batch {bi} ({len(grp)} files) @ {stamp()} ==", flush=True)
        b_started, b_transfer, b_msgs, b_files = now_iso(), 0.0, 0, 0
        for s in grp:
            if paused():
                print("[pause] at file boundary; stopping. Re-run to continue.")
                final_status = "paused"
                break
            try:
                msgs = load_file(con, s, existing, bi, derive)
                consec_errors = 0
            except SystemExit:
                raise
            except FileNotFoundError as e:
                # Source file vanished from the live ~/.claude/projects dir between
                # scan and load. Benign — mark terminal-skipped and keep going.
                record_file(con, session_id=s["session_id"], honcho_session=None,
                            project=s["project"], source_path=s["source_path"],
                            n_msgs=0, n_chars=0, batch_idx=bi, status="skipped",
                            parse_secs=0.0, transfer_secs=0.0,
                            started_at=now_iso(), error="source file missing at load")
                print(f"  [skip] {s['session_id']}: source file gone since scan — "
                      "skipping (won't retry).")
                consec_errors = 0
                continue
            except Exception as e:                       # record + continue (breaker)
                record_file(con, session_id=s["session_id"], honcho_session=None,
                            project=s["project"], source_path=s["source_path"],
                            n_msgs=0, n_chars=0, batch_idx=bi, status="error",
                            parse_secs=0.0, transfer_secs=0.0,
                            started_at=now_iso(), error=repr(e))
                consec_errors += 1
                print(f"  [!] error on {s['session_id']}: {e!r} "
                      f"(recorded; {consec_errors}/{MAX_CONSEC_ERRORS} consecutive)")
                if consec_errors >= MAX_CONSEC_ERRORS:
                    print(f"  [!] {consec_errors} consecutive errors — aborting "
                          "(looks systemic, e.g. server down). Re-run to resume.")
                    final_status = "error"
                    raise
                continue   # isolated bad file — left pending, retried on next run
            row = con.execute("SELECT transfer_secs FROM files WHERE session_id=?",
                              (s["session_id"],)).fetchone()
            b_transfer += row["transfer_secs"] or 0.0
            b_msgs += msgs
            b_files += 1
            loaded_files += 1
            loaded_msgs += msgs
        run_transfer += b_transfer
        drain_secs = None
        if drain and not paused():
            drain_secs = drain_queue()
        con.execute("INSERT OR REPLACE INTO batches(run_id,batch_idx,n_files,n_msgs,"
                    "transfer_secs,drain_secs,started_at,finished_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, bi, b_files, b_msgs, round(b_transfer, 3), drain_secs,
                     b_started, now_iso()))
        con.commit()
        print(f"  batch {bi} done: {b_files} files, {b_msgs} msgs, "
              f"transfer={b_transfer:.0f}s"
              + (f", drain={drain_secs:.0f}s" if drain_secs is not None else ""))
        if paused():
            break

    con.execute("UPDATE runs SET finished_at=?,files_loaded=?,msgs_loaded=?,"
                "transfer_secs=?,status=? WHERE run_id=?",
                (now_iso(), loaded_files, loaded_msgs, round(run_transfer, 3),
                 final_status, run_id))
    con.commit()
    print(f"\n== run {final_status}: +{loaded_files} files, +{loaded_msgs} msgs, "
          f"transfer={run_transfer:.0f}s "
          f"(ledger now {len(done_ids(con))} done) ==")
    if drain and final_status == "completed":
        print("All batches (including the last) drained to an empty queue — "
              "derivation is fully caught up.")
    elif drain and final_status == "paused":
        print("Paused: the loader stopped and no new batches were ingested. The "
              "batch in flight when you paused may still be deriving on the server "
              "(bounded to one batch) — run --status / get_queue_status to watch it "
              "settle.")
    elif derive and not drain:
        print("Note: drain is OFF — the deriver queue is still backed up and will "
              "keep processing on the server long after this run exits. PAUSE will "
              "NOT stop it; that requires stopping the deriver process on the server.")
    con.close()


if __name__ == "__main__":
    main()
