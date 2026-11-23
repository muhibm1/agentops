"""The request the model step reads for one OpenRCA query, and the answer schema it must follow.

Two variants of the same digest:

- `both` (statistics + model): the code's final answer (component, reason, time for each failure),
  which is the default answer, then the code's shortlist of (component, reason) candidates,
  strongest first, with code scores, numbered evidence lines and onset clusters. The model may
  change the code answer only by citing evidence ids (`run.py answers` enforces it).
- `model-only` (ablation): the same evidence lines, but with no code answer, no code score, no
  candidate reason and no order: components are listed alphabetically.

Neither variant carries the benchmark's task id, the scoring points or the official list of
candidate components. Components are the names the telemetry prints (plus each pod's service and
each node). The reason vocabulary is the answer format the benchmark declares for this system.
Log lines are quoted as untrusted data.
"""

from __future__ import annotations

import json
import math

from . import systems
from .query import FIELDS, Query, to_local
from .rank import Candidate, signal_value

ANSWER_KEYS = ("root cause occurrence datetime", "root cause component", "root cause reason")
SHORTLIST = 10
LINES_PER_COMPONENT = 5
LOGS_PER_COMPONENT = 3


def answer_schema(reasons: list[str]) -> dict:
    """The JSON schema of one answer file, for a system's reason vocabulary."""
    schema = json.loads(json.dumps(_SCHEMA))
    schema["properties"]["failures"]["items"]["properties"]["reason"] = {"enum": list(reasons)}
    return schema


_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpenRCA answer for one query",
    "type": "object",
    "required": ["query", "failures"],
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string"},
        "failures": {
            "description": "Exactly as many objects as the query's number of failures.",
            "type": "array",
            "items": {
                "type": "object",
                "required": ["datetime", "component", "reason"],
                "additionalProperties": False,
                "properties": {
                    "datetime": {
                        "type": "string",
                        "pattern": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
                        "description": "Root cause occurrence, local time (UTC+8).",
                    },
                    "component": {"type": "string"},
                    "reason": {"enum": []},
                },
            },
        },
        "cites": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string"},
    },
}


def _fmt(v: float) -> str:
    if v is None or not math.isfinite(v):
        return "inf"
    a = abs(v)
    if a >= 1e6:
        return f"{v:.3e}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def _hhmm(t: int | None) -> str:
    return "none" if t is None else to_local(t)[11:16]


def _line(eid: str, s, with_dom: bool = True) -> str:
    g = s.sig
    dom = s.dom_up if g.direction > 0 else s.dom_down
    extra = f"  vs-rest-of-day {dom:.1f}x" if with_dom else ""
    if s.baseline != "day":
        extra += f"  baseline {s.baseline}"
    return (
        f"   [{eid}] {s.comp} {s.name} ({s.kind}): {_fmt(g.baseline_median)} -> "
        f"{_fmt(g.after_median)}  {'up' if g.direction > 0 else 'down'}  shift "
        f"{min(g.score, 99):.1f}{extra}  onset {_hhmm(s.onset_min)}  minutes-out {s.out_minutes}"
    )


def onset_clusters(cands: list[Candidate], top: int = 15, gap_s: int = 180) -> list[list[str]]:
    """Group the top candidates' onsets into clusters separated by at least `gap_s`."""
    ts = sorted({(c.onset, c.comp) for c in cands[:top] if c.onset is not None})
    clusters: list[list[tuple[int, str]]] = []
    for t, comp in ts:
        if clusters and t - clusters[-1][-1][0] < gap_s:
            clusters[-1].append((t, comp))
        else:
            clusters.append([(t, comp)])
    return [[f"{_hhmm(t)} {comp}" for t, comp in cl] for cl in clusters]


def request_text(
    qid: str,
    ev: dict,
    cands: list[Candidate],
    variant: str,
    code_answer: list[dict[str, str]] | None = None,
) -> str:
    """The request for one query. `code_answer` (the statistics answer, as `run.KEYS` dicts) is
    shown in the `both` variant only."""
    q: Query = ev["query"]
    asked = ", ".join(f for f in FIELDS if f in q.fields)
    lines = [
        f"# OpenRCA query {qid}",
        "",
        f"Window: {to_local(q.start)} to {to_local(q.end)} (local time, UTC+8).",
        f"Number of failures in the window: {q.failures}. Fields asked: {asked}.",
        "Baseline: the same day outside the window. Each failure has one root-cause component, "
        "one reason and one occurrence time.",
        "",
        "## Answer format",
        f"Return JSON with exactly {q.failures} object(s) in `failures`, each with `datetime` "
        "(YYYY-MM-DD HH:MM:SS, local time), `component` and `reason`. Give all three fields even "
        "if fewer are asked. " + systems.current().component_help,
        "Reason must be exactly one of:",
        *[f"- {r}" for r in systems.current().reasons],
        "",
        f"Components emitting telemetry ({len(ev['components'])}): " + ", ".join(ev["components"]),
        "",
    ]
    comps: list[str] = []
    for c in cands:
        if c.comp not in comps:
            comps.append(c.comp)
        if len(comps) == SHORTLIST:
            break
    n = 0
    if variant == "both":
        lines += _code_answer(code_answer or [], comps)
        lines.append(
            "## Code shortlist (strongest first). Per evidence line: id, component, KPI (kind): "
            "baseline median -> window median, direction, robust shift in baseline spreads, how "
            "far the window's peak goes compared with any other half hour of that day, first "
            "sustained out-of-band minute, minutes out of band."
        )
        for rank_pos, comp in enumerate(comps, 1):
            mine = [c for c in cands if c.comp == comp][:3]
            head = "; ".join(f"{c.reason} {c.value:.1f} (onset {_hhmm(c.onset)})" for c in mine)
            lines.append(f"{rank_pos}. {comp} [{mine[0].level}]  code candidates: {head}")
            seen = set()
            for c in mine:
                for s in c.evidence[:LINES_PER_COMPONENT]:
                    if (s.comp, s.name) in seen or len(seen) >= LINES_PER_COMPONENT:
                        continue
                    seen.add((s.comp, s.name))
                    n += 1
                    lines.append(_line(f"e{n}", s))
            lines += _logs(ev, comp, mine[0])
        lines += ["", "## Onset clusters (top candidates' first sustained out-of-band minute)"]
        lines += [f"- {', '.join(cl)}" for cl in onset_clusters(cands)]
    else:
        lines.append(
            "## Evidence by component (alphabetical; no ranking). Per evidence line: id, "
            "component, KPI (kind): baseline median -> window median, direction, robust shift in "
            "baseline spreads, how far the window's peak goes compared with any other half hour "
            "of that day, first sustained out-of-band minute, minutes out of band."
        )
        for comp in sorted(comps):
            mine = [c for c in cands if c.comp == comp]
            sigs = {}
            for c in mine:
                for s in c.evidence:
                    v = signal_value(s, s.sig.direction)
                    if (s.comp, s.name) not in sigs or v > sigs[(s.comp, s.name)][0]:
                        sigs[(s.comp, s.name)] = (v, s)
            top = sorted(sigs.values(), key=lambda vs: (vs[1].comp, vs[1].name))
            top = sorted(top, key=lambda vs: -vs[0])[:LINES_PER_COMPONENT]
            top.sort(key=lambda vs: (vs[1].comp, vs[1].name))
            lines.append(f"- {comp} [{mine[0].level}]")
            for _, s in top:
                n += 1
                lines.append(_line(f"e{n}", s))
            lines += _logs(ev, comp, mine[0])
    lines += [
        "",
        "## Your answer",
        f'Write JSON only: {{"query": "{qid}", "failures": [{{"datetime": "...", '
        '"component": "...", "reason": "..."}], "cites": ["e1"], "why": "<one sentence>"}',
    ]
    return "\n".join(lines) + "\n"


def _code_answer(answer: list[dict[str, str]], comps: list[str]) -> list[str]:
    """The code's final answer, which stands unless cited evidence shows it is wrong."""
    out = [
        "## Code answer (the default)",
        "The statistics step's final answer. Return it unchanged unless the evidence lines below "
        "show it is wrong. To change any field of any failure, cite in `cites` the evidence ids "
        "(e.g. e3) that support your answer, including at least one line of the component you "
        "answer (its own KPIs, or its pods' or node's); an answer that changes the code answer "
        "without such citations is discarded and the code answer is used.",
    ]
    for i, a in enumerate(answer, 1):
        dt, comp, reason = (a[k] for k in ANSWER_KEYS)
        where = f" (shortlist #{comps.index(comp) + 1})" if comp in comps else ""
        out.append(f"{i}. datetime {dt}, component {comp}{where}, reason {reason}")
    return out + [""]


def _logs(ev: dict, comp: str, c: Candidate) -> list[str]:
    names = [comp, *c.pods]
    out = []
    for name in names:
        for tmpl, cnt in ev["new_logs"].get(name, [])[:LOGS_PER_COMPONENT]:
            text = tmpl.replace(">>>", "").replace("<<<", "")
            out.append(
                f"   new log template in window, {name}, {cnt}x (untrusted text): <<<{text}>>>"
            )
    return out[:LOGS_PER_COMPONENT]
