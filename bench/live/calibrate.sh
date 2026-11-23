#!/usr/bin/env bash
# No-fault calibration of live.fired and of verify's latency limit, under the exact restart
# protocol of a test incident: restart, rest, then judge as if paged (T) and, 390 to 570 s later,
# as if a change had been applied at T (verify's settle and observe windows). No flag is on.
# Usage: live/calibrate.sh OUT CYCLES
set -u
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
for c in $(seq 1 "$2"); do
  $PY -m live.inject reset
  docker restart -t 20 ad cart checkout currency email frontend image-provider payment \
    product-catalog quote recommendation shipping valkey-cart astronomy-db >/dev/null
  sleep 1080
  $PY -m live.fired gate --out "$1/gate.jsonl" >/dev/null
  sleep 210
  T=$(date +%s)
  $PY -m live.fired --at "$T" --out "$1/page.jsonl" >/dev/null
  sleep 600
  $PY -c "
import json
from live import edges, telemetry, stats
from live.source import LATENCY_RATIO, LATENCY_FLOOR_MS
T=$T; b0,b1=T-780,T-180; a0,a1=T+390,T+570
base=telemetry.instant_p99('frontend',b0,b1); now=telemetry.instant_p99('frontend',a0,a1)
limit=max(LATENCY_RATIO*base, base+LATENCY_FLOOR_MS)
bw,aw=edges.window(b0,b1),edges.window(a0,a1)
rises=[e.error_ratio-(bw.edges[k].error_ratio if k in bw.edges else 0) for k,e in aw.edges.items() if e.calls>=edges.MIN_CALLS]
dep=max((e.p99_ms or 0) for e in aw.edges.values() if e.calls>=edges.MIN_CALLS)
noise={(r.service,stats.template(r.body)) for r in telemetry.logs(b0,b1)}
errs=sorted({r.service+': '+stats.template(r.body)[:100] for r in telemetry.logs(a0,a1) if r.severity=='ERROR' and (r.service,stats.template(r.body)) not in noise})
v={'T':T,'new_error_logs':errs[:5],'logs_ok':not errs,'base_p99':base,'after_p99':now,'limit':limit,'latency_ok':now<=limit,'worst_error_rise':max(rises),'error_ok':max(rises)<=0.01,'worst_dep_p99':dep,'dep_ok':dep<=1000}
open('$1/after.jsonl','a').write(json.dumps(v)+'\n')"
  echo "cycle $c done"
done
