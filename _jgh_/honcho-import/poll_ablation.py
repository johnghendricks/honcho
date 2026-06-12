"""Track the `ablation` workspace queue draining to zero on the live Mando deriver.

Companion to ablation_assistant_context.py. Unlike poll_deriver.py (single
`default`-workspace session), this watches the WHOLE ablation workspace queue
drain from ~1277 pending to 0, which is what the 3-arm ablation enqueues.

Robust to the periodic queue-cleanup that wipes *completed* counts: throughput
is estimated from positive completed-deltas (resets ignored), and "done" is
detected as pending+in_progress == 0 after we've seen the queue active.

Usage:
    python poll_ablation.py            # poll until drained (or frozen)
"""

import json
import os
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get("HONCHO_BASE", "http://192.168.0.225:8000")
WS = os.environ.get("ABLATION_WS", "ablation")
OUT = Path(__file__).parent / "ablation_poll_results.json"
POLL = 5
MAX_WAIT = 4 * 3600          # ablation drain can run 1-2h; give it room
FROZEN_AFTER = 600           # no movement this long after being active -> frozen
                             # (>= the ~302s slow summary tasks so they don't false-trip)


def status() -> dict:
    r = urllib.request.Request(f"{BASE}/v3/workspaces/{WS}/queue/status", method="GET")
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


def main():
    t0 = time.perf_counter()
    timeline = []
    drain_start = None
    seen_active = False
    peak_pending = 0
    prev_completed = None
    thru_units = 0
    thru_secs = 0.0
    prev_t = t0
    last_sig = None
    last_change = 0.0
    deadline = t0 + MAX_WAIT

    while time.perf_counter() < deadline:
        st = status()
        pend = st.get("pending_work_units", 0)
        inprog = st.get("in_progress_work_units", 0)
        comp = st.get("completed_work_units", 0)
        now = time.perf_counter()
        el = round(now - t0, 1)
        peak_pending = max(peak_pending, pend)

        if prev_completed is not None and comp > prev_completed:
            thru_units += (comp - prev_completed)
            thru_secs += (now - prev_t)
        prev_completed = comp
        prev_t = now

        if (pend + inprog) > 0:
            seen_active = True
            if drain_start is None:
                drain_start = el

        rate = round(thru_units / thru_secs, 3) if thru_secs else 0
        done_units = max(0, peak_pending - pend)
        eta = round(pend / rate / 60, 1) if rate else None
        timeline.append({"t": el, "pend": pend, "inprog": inprog, "comp": comp,
                         "rate_per_s": rate, "eta_min": eta})
        print(f"t={el:>7}s pend={pend:>5} inprog={inprog:>3} done~{done_units:>5}/"
              f"{peak_pending} rate={rate}/s eta~{eta}min", flush=True)

        OUT.write_text(json.dumps({
            "ws": WS, "model": "gemma (Mando)", "elapsed": el,
            "peak_pending": peak_pending, "drain_start_secs": drain_start,
            "seen_active": seen_active, "throughput_per_sec": rate,
            "eta_min": eta, "timeline": timeline,
        }, indent=2), encoding="utf-8")

        if seen_active and pend == 0 and inprog == 0:
            break

        sig = (comp, pend, inprog)
        if sig != last_sig:
            last_sig = sig
            last_change = el
        elif seen_active and el - last_change > FROZEN_AFTER:
            print(f"\nFROZEN: no queue movement for {FROZEN_AFTER}s "
                  f"(pending={pend}, in_progress={inprog}). Deriver down/stalled.",
                  flush=True)
            OUT.write_text(json.dumps({"FROZEN": True, "frozen_after_s": FROZEN_AFTER,
                                       "pending": pend, "in_progress": inprog,
                                       "peak_pending": peak_pending,
                                       "timeline": timeline}, indent=2),
                           encoding="utf-8")
            return
        time.sleep(POLL)

    el = round(time.perf_counter() - t0, 1)
    drain_secs = round(el - drain_start, 1) if drain_start is not None else None
    rate = round(thru_units / thru_secs, 3) if thru_secs else 0
    per_task = round(1 / rate, 2) if rate else None
    summary = {
        "ws": WS, "model": "gemma (Mando)", "DONE": True,
        "peak_pending": peak_pending, "drain_secs": drain_secs,
        "throughput_per_sec": rate, "secs_per_task": per_task,
        "timeline": timeline,
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nDONE: drained {peak_pending} tasks in {drain_secs}s "
          f"(~{per_task}s/task, {rate}/s). Ready for the grounding audit.")


if __name__ == "__main__":
    main()
