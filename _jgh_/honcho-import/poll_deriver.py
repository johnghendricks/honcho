"""Track a session's representation tasks draining to completion, timing the
Deriver (gemma4:26b) on the live Mando queue.

Robust to the periodic queue-cleanup that wipes *completed* counts: we time the
PENDING count draining (pending is not wiped until the item is done) and estimate
global throughput from positive completed-deltas (resets ignored).

Usage:
    python poll_deriver.py <internal_session_id> <n_tasks>
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://192.168.0.225:8000"
WS = "default"
SID = sys.argv[1]
N = int(sys.argv[2])
OUT = Path(__file__).parent / "bench_deriver_results.json"
POLL = 3
MAX_WAIT = 600
FROZEN_AFTER = 180  # if no completions + no pending change for this long, call it frozen


def status():
    r = urllib.request.Request(f"{BASE}/v3/workspaces/{WS}/queue/status", method="GET")
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


t0 = time.perf_counter()
timeline = []
drain_start = None          # when mine first goes pending>0 / inprog>0
seen_active = False
prev_g_completed = None
g_throughput_units = 0      # accumulated positive global completed-deltas
g_throughput_secs = 0.0
prev_t = t0
deadline = t0 + MAX_WAIT

while time.perf_counter() < deadline:
    st = status()
    mine = st.get("sessions", {}).get(SID, {})
    m_pend = mine.get("pending_work_units", 0)
    m_inprog = mine.get("in_progress_work_units", 0)
    m_comp = mine.get("completed_work_units", 0)
    m_tot = mine.get("total_work_units", 0)
    g_comp = st.get("completed_work_units", 0)
    g_pend = st.get("pending_work_units", 0)
    g_inprog = st.get("in_progress_work_units", 0)
    now = time.perf_counter()
    el = round(now - t0, 1)

    # Global throughput via positive deltas (ignore cleanup resets).
    if prev_g_completed is not None and g_comp > prev_g_completed:
        g_throughput_units += (g_comp - prev_g_completed)
        g_throughput_secs += (now - prev_t)
    prev_g_completed = g_comp
    prev_t = now

    if (m_pend + m_inprog) > 0:
        seen_active = True
        if drain_start is None:
            drain_start = el

    timeline.append({"t": el, "m_pend": m_pend, "m_inprog": m_inprog,
                     "m_comp": m_comp, "g_pend": g_pend, "g_inprog": g_inprog,
                     "g_comp": g_comp})
    g_rate = round(g_throughput_units / g_throughput_secs, 3) if g_throughput_secs else 0
    print(f"t={el:>6}s mine[pend={m_pend} inprog={m_inprog} done={m_comp}/{m_tot}] "
          f"global[pend={g_pend} inprog={g_inprog}] gThru={g_rate}/s", flush=True)

    OUT.write_text(json.dumps({
        "internal_session": SID, "n_tasks": N, "model": "gemma4:26b (Mando)",
        "elapsed": el, "drain_start_secs": drain_start, "seen_active": seen_active,
        "global_throughput_per_sec": g_rate, "timeline": timeline,
    }, indent=2), encoding="utf-8")

    # Done = we saw it active and now it's fully drained.
    if seen_active and m_pend == 0 and m_inprog == 0:
        break
    # Frozen detection: no global completions advancing and our pending static.
    sig = (g_comp, m_pend, m_inprog, g_pend, g_inprog)
    if "last_sig" not in dir() or sig != last_sig:  # type: ignore
        last_sig = sig
        last_change = el
    elif el - last_change > FROZEN_AFTER:
        print(f"\nFROZEN: no queue movement for {FROZEN_AFTER}s "
              f"(mine still pending={m_pend}, global in_progress stuck={g_inprog}). "
              f"Deriver worker appears down/stalled.", flush=True)
        OUT.write_text(json.dumps({"FROZEN": True, "frozen_after_s": FROZEN_AFTER,
                                   "my_pending": m_pend, "global_in_progress": g_inprog,
                                   "timeline": timeline}, indent=2), encoding="utf-8")
        sys.exit(0)
    time.sleep(POLL)

el = round(time.perf_counter() - t0, 1)
drain_secs = round(el - drain_start, 1) if drain_start is not None else None
per_task = round(drain_secs / N, 1) if drain_secs else None
g_rate = round(g_throughput_units / g_throughput_secs, 3) if g_throughput_secs else 0
g_per_task = round(1 / g_rate, 1) if g_rate else None

summary = {
    "internal_session": SID, "n_tasks": N, "model": "gemma4:26b (Mando)",
    "DONE": True,
    "my_drain_secs": drain_secs,
    "my_secs_per_task_contended": per_task,
    "global_throughput_per_sec": g_rate,
    "global_secs_per_task": g_per_task,
    "PROJECTION_full_kb_proto_1": {
        "msgs": 6386,
        "hours_at_my_contended_rate": round(6386 * per_task / 3600, 1) if per_task else None,
        "hours_at_global_throughput": round(6386 * g_per_task / 3600, 1) if g_per_task else None,
    },
    "timeline": timeline,
}
OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(f"\nDONE: my {N} tasks drained in {drain_secs}s "
      f"(~{per_task}s/task contended) | global ~{g_per_task}s/task ({g_rate}/s)")
