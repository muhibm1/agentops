"""Did the shop show a customer-visible symptom? The same rule verify applies after a change,
applied to a window against the pre-incident baseline before it.

    python -m live.fired --at T [--out F]   # the 180 s before T against the 600 s before that
    python -m live.fired gate               # before an injection: exit 0 when the shop is quiet

Used three ways, all fixed before blind test 3:
- at the page, by `harness open`, before the agent runs: the verdict is written to
  W/<id>/fired.json and nothing the agent does can change it;
- before each injection, by `episode.sh`: the shop must be quiet, else the incident is void;
- on idle cycles with no fault, to measure how often the rule fires on nothing.

A symptom is any of the checks verify uses (src/agentops/nodes/verify.py, ADR 0003), with the
latency check relative to baseline as in `source.OtelDemoSource.latency_over_limit`:
- error: a call edge's error ratio, or the storefront's own server error ratio, more than 0.01
  above its baseline level;
- latency: the storefront's p99 over max(1.5 x baseline p99, baseline p99 + 25 ms);
- dependency: any call edge's p99 over 1000 ms;
- logs: an ERROR line whose pattern did not occur in the baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import edges, stats, telemetry
from .source import LATENCY_FLOOR_MS, LATENCY_RATIO

PAGED = "frontend"
ERROR_RISE = 0.01
DEPENDENCY_P99_MS = 1000.0


def symptoms(at: float, recent_s: float = 180.0, baseline_s: float = 600.0) -> dict:
    """Judge [at - recent_s, at] against [at - recent_s - baseline_s, at - recent_s]."""
    b0, b1, w0 = at - recent_s - baseline_s, at - recent_s, at - recent_s
    before, now = edges.window(b0, b1), edges.window(w0, at)
    rises = {}
    for k, e in now.edges.items():
        if e.calls >= edges.MIN_CALLS:
            rise = e.error_ratio - (before.edges[k].error_ratio if k in before.edges else 0.0)
            if rise > ERROR_RISE:
                rises[f"{k[0]}->{k[1]}"] = round(rise, 4)
    served_b, served_n = before.served.get(PAGED, 0.0), now.served.get(PAGED, 0.0)
    own_b = before.server_errors.get(PAGED, 0.0) / served_b if served_b else 0.0
    own_n = now.server_errors.get(PAGED, 0.0) / served_n if served_n else 0.0
    if own_n - own_b > ERROR_RISE:
        rises[PAGED] = round(own_n - own_b, 4)
    base_p99 = telemetry.instant_p99(PAGED, b0, b1)
    p99 = telemetry.instant_p99(PAGED, w0, at)
    limit = (
        max(LATENCY_RATIO * base_p99, base_p99 + LATENCY_FLOOR_MS) if base_p99 is not None else None
    )
    slow = p99 is not None and limit is not None and p99 > limit
    deps = {
        f"{k[0]}->{k[1]}": round(e.p99_ms)
        for k, e in now.edges.items()
        if e.calls >= edges.MIN_CALLS and (e.p99_ms or 0.0) > DEPENDENCY_P99_MS
    }
    noise = {(r.service, stats.template(r.body)) for r in telemetry.logs(b0, b1)}
    errors = sorted(
        {
            f"{r.service}: {stats.template(r.body)[:120]}"
            for r in telemetry.logs(w0, at)
            if r.severity == "ERROR" and (r.service, stats.template(r.body)) not in noise
        }
    )
    checks = {
        "error": bool(rises),
        "latency": bool(slow),
        "dependency": bool(deps),
        "logs": bool(errors),
    }
    return {
        "at": at,
        "fired": any(checks.values()),
        "checks": checks,
        "error_rises": rises,
        "storefront_p99_ms": None if p99 is None else round(p99, 1),
        "storefront_baseline_p99_ms": None if base_p99 is None else round(base_p99, 1),
        "storefront_limit_ms": None if limit is None else round(limit, 1),
        "slow_dependencies": deps,
        "new_error_logs": errors[:10],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", nargs="?", default="judge", choices=["judge", "gate"])
    p.add_argument("--at", type=float, default=None)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    verdict = symptoms(a.at if a.at is not None else time.time())
    line = json.dumps(verdict)
    if a.out:
        with open(Path(a.out), "a") as f:
            f.write(line + "\n")
    print(line)
    if a.cmd == "gate":
        sys.exit(1 if verdict["fired"] else 0)


if __name__ == "__main__":
    main()
