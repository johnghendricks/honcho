#!/usr/bin/env bash
# Dreamer model sweep — runs ON Mando (host). For each model: reset john-cc's
# regenerable dream layer, swap the DREAM_* model in .env, recreate the deriver,
# fire one dream, wait for it, and record the 4 verdict metrics:
#   1. tool-loop completion (did deduction/induction call their create tools?)
#   2. #deductive / #inductive conclusions produced
#   3. peer-card write (did update_peer_card fire? is the card now populated?)
#   4. latency (dream duration) + VRAM (resident model size)
#
# Only the regenerable deductive/inductive docs + peer_card are touched on
# default/john-cc/john-cc; the 5,324 explicit conclusions are never modified.
#
# Usage:  zsh dream_sweep.sh <model> [<model> ...]
# Output: appends one JSON line per model to dream_sweep_results.jsonl
set -uo pipefail
cd ~/honcho || exit 1

WS=default; OBS=john-cc
RESULTS=~/honcho/dream_sweep_results.jsonl
WAIT_ITERS=150     # 150 * 10s = 25 min max per dream (covers 70B cold loads)

psql() { docker compose exec -T database psql -U postgres -d postgres -tAc "$1" 2>/dev/null | tr -d '\r'; }

reset_state() {
  docker compose exec -T database psql -U postgres -d postgres -q >/dev/null 2>&1 <<SQL
DELETE FROM documents WHERE workspace_name='$WS' AND observer='$OBS' AND observed='$OBS' AND level IN ('deductive','inductive');
UPDATE peers SET internal_metadata = internal_metadata - 'peer_card' WHERE workspace_name='$WS' AND name='$OBS';
SQL
}

set_model() {
  local m="$1"
  perl -i -pe "s|^DREAM_DEDUCTION_MODEL_CONFIG__MODEL=.*|DREAM_DEDUCTION_MODEL_CONFIG__MODEL=$m|; s|^DREAM_INDUCTION_MODEL_CONFIG__MODEL=.*|DREAM_INDUCTION_MODEL_CONFIG__MODEL=$m|" .env
  docker compose up -d --no-deps --force-recreate deriver >/dev/null 2>&1
  for i in $(seq 1 40); do
    docker compose logs deriver --no-color --tail 15 2>/dev/null | grep -q "Running main loop" && break
    sleep 2
  done
  sleep 3
}

fire_dream() {
  docker compose exec -T api /app/.venv/bin/python - <<'PY' >/dev/null 2>&1
import asyncio
from src.schemas import DreamType
from src.deriver.enqueue import enqueue_dream
asyncio.run(enqueue_dream("default","john-cc","john-cc",DreamType.OMNI,session_name=None))
PY
}

jstr() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

TRIALS=${TRIALS:-3}
for MODEL in "$@"; do
  echo ">>> [$MODEL] swap + recreate, then $TRIALS trials" >&2
  set_model "$MODEL"
 for TRIAL in $(seq 1 $TRIALS); do
  reset_state
  MARK=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  EPOCH0=$(date +%s)
  fire_dream
  QID=$(psql "SELECT id FROM queue WHERE task_type='dream' ORDER BY created_at DESC LIMIT 1")
  echo ">>> [$MODEL] trial $TRIAL/$TRIALS queued id=$QID (mark=$MARK)" >&2

  STATUS=timeout
  for i in $(seq 1 $WAIT_ITERS); do
    P=$(psql "SELECT processed FROM queue WHERE id=$QID")
    [ "$P" = "t" ] && { STATUS=done; break; }
    sleep 10
  done
  EPOCH1=$(date +%s); WALL=$((EPOCH1-EPOCH0))

  # --- parse logs for this dream's run window ---
  LOG=$(docker compose logs deriver --no-color --since "$MARK" 2>/dev/null)
  RUNID=$(printf '%s' "$LOG" | grep -oE '\[[A-Za-z0-9_-]+\] Starting dream cycle' | head -1 | sed -E 's/^\[([A-Za-z0-9_-]+)\].*/\1/')
  DED_CALLS=$(printf '%s' "$LOG" | grep -c 'create_observations_deductive keys')
  IND_CALLS=$(printf '%s' "$LOG" | grep -c 'create_observations_inductive keys')
  CARD_CALLS=$(printf '%s' "$LOG" | grep -c 'update_peer_card keys')
  DUR_MS=$(printf '%s' "$LOG" | grep -oE 'Dream completed: run_id=[^ ]+ .*duration=[0-9]+ms' | grep -oE 'duration=[0-9]+' | grep -oE '[0-9]+' | tail -1)
  DED_DONE=$(printf '%s' "$LOG" | grep -cE 'deduction: Completed in')
  IND_DONE=$(printf '%s' "$LOG" | grep -cE 'induction: Completed in')
  ERRS=$(printf '%s' "$LOG" | grep -ciE 'error|exception|traceback')

  # --- DB state after the dream ---
  DED_N=$(psql "SELECT count(1) FROM documents WHERE workspace_name='$WS' AND observer='$OBS' AND observed='$OBS' AND level='deductive'")
  IND_N=$(psql "SELECT count(1) FROM documents WHERE workspace_name='$WS' AND observer='$OBS' AND observed='$OBS' AND level='inductive'")
  HAS_CARD=$(psql "SELECT (internal_metadata ? 'peer_card') FROM peers WHERE workspace_name='$WS' AND name='$OBS'")
  CARD_LEN=$(psql "SELECT coalesce(length(internal_metadata->>'peer_card'),0) FROM peers WHERE workspace_name='$WS' AND name='$OBS'")

  # --- VRAM (resident size for this model) ---
  VRAM=$(ollama ps 2>/dev/null | awk -v m="$MODEL" '$1==m {print $3" "$4}')

  printf '{"model":"%s","trial":%s,"status":"%s","wall_s":%s,"dur_ms":%s,"run_id":"%s","ded_calls":%s,"ind_calls":%s,"card_calls":%s,"ded_done":%s,"ind_done":%s,"ded_n":%s,"ind_n":%s,"has_card":"%s","card_len":%s,"errors":%s,"vram":"%s","mark":"%s"}\n' \
    "$(jstr "$MODEL")" "$TRIAL" "$STATUS" "${WALL:-0}" "${DUR_MS:-0}" "$(jstr "${RUNID:-}")" \
    "${DED_CALLS:-0}" "${IND_CALLS:-0}" "${CARD_CALLS:-0}" "${DED_DONE:-0}" "${IND_DONE:-0}" \
    "${DED_N:-0}" "${IND_N:-0}" "${HAS_CARD:-f}" "${CARD_LEN:-0}" "${ERRS:-0}" "$(jstr "${VRAM:-}")" "$MARK" \
    | tee -a "$RESULTS"
  echo ">>> [$MODEL] trial $TRIAL $STATUS  ded=$DED_N ind=$IND_N card=$HAS_CARD dur=${DUR_MS}ms wall=${WALL}s" >&2
 done
done
echo "SWEEP_COMPLETE" >&2
