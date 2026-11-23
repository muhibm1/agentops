"""The statistics-only answer for an OpenRCA query: which component, which reason, and when.

Each reason in the system's vocabulary has a signature: the KPIs that should move, and in which
direction, when that fault hits a component (a node CPU fault raises the node's CPU use; a
container read I/O fault raises the container's file-system reads). A candidate is a
(component, reason) pair. Its strength is the strongest of its signature KPIs, valued as

    frozen RCAEval weight of the KPI's kind x min(robust shift, 20)
    + DOM_WEIGHT x log2(dominance)

The first term is RCAEval's rank value, unchanged (`rcaeval.rank.KIND_WEIGHT`, `SCORE_CAP`).
Shifts saturate at the cap for many series in a busy half hour, so the second term breaks the
ties: dominance is how far this window's excursion goes compared with the largest excursion in
any other half hour of the same day. A fault is usually the biggest thing that happens to its
own KPI that day; routine bursts are not.

Levels: a node's own KPIs make node candidates. Container KPIs make pod candidates; when two or
more pods of one service carry the same reason strongly, the service replaces its pods (the
benchmark answers "the service" when all its pods are faulty).

The occurrence time is the earliest sustained out-of-band minute among the chosen candidate's
strong signature KPIs, plus a fixed offset fitted on Market cloudbed-1 only.

The reason vocabulary is the answer format the benchmark declares for Market (OpenRCA's own
prompt, `rca/baseline/rca_agent/prompt/basic_prompt_Market.py`). Every constant marked "fitted"
was chosen on cloudbed-1 only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from rcaeval.rank import KIND_WEIGHT, SCORE_CAP

from .evidence import WEIGHT_KEY, Scored, service_of

REASONS = [
    "container CPU load",
    "container memory load",
    "container network packet retransmission",
    "container network packet corruption",
    "container network latency",
    "container packet loss",
    "container process termination",
    "container read I/O load",
    "container write I/O load",
    "node CPU load",
    "node CPU spike",
    "node memory consumption",
    "node disk read I/O consumption",
    "node disk write I/O consumption",
    "node disk space consumption",
]

# reason -> (level, [(KPI name pattern, direction)]). "node CPU spike" and "container packet
# loss" share their neighbours' signatures and are told apart afterwards.
SIGNATURES: dict[str, tuple[str, list[tuple[str, int]]]] = {
    "node CPU load": ("node", [(r"^system\.cpu\.(pct_usage|user)$", 1)]),
    "node memory consumption": ("node", [(r"^system\.mem\.(pct_usage|used|real\.used)$", 1)]),
    "node disk read I/O consumption": ("node", [(r"^system\.io\.(rkb_s|r_s)$", 1)]),
    "node disk write I/O consumption": ("node", [(r"^system\.io\.(w_s|w_await)$", 1)]),
    "node disk space consumption": ("node", [(r"^system\.disk\.(pct_usage|used)$", 1)]),
    "container CPU load": ("pod", [(r"^container_cpu_(usage|user)_seconds$", 1)]),
    "container memory load": (
        "pod",
        [(r"^container_memory_(rss|working_set_MB|usage_MB)$", 1)],
    ),
    "container read I/O load": ("pod", [(r"^container_fs_reads(_MB)?\./dev/vda$", 1)]),
    "container write I/O load": ("pod", [(r"^container_fs_writes(_MB)?\./dev/vda$", 1)]),
    "container network latency": (
        "pod",
        [(r"^trace_self_p50$", 1), (r"^mesh_rt\.", 1), (r"^proxy_mean_ms$", 1)],
    ),
    "container network packet corruption": (
        "pod",
        [(r"^(log_error_lines|mesh_bad_requests|proxy_bad_responses|trace_own_errors)$", 1)],
    ),
    "container network packet retransmission": (
        "pod",
        [(r"^container_network_(transmit|receive)_packets\.eth0$", 1)],
    ),
    "container process termination": (
        "pod",
        [(r"^container_threads$", -1), (r"^trace_spans$", -1)],
    ),
}

# Fitted on cloudbed-1.
DOM_WEIGHT = 2.0
HOT = 12.0  # a pod "carries" a reason when its candidate value reaches this
SERVICE_MIN_PODS = 2
STRONG_FRACTION = 0.6  # signature KPIs within this fraction of the best set the onset
SPIKE_MAX_MINUTES = 2  # node CPU out of band for at most this many minutes -> spike
TIME_OFFSET_S = 0  # added to the onset minute's timestamp
EARLY_MINUTES = 2  # a candidate whose onset is this close to the window start ...
EARLY_PENALTY = 6.0  # ... is likely the tail of an earlier fault and loses this much
ERRORS_OVER_LATENCY = 0.5  # corruption wins over latency at this fraction of its value
SPACE_DOMINANCE = 1.0  # used space beyond any other half hour of the day -> disk space
STRESSOR_DOMINANCE = 5.0  # CPU or memory this far beyond the rest of the day beats file I/O
REASON_PRIOR: dict[str, float] = {}


@dataclass
class Candidate:
    comp: str
    level: str
    reason: str
    value: float
    onset: int | None
    evidence: list[Scored] = field(default_factory=list)
    pods: list[str] = field(default_factory=list)


def signal_value(s: Scored, direction: int) -> float:
    base = KIND_WEIGHT.get(WEIGHT_KEY[s.kind], 0.5) * min(s.sig.score, SCORE_CAP)
    if s.sig.direction != direction:
        base = 0.0
    dom = s.dom_up if direction > 0 else s.dom_down
    return base + DOM_WEIGHT * math.log2(max(dom, 1 / 16))


def _compiled() -> list[tuple[str, str, re.Pattern, int]]:
    out = []
    for reason, (level, pats) in SIGNATURES.items():
        for pat, d in pats:
            out.append((reason, level, re.compile(pat), d))
    return out


_SIGS = _compiled()


def pod_candidates(scored: list[Scored]) -> dict[tuple[str, str], Candidate]:
    cands: dict[tuple[str, str], Candidate] = {}
    for s in scored:
        for reason, level, pat, d in _SIGS:
            if s.level != level or not pat.search(s.name):
                continue
            v = signal_value(s, d) + REASON_PRIOR.get(reason, 0.0)
            c = cands.get((s.comp, reason))
            if c is None:
                c = cands[(s.comp, reason)] = Candidate(s.comp, level, reason, -1e9, None)
            c.evidence.append(s)
            if v > c.value:
                c.value = v
    for c in cands.values():
        d = dict((r, dd) for r, _, _, dd in _SIGS)
        vals = [(signal_value(s, d.get(c.reason, 1)), s) for s in c.evidence]
        strong = [s for v, s in vals if v >= STRONG_FRACTION * c.value and s.onset_min]
        c.onset = min((s.onset_min for s in strong), default=None)
        c.evidence.sort(key=lambda s: -signal_value(s, d.get(c.reason, 1)))
    return cands


def all_candidates(scored: list[Scored], window_start: int | None = None) -> list[Candidate]:
    """Pod and node candidates, with services replacing pods that share a strong reason."""
    cands = pod_candidates(scored)
    if window_start is not None:
        for c in cands.values():
            if c.onset is None or c.onset < window_start + EARLY_MINUTES * 60:
                c.value -= EARLY_PENALTY
    by_service: dict[tuple[str, str], list[Candidate]] = {}
    for c in cands.values():
        if c.level == "pod":
            svc = service_of(c.comp)
            if svc:
                by_service.setdefault((svc, c.reason), []).append(c)
    out = list(cands.values())
    for (svc, reason), pods in by_service.items():
        hot = sorted((p for p in pods if p.value >= HOT), key=lambda p: -p.value)
        if len(hot) >= SERVICE_MIN_PODS:
            value = sum(p.value for p in hot[:2]) / 2
            onset = min((p.onset for p in hot if p.onset), default=None)
            ev = [e for p in hot for e in p.evidence[:2]]
            out.append(
                Candidate(svc, "service", reason, value + 0.5, onset, ev, [p.comp for p in hot])
            )
    out.sort(key=lambda c: (-c.value, c.comp, c.reason))
    return out


def _covers(a: Candidate, b: Candidate) -> bool:
    """True when the two candidates name the same component or a service and one of its pods."""
    if a.comp == b.comp:
        return True
    return (
        (a.level == "service" and b.comp in a.pods)
        or (b.level == "service" and a.comp in b.pods)
        or service_of(a.comp) == b.comp
        or service_of(b.comp) == a.comp
    )


def choose(cands: list[Candidate], k: int) -> list[Candidate]:
    picked: list[Candidate] = []
    for c in cands:
        if any(_covers(c, p) for p in picked):
            continue
        picked.append(c)
        if len(picked) == k:
            break
    return picked


def final_reason(c: Candidate, cands: list[Candidate] | None = None) -> str:
    """The reason to answer for a chosen candidate, after rules between look-alike reasons."""
    same = {o.reason: o for o in cands or [] if o.comp == c.comp}
    if c.reason == "node CPU load" and c.evidence:
        if c.evidence[0].out_minutes <= SPIKE_MAX_MINUTES:
            return "node CPU spike"
    if c.reason == "container network latency":
        # Errors alongside the slowdown point at a damaged link rather than a plain delay.
        err = same.get("container network packet corruption")
        if err is not None and err.value >= ERRORS_OVER_LATENCY * c.value:
            return err.reason
    if c.reason in ("node disk write I/O consumption", "node disk read I/O consumption"):
        # Filling a disk is write I/O too; the used-space KPI tells the two apart.
        space = same.get("node disk space consumption")
        if space is not None and any(s.dom_up >= SPACE_DOMINANCE for s in space.evidence):
            return space.reason
    if c.reason in ("container write I/O load", "container read I/O load"):
        # Starting a CPU or memory stressor writes and reads files too; a CPU or memory KPI
        # beyond anything else that day outranks the file-system burst.
        alt = [same[r] for r in ("container CPU load", "container memory load") if r in same]
        alt = [(max((s.dom_up for s in a.evidence), default=0.0), a) for a in alt]
        alt = [(d, a) for d, a in alt if d >= STRESSOR_DOMINANCE]
        if alt:
            return max(alt, key=lambda da: da[0])[1].reason
    return c.reason


def answer_time(c: Candidate, window_start: int) -> int:
    t = c.onset if c.onset is not None else window_start
    return t + TIME_OFFSET_S
