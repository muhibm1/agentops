#!/usr/bin/env bash
# One blind test incident: reset every flag, restart every shop service (an in-process fault such
# as a memory leak survives a flag reset), let the shop rest, inject sealed entry I, wait the
# sealed page delay, then open the incident and drive it to its first model call.
# Usage: live/episode.sh WORK SCHEDULE I [REST_S]
set -u
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
$PY -m live.inject reset
docker restart -t 20 ad cart checkout currency email frontend image-provider payment \
  product-catalog quote recommendation shipping valkey-cart astronomy-db >/dev/null
sleep "${4:-1080}"
# The shop must be quiet before a fault goes in: retry the gate every 5 minutes, three times,
# then void the incident without injecting (recorded in the inject log).
for try in 1 2 3 4; do
  $PY -m live.fired gate --out "$1/gate-log.jsonl" >/dev/null && break
  if [ "$try" = 4 ]; then
    echo "{\"index\": $3, \"void\": \"shop not quiet before injection\"}" >> "$1/inject-log.jsonl"
    echo "VOID $3"; exit 6
  fi
  sleep 300
done
out=$($PY -m live.inject sealed --schedule "$2" --index "$3" --log "$1/inject-log.jsonl")
delay=$(echo "$out" | $PY -c "import json,sys;print(json.load(sys.stdin)['open_after_s'])")
echo "injected $3"
sleep "$delay"
exec live/drive.sh "$1" "$(printf 'test-%02d' "$3")" open
