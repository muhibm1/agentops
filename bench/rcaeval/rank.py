"""Deterministic service ranking from `CaseEvidence`: the statistics-only arm, and the shortlist
the model step chooses from."""

from __future__ import annotations

import math

from .evidence import CaseEvidence

# How much each kind of signal says about the service it is measured on, as opposed to its
# callers. Resource signals (cpu, mem, disk) are local to the container. Latency and errors
# propagate up the call chain, so a raised latency is weaker evidence that *this* service is the
# cause. Load is mostly traffic and moves with the callers.
KIND_WEIGHT = {"cpu": 1.0, "mem": 1.0, "disk": 1.0, "net": 0.8, "latency": 0.7, "error": 0.7, "load": 0.3}
# Robust shifts saturate near 20 (the spread floor is 5% of the series' range), so many services
# tie on a strong latency shift. A delay injected into one service raises every caller's latency
# by about the same absolute amount, but the injected service's own baseline is the smallest, so
# its *relative* rise is the largest. LATENCY_RATIO_WEIGHT adds log(after/before) for latency.
# Chosen on RE1 only (values 1 to 8 all within 0.04 AC@1 of each other); the middle value is used.
LATENCY_RATIO_WEIGHT = 2.0
SCORE_CAP = 20.0


def service_scores(ev: CaseEvidence, weights: dict[str, float] | None = None) -> dict[str, float]:
    w = weights or KIND_WEIGHT
    scores: dict[str, float] = {s: 0.0 for s in ev.services}
    for sig in ev.signals:
        # Resource metrics only count when they rise; a falling cpu is a symptom of less traffic.
        if sig.kind in ("cpu", "mem", "disk", "latency", "error") and sig.direction < 0:
            continue
        v = w.get(sig.kind, 0.5) * min(sig.score, SCORE_CAP)
        if sig.kind == "latency" and sig.ratio > 1 and math.isfinite(sig.ratio):
            v += LATENCY_RATIO_WEIGHT * math.log(sig.ratio)
        if v > scores.get(sig.service, 0.0):
            scores[sig.service] = v
    return scores


def rank(ev: CaseEvidence, weights: dict[str, float] | None = None) -> list[str]:
    s = service_scores(ev, weights)
    return sorted(s, key=lambda k: (-s[k], k))
