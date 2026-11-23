#!/usr/bin/env bash
# One blind test incident: reset every flag, let the shop rest, inject sealed entry I, wait the
# sealed page delay, then open the incident and drive it to its first model call.
# Usage: live/episode.sh WORK SCHEDULE I [REST_S]
set -u
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
$PY -m live.inject reset
sleep "${4:-600}"
out=$($PY -m live.inject sealed --schedule "$2" --index "$3" --log "$1/inject-log.jsonl")
delay=$(echo "$out" | $PY -c "import json,sys;print(json.load(sys.stdin)['open_after_s'])")
echo "injected $3"
sleep "$delay"
exec live/drive.sh "$1" "$(printf 'test-%02d' "$3")" open
