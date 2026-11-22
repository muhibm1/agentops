"""The RCAEval statistics, applied to the live shop's telemetry.

The scoring is the frozen RCAEval method (`bench/rcaeval/evidence.py` and `rank.py`): the same
robust spread, the same shift score and the same per-kind weights. Only the input differs: rows
are Prometheus steps (15 s) rather than 1 s samples, so the post-window smoothing is 4 rows, and
the column-to-service map is given explicitly because the shop names its client-side error
series `<service>_clienterror`. Nothing here knows which flag is on.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np
import pandas as pd

from rcaeval.evidence import CaseEvidence, MetricSignal, _robust
from rcaeval.rank import service_scores

from .telemetry import SERVICES, LogRow

KINDS = {"load": "load", "error": "error", "clienterror": "error", "latency": "latency",
         "cpu": "cpu", "mem": "mem", "net": "net"}
SMOOTH_ROWS = 4
MIN_ROWS_BEFORE = 10
MIN_ROWS_AFTER = 4


def column_kind(column: str) -> tuple[str, str] | None:
    for svc in sorted(SERVICES, key=len, reverse=True):
        if column.startswith(svc + "_"):
            kind = KINDS.get(column[len(svc) + 1:])
            return (svc, kind) if kind else None
    return None


def evidence(df: pd.DataFrame, split: float, label: str = "live") -> CaseEvidence:
    """Signals for every shop series: rows before `split` are the normal period, rows from
    `split` on are the incident."""
    t = df["time"].to_numpy(dtype=float)
    before, after = t < split, t >= split
    signals: list[MetricSignal] = []
    services: set[str] = set()
    for col in df.columns:
        parsed = column_kind(col) if col != "time" else None
        if parsed is None:
            continue
        service, kind = parsed
        services.add(service)
        x = pd.to_numeric(df[col], errors="coerce").ffill().bfill().to_numpy(dtype=float)
        if np.all(np.isnan(x)):
            continue
        xb, xa = x[before], x[after]
        if len(xb) < MIN_ROWS_BEFORE or len(xa) < MIN_ROWS_AFTER:
            continue
        med, spread = _robust(xb, x)
        xs = pd.Series(xa).rolling(SMOOTH_ROWS, min_periods=1).mean().to_numpy()
        after_med = float(np.nanmedian(xa))
        sustained = abs(after_med - med) / spread
        peak = float(np.nanmax(np.abs(xs - med))) / spread
        score = max(sustained, 0.5 * peak)
        direction = 1 if (after_med - med) >= 0 else -1
        if sustained < 0.5 * peak:
            direction = 1 if float(np.nanmax(xs - med)) >= abs(float(np.nanmin(xs - med))) else -1
        out_band = np.where(np.abs(xs - med) > 3 * spread)[0]
        onset = float(t[after][out_band[0]] - split) if len(out_band) else None
        ratio = after_med / med if abs(med) > 1e-9 else math.inf if after_med > 0 else 1.0
        signals.append(MetricSignal(col, service, kind, med, spread, after_med, score, direction, onset, ratio))
    return CaseEvidence(label, sorted(services), int(split), signals)


def ranked(ev: CaseEvidence) -> list[tuple[str, float]]:
    s = service_scores(ev)
    return sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))


_VARIABLE = re.compile(r"0x[0-9a-f]+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{16,}|\d+(\.\d+)?", re.I)


def template(body: str) -> str:
    """A log line with ids and numbers blanked, so repeats of one message group together."""
    return _VARIABLE.sub("#", body)[:160]


def new_log_lines(before: list[LogRow], after: list[LogRow], per_service: int = 3) -> dict[str, list[str]]:
    """Per service, up to `per_service` WARN-or-worse messages whose pattern never appeared in
    the normal period, most frequent first."""
    seen = {(r.service, template(r.body)) for r in before}
    counts: dict[str, Counter[str]] = {}
    example: dict[tuple[str, str], str] = {}
    for r in after:
        key = (r.service, template(r.body))
        if key in seen:
            continue
        counts.setdefault(r.service, Counter())[key[1]] += 1
        example.setdefault(key, r.body)
    return {
        svc: [example[(svc, tpl)][:200] for tpl, _ in c.most_common(per_service)]
        for svc, c in counts.items()
    }
