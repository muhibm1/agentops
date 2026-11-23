"""Turn one RCAEval case into evidence AgentOps can reason over, without ever reading its label.

A case is a folder of per-second metrics (and, from RE2 on, logs and traces) around one injected
fault. The folder name and the benchmark's case index both contain the answer, so this module
takes a path, reads only `metrics.parquet`, `logs.parquet`, `traces.parquet` and
`inject_time.txt`, and never looks at the name. The candidate set is every service that emits
telemetry; there is no list of "services that tend to be faulted", by design (a no-telemetry
prior over the five injected services already scores Avg@5 0.49 on RE1, arXiv 2609.27069).

Everything here is deterministic statistics. The model step reads the digest this produces.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# Metric column suffixes as they appear across the three systems, mapped to one vocabulary.
KIND_PATTERNS: list[tuple[str, str]] = [
    (r"_cpu$", "cpu"),
    (r"_mem$", "mem"),
    (r"_(load|workload)$", "load"),
    (r"_latency(-\d+)?$", "latency"),
    (r"_error$", "error"),
    (r"_(diskio|disk)$", "disk"),
    (r"_(net\w*|socket\w*)$", "net"),
]
# Pseudo-services that are infrastructure or traffic sources, not deployable services.
NOT_A_SERVICE = re.compile(r"^(PassthroughCluster|main|frontend-external|istio.*|node.*)$")


@dataclass
class MetricSignal:
    column: str
    service: str
    kind: str
    baseline_median: float
    baseline_spread: float
    after_median: float
    score: float  # robust shift of the post-injection level, in baseline spreads
    direction: int  # +1 rose, -1 fell
    onset_s: float | None  # seconds after injection when it first left the normal band
    ratio: float  # after_median / baseline_median, when the baseline is not ~0


@dataclass
class CaseEvidence:
    case_dir: str
    services: list[str]
    inject_time: int
    signals: list[MetricSignal] = field(default_factory=list)
    # Up to three log messages per service that first appeared after injection (untrusted text).
    new_logs: dict[str, list[str]] = field(default_factory=dict)

    def by_service(self) -> dict[str, list[MetricSignal]]:
        out: dict[str, list[MetricSignal]] = {s: [] for s in self.services}
        for sig in self.signals:
            out.setdefault(sig.service, []).append(sig)
        return out


def classify(column: str) -> tuple[str, str] | None:
    for pattern, kind in KIND_PATTERNS:
        m = re.search(pattern, column)
        if m:
            service = column[: m.start()]
            if not service or NOT_A_SERVICE.match(service):
                return None
            return service, kind
    return None


def _robust(x: np.ndarray, whole: np.ndarray) -> tuple[float, float]:
    med = float(np.nanmedian(x))
    q75, q25 = np.nanpercentile(x, [75, 25])
    iqr = float(q75 - q25)
    mad = float(np.nanmedian(np.abs(x - med))) * 1.4826
    # A flat or mostly-zero baseline (a sparse request counter) has IQR and MAD of 0, which
    # would make any wiggle look infinite. Floor the spread by the baseline's standard
    # deviation, by 1% of its level, and by 5% of the whole series' range, so a series has to
    # move by a visible fraction of its own scale to score.
    std = float(np.nanstd(x))
    rng = float(np.nanmax(whole) - np.nanmin(whole))
    spread = max(iqr, mad, std, abs(med) * 0.01, rng * 0.05, 1e-9)
    return med, spread


def load_metrics(case_dir: Path) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(case_dir / "metrics.parquet")
    inject = int(float((case_dir / "inject_time.txt").read_text().strip()))
    df = df.sort_values("time").reset_index(drop=True)
    return df, inject


def extract(case_dir: Path, smooth_s: int = 30) -> CaseEvidence:
    df, inject = load_metrics(case_dir)
    t = df["time"].to_numpy()
    before = t < inject
    after = t >= inject
    signals: list[MetricSignal] = []
    services: set[str] = set()
    for col in df.columns:
        if col == "time":
            continue
        parsed = classify(col)
        if parsed is None:
            continue
        service, kind = parsed
        services.add(service)
        x = pd.to_numeric(df[col], errors="coerce").ffill().bfill().to_numpy(dtype=float)
        if np.all(np.isnan(x)):
            continue
        xb, xa = x[before], x[after]
        if len(xb) < 10 or len(xa) < 10:
            continue
        med, spread = _robust(xb, x)
        # Smooth the post window so one spike does not dominate, then take the median level.
        xs = pd.Series(xa).rolling(smooth_s, min_periods=1).mean().to_numpy()
        after_med = float(np.nanmedian(xa))
        # Score: the larger of the sustained shift (median) and the peak smoothed excursion,
        # the latter down-weighted because transient spikes are common in healthy services.
        sustained = abs(after_med - med) / spread
        peak = float(np.nanmax(np.abs(xs - med))) / spread
        score = max(sustained, 0.5 * peak)
        direction = 1 if (after_med - med) >= 0 else -1
        if sustained < 0.5 * peak:
            direction = 1 if float(np.nanmax(xs - med)) >= abs(float(np.nanmin(xs - med))) else -1
        out_band = np.where(np.abs(xs - med) > 3 * spread)[0]
        onset = float(t[after][out_band[0]] - inject) if len(out_band) else None
        ratio = after_med / med if abs(med) > 1e-9 else math.inf if after_med > 0 else 1.0
        signals.append(
            MetricSignal(col, service, kind, med, spread, after_med, score, direction, onset, ratio)
        )
    return CaseEvidence(str(case_dir), sorted(services), inject, signals)
