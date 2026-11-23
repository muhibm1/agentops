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

Levels: a node's own KPIs make node candidates. Container KPIs make pod candidates; a host's
network receive-error counter marks packet corruption on its strongest pod. When two or more pods
of one service are hot with onsets together, under any reason, the service replaces its pods and
outranks the best of them (the benchmark answers "the service" when all its pods are faulty).

The occurrence time is the earliest sustained out-of-band minute among the chosen component's
strong KPIs of any kind, plus a fixed offset fitted on Market cloudbed-1 only.

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

# Host protocol counter kind -> the pod-level network reason it marks, in order of precedence.
# Retransmissions rising without receive errors as "container packet loss" was measured and
# dropped: it relabelled latency faults (cloudbed-1 reason 27 -> 26 of 55).
NET_MARKERS = {
    "net-rx-error": "container network packet corruption",
}

# Fitted on cloudbed-1.
DOM_WEIGHT = 2.0
HOT = 12.0  # a pod "carries" a reason when its candidate value reaches this
SERVICE_MIN_PODS = 2
SAME_ONSET_S = 60  # pods whose hot onsets are this close are hot together (0-120 s all equal)
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
SIGNATURE_REASONS = set(REASONS)


def pod_candidates(scored: list[Scored]) -> dict[tuple[str, str], Candidate]:
    """One candidate per (component, reason). Each series may be scored against two baselines
    (the day, and the hours before the window); a candidate keeps the stronger score per series."""
    cands: dict[tuple[str, str], Candidate] = {}
    best: dict[tuple[str, str], dict[str, tuple[float, Scored]]] = {}
    for s in scored:
        for reason, level, pat, d in _SIGS:
            if s.level != level or not pat.search(s.name):
                continue
            v = signal_value(s, d) + REASON_PRIOR.get(reason, 0.0)
            per = best.setdefault((s.comp, reason), {})
            if s.name not in per or v > per[s.name][0]:
                per[s.name] = (v, s)
    for (comp, reason), per in best.items():
        level = SIGNATURES[reason][0]
        vals = sorted(per.values(), key=lambda vs: -vs[0])
        c = Candidate(comp, level, reason, vals[0][0], None, [s for _, s in vals])
        strong = [s for v, s in vals if v >= STRONG_FRACTION * c.value and s.onset_min]
        c.onset = min((s.onset_min for s in strong), default=None)
        cands[(comp, reason)] = c
    return cands


def network_candidates(
    cands: dict[tuple[str, str], Candidate], scored: list[Scored], host: dict[str, str]
) -> list[Candidate]:
    """A network fault on a pod shows on its host's protocol counters, which no pod KPI carries:
    receive errors rising mark corruption, retransmissions rising without receive errors mark
    loss (`NET_MARKERS`). Each marked node's fault is attributed to the pod on that node whose own
    signals are strongest; that pod gets the marker's reason, valued at its strongest signal."""
    marks: dict[str, dict[str, Scored]] = {}
    for s in scored:
        if s.level != "node" or s.kind not in NET_MARKERS or s.onset_min is None:
            continue
        if s.sig.direction <= 0 or NET_MARKERS[s.kind] not in SIGNATURE_REASONS:
            continue
        mine = marks.setdefault(s.comp, {})
        if s.kind not in mine or s.sig.score > mine[s.kind].sig.score:
            mine[s.kind] = s
    out = []
    for node, mine in sorted(marks.items()):
        kind = next((k for k in NET_MARKERS if k in mine), None)
        if kind is None:
            continue
        pods = {p for p, n in host.items() if n == node}
        own = [c for c in cands.values() if c.comp in pods]
        if not own:
            continue
        lead = max(own, key=lambda c: (c.value, c.comp))
        mark = mine[kind]
        out.append(
            Candidate(
                lead.comp,
                "pod",
                NET_MARKERS[kind],
                lead.value + 0.5,
                lead.onset,
                [mark, *lead.evidence],
            )
        )
    return out


def all_candidates(
    scored: list[Scored], window_start: int | None = None, host: dict[str, str] | None = None
) -> list[Candidate]:
    """Pod and node candidates, network faults read from host counters, and services replacing
    pods that are hot together."""
    cands = pod_candidates(scored)
    if window_start is not None:
        for c in cands.values():
            if c.onset is None or c.onset < window_start + EARLY_MINUTES * 60:
                c.value -= EARLY_PENALTY
    if host:
        for c in network_candidates(cands, scored, host):
            cands[(c.comp, c.reason)] = c
    out = list(cands.values())
    out += service_candidates(cands.values())
    out.sort(key=lambda c: (-c.value, c.comp, c.reason))
    return out


def service_candidates(cands) -> list[Candidate]:
    """A service replaces its pods when two or more of them are hot with onsets at most
    `SAME_ONSET_S` apart, under any reason: the benchmark answers the service when all its pods
    are faulty, and a fault that hits every pod starts on all of them together. The service
    takes the strongest of those pods' reasons and outranks that pod."""
    by_service: dict[str, list[Candidate]] = {}
    for c in cands:
        if c.level == "pod" and c.value >= HOT and c.onset is not None:
            svc = service_of(c.comp)
            if svc:
                by_service.setdefault(svc, []).append(c)
    out = []
    for svc, hot in by_service.items():
        hot.sort(key=lambda c: (-c.value, c.comp, c.reason))
        best: Candidate | None = None
        pods: list[str] = []
        for lead in hot:
            group = [o for o in hot if abs(o.onset - lead.onset) <= SAME_ONSET_S]
            names = sorted({o.comp for o in group})
            if len(names) >= SERVICE_MIN_PODS:
                best, pods = lead, names
                break
        if best is None:
            continue
        group = [o for o in hot if o.comp in pods and abs(o.onset - best.onset) <= SAME_ONSET_S]
        onset = min(o.onset for o in group)
        ev = [e for o in group for e in o.evidence[:2]]
        out.append(Candidate(svc, "service", best.reason, best.value + 0.5, onset, ev, pods))
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


def component_onset(c: Candidate, scored: list[Scored]) -> int | None:
    """The earliest sustained out-of-band minute among the chosen component's strong KPIs: every
    series of the component (a service's own and its pods'), of any kind, valued in its own
    direction, within `STRONG_FRACTION` of the component's strongest."""
    names = {c.comp, *c.pods}
    best: dict[tuple[str, str], tuple[float, Scored]] = {}
    for s in scored:
        if s.comp not in names:
            continue
        v = signal_value(s, s.sig.direction)
        k = (s.comp, s.name)
        if k not in best or v > best[k][0]:
            best[k] = (v, s)
    if not best:
        return c.onset
    top = max(v for v, _ in best.values())
    strong = [s.onset_min for v, s in best.values() if v >= STRONG_FRACTION * top and s.onset_min]
    return min(strong, default=c.onset)


def answer_time(c: Candidate, window_start: int, scored: list[Scored] | None = None) -> int:
    t = component_onset(c, scored) if scored else c.onset
    t = t if t is not None else window_start
    return t + TIME_OFFSET_S
