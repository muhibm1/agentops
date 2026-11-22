#!/usr/bin/env bash
# Advance one live incident until it needs a model answer (exit 5) or ends (exit 0).
# Usage: live/drive.sh WORK ID [open]
set -u
cd "$(dirname "$0")/.."
export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
PY=../.venv/bin/python
if [ "${3:-}" = open ]; then $PY -m live.harness open --work "$1" --id "$2" 2>>"$1/$2.stderr" | cut -c1-400; fi
while true; do
  last=$(tail -1 "$1/$2/events.jsonl" | $PY -c "import json,sys;print(json.load(sys.stdin)['step'])")
  [ "$last" = ended ] && { echo "ENDED $2"; exit 0; }
  $PY -m live.harness advance --work "$1" --id "$2" 2>>"$1/$2.stderr" | cut -c1-400
  rc=${PIPESTATUS[0]}
  [ "$rc" = 5 ] && { echo "NEEDS_ANSWER"; exit 5; }
  case "$rc" in 0|3|4) ;; *) echo "FAILED rc=$rc"; exit "$rc";; esac
done
