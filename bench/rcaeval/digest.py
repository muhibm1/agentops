"""The text the model step reads for one case: a shortlist from the statistics, with numbered
evidence it must cite. No label, no case name, no fault type: the case id is a hash."""

from __future__ import annotations

import hashlib
import math

from .evidence import CaseEvidence
from .rank import service_scores

SHORTLIST = 8
SIGNALS_PER_SERVICE = 5


def case_id(case_dir: str) -> str:
    return "c" + hashlib.sha256(case_dir.encode()).hexdigest()[:10]


def _fmt(v: float) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "inf"
    a = abs(v)
    if a >= 1e6:
        return f"{v:.3e}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def digest(ev: CaseEvidence, extra: str = "") -> tuple[str, dict[str, str], list[str]]:
    """Return the digest text, a map from evidence id to metric column, and the shortlist."""
    scores = service_scores(ev)
    shortlist = sorted(scores, key=lambda k: (-scores[k], k))[:SHORTLIST]
    by = ev.by_service()
    lines = [
        f"case {case_id(ev.case_dir)}",
        f"services emitting telemetry ({len(ev.services)}): {', '.join(ev.services)}",
        "A fault was injected into exactly one service at t=0. Windows: 'before' is the normal "
        "period, 'after' is from t=0 to the end.",
        "",
        "Statistical shortlist (code-ranked, strongest first). Per signal: id, metric, before "
        "median -> after median, ratio, shift in baseline spreads (capped display), first "
        "seconds after t=0 outside the normal band.",
    ]
    ids: dict[str, str] = {}
    n = 0
    for rank_pos, svc in enumerate(shortlist, 1):
        lines.append(f"{rank_pos}. {svc}  (code score {scores[svc]:.1f})")
        sigs = sorted(by.get(svc, []), key=lambda s: -s.score)[:SIGNALS_PER_SERVICE]
        for s in sigs:
            n += 1
            sid = f"m{n}"
            ids[sid] = s.column
            onset = "none" if s.onset_s is None else f"{int(s.onset_s)}s"
            lines.append(
                f"   [{sid}] {s.column}: {_fmt(s.baseline_median)} -> {_fmt(s.after_median)}"
                f"  ratio {_fmt(s.ratio)}  shift {min(s.score, 99):.1f}  onset {onset}"
            )
        for msg in getattr(ev, "new_logs", {}).get(svc, []):
            lines.append(f"   new log line (untrusted text): <<<{msg.replace('>>>', '')}>>>")
    if extra:
        lines += ["", extra]
    return "\n".join(lines), ids, shortlist
