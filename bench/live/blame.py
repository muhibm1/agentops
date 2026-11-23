"""Per-service health for the live shop: the frozen RCAEval scores, corrected along call edges.

A caller shows the errors and the waiting of the services it calls, so on its own the RCAEval
ranking blames the caller when a callee fails quietly. Here a caller's propagating signals
(server errors, latency, client errors) move to a callee only when the call edge between them is
impaired and the callee corroborates it on its own: its server error or latency shifted, it
stopped serving calls it used to serve (vanished), or its container restarted. Otherwise blame
stays where it is. Resource signals (cpu, memory, network, load) never move.

Thresholds were fixed before any replay and are not tuned: 3 calls, 10% served, a 5-point error
ratio rise, a 3x and +200 ms p99 rise, 0.25 for an explained caller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from rcaeval.evidence import CaseEvidence
from rcaeval.rank import KIND_WEIGHT, LATENCY_RATIO_WEIGHT, SCORE_CAP

from .edges import MIN_CALLS, Edge, Window

PROPAGATING = {"error", "latency"}
EXPLAINED_CALLER = 0.25
DEGRADED_SCORE = 5.0
SERVED_FRACTION = 0.10


@dataclass
class Health:
    service: str
    score: float
    status: str
    local: float
    propagating: float
    reasons: list[str] = field(default_factory=list)


def signal_value(sig) -> float:
    """One signal's contribution, exactly as `rcaeval.rank.service_scores` counts it."""
    if sig.kind in ("cpu", "mem", "disk", "latency", "error") and sig.direction < 0:
        return 0.0
    v = KIND_WEIGHT.get(sig.kind, 0.5) * min(sig.score, SCORE_CAP)
    if sig.kind == "latency" and sig.ratio > 1 and math.isfinite(sig.ratio):
        v += LATENCY_RATIO_WEIGHT * math.log(sig.ratio)
    return v


def impaired(after: Edge, before: Edge | None) -> str | None:
    """Why a call edge is worse than before, or None."""
    if after.calls < MIN_CALLS:
        return None
    b_ratio = before.error_ratio if before else 0.0
    if after.error_ratio >= max(0.05, b_ratio + 0.05):
        return f"{after.errors:.0f} of {after.calls:.0f} calls failed (before: {b_ratio:.0%})"
    b_p99 = before.p99_ms if before and before.p99_ms else None
    if after.p99_ms and b_p99 and after.p99_ms > max(3 * b_p99, b_p99 + 200):
        return f"p99 {after.p99_ms:.0f} ms (before {b_p99:.0f} ms)"
    return None


def analyse(ev: CaseEvidence, before: Window, after: Window, after_s: float, before_s: float) -> list[Health]:
    by = ev.by_service()
    services = sorted(set(ev.services) | {s for e in after.edges for s in e})
    local: dict[str, float] = {}
    prop: dict[str, float] = {}
    own_server_shift: dict[str, float] = {}
    for s in services:
        sigs = by.get(s, [])
        local[s] = max((signal_value(g) for g in sigs if g.kind not in PROPAGATING), default=0.0)
        prop[s] = max((signal_value(g) for g in sigs if g.kind in PROPAGATING), default=0.0)
        own_server_shift[s] = max(
            (signal_value(g) for g in sigs if g.kind in PROPAGATING and not g.column.endswith("_clienterror")),
            default=0.0,
        )
    reasons: dict[str, list[str]] = {s: [] for s in services}

    inbound: dict[str, float] = {}
    for (c, x), e in after.edges.items():
        inbound[x] = inbound.get(x, 0.0) + e.calls
    inbound_errors: dict[str, float] = {}
    for (c, x), e in after.edges.items():
        inbound_errors[x] = inbound_errors.get(x, 0.0) + e.errors
    # Vanished: callers still try and fail, but the service now serves under 10% of the calls it
    # served at its normal rate (a dead upstream would stop the attempts, not fail them).
    vanished = {
        x for x in services
        if (expected := before.served.get(x, 0.0) * after_s / max(before_s, 1.0)) >= MIN_CALLS
        and inbound_errors.get(x, 0.0) > 0
        and after.served.get(x, 0.0) < SERVED_FRACTION * max(expected, inbound.get(x, 0.0))
    }
    restarted = {
        x for x in services
        if after.restarts.get(x, 0.0) > 0 or (x in after.uptime and after.uptime[x] < after_s)
    }
    for x in vanished:
        reasons[x].append(f"received {after.served.get(x, 0):.0f} calls while callers attempted {inbound[x]:.0f}")
    for x in restarted:
        reasons[x].append(f"container restarted ({after.restarts.get(x, 0):.0f} resets; up {after.uptime.get(x, 0):.0f} s)")

    # Counted, not median-of-rates: the share of a service's own server calls that failed, against
    # its share before. Rates lag a fault by their window; counts do not.
    def err_share(w: Window, x: str) -> float:
        served = w.served.get(x, 0.0)
        return w.server_errors.get(x, 0.0) / served if served else 0.0

    error_rise = {
        x: err_share(after, x) - err_share(before, x)
        for x in services
        if after.server_errors.get(x, 0.0) >= MIN_CALLS
    }
    for x, rise in error_rise.items():
        if rise >= 0.05:
            reasons[x].append(f"{after.server_errors[x]:.0f} of its {after.served[x]:.0f} served calls failed ({rise:+.0%} on before)")

    def corroborated(x: str) -> bool:
        return (
            x in vanished or x in restarted or own_server_shift.get(x, 0.0) >= DEGRADED_SCORE
            or error_rise.get(x, 0.0) >= 0.05
        )

    score = {s: max(local[s], prop[s]) for s in services}
    for x in vanished | restarted:
        score[x] = max(score[x], SCORE_CAP)
    for x, rise in error_rise.items():
        if rise >= 0.05:
            # one point per percentage point of failed calls, at the error kind's weight
            own = KIND_WEIGHT["error"] * min(SCORE_CAP, 100 * rise)
            prop[x] = max(prop[x], own)
            score[x] = max(score[x], own)
    explained: dict[str, list[str]] = {}
    bad_edges: dict[tuple[str, str], str] = {}
    for key, e in after.edges.items():
        why = impaired(e, before.edges.get(key))
        if why is None and key[1] in vanished and e.errors > 0:
            why = f"{e.calls:.0f} calls, callee received almost none"
        if why:
            bad_edges[key] = why
    # Move blame down impaired edges to corroborating callees, deepest last; a few passes reach
    # the end of any chain in this shop's depth.
    received: dict[str, float] = {}
    for _ in range(4):
        changed = False
        for (c, x), why in bad_edges.items():
            if not corroborated(x):
                continue
            carried = max(prop[c], received.get(c, 0.0))
            received[x] = max(received.get(x, 0.0), carried)
            if score[x] < carried:
                score[x] = carried
                changed = True
            if c not in explained:
                explained[c] = []
                changed = True
            if x not in explained[c]:
                explained[c].append(x)
            new_c = max(local[c], EXPLAINED_CALLER * prop[c])
            if score[c] != new_c and not (c in vanished or c in restarted):
                score[c] = new_c
                changed = True
        if not changed:
            break
    for (c, x), why in bad_edges.items():
        reasons[c].append(f"calls to {x}: {why}")
    for c, xs in explained.items():
        reasons[c].append("its errors and latency are explained by calls to " + ", ".join(sorted(xs)))

    out = []
    for s in services:
        served = after.served.get(s, 0.0)
        own_err = after.server_errors.get(s, 0.0) / served if served else 0.0
        inbound_bad = any(x == s and after.edges[(c, x)].error_ratio >= 0.05 for (c, x) in bad_edges)
        if s in vanished or s in restarted or (own_err >= 0.5 and s not in explained):
            status = "down"
        elif score[s] >= DEGRADED_SCORE or inbound_bad or s in explained:
            status = "degraded"
        else:
            status = "healthy"
        out.append(Health(s, score[s], status, local[s], prop[s], reasons[s]))
    # Equal scores (common when two services hit the score cap) are ordered by how many failed
    # server calls each served: the larger failure volume is the likelier origin.
    return sorted(out, key=lambda h: (-round(h.score, 1), -after.server_errors.get(h.service, 0.0), h.service))
