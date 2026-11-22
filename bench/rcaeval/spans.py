"""Trace and log evidence for RCAEval cases that have them (RE2 and RE3), as extra signals on the
same `CaseEvidence`.

Designed from first principles and never tuned: the development split (RE1) has no logs or
traces, so nothing here was fitted to any labelled case. The signals:

- trace self-time: a span's duration minus the time spent in its direct children. A delay or a
  CPU-starved service raises its own self-time; its callers' self-time does not move, even though
  their total latency does. This is the propagation-free version of latency.
- trace errors: the share of a service's spans with a non-zero status, counting only spans whose
  children did not also fail, so an error is charged to where it started.
- log errors: lines that mention an error, exception, failure or timeout, per service, per minute.
- new log templates: messages (digits and ids masked) never seen before injection. A code-level
  fault usually prints something new.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .evidence import CaseEvidence, MetricSignal, _robust

ERROR_WORDS = re.compile(r"error|exception|fail|timeout|timed out|refused|unavailable|panic", re.I)
MASK = re.compile(r"[0-9a-f]{8,}|\d+")


def norm(name: str) -> str:
    """Normalise a service name so trace, log and metric spellings meet."""
    n = name.lower()
    n = re.sub(r"[^a-z0-9]", "", n)
    if n.endswith("service") and len(n) > 7:
        n = n[: -len("service")]
    return n


def _map_names(names: list[str], services: list[str]) -> dict[str, str]:
    by_norm = {norm(s): s for s in services}
    return {n: by_norm.get(norm(n), n) for n in names}


def _bucket_series(times: np.ndarray, values: np.ndarray, start: int, end: int, width: int) -> np.ndarray:
    edges = np.arange(start, end + width, width)
    idx = np.clip(np.searchsorted(edges, times, side="right") - 1, 0, len(edges) - 2)
    out = np.full(len(edges) - 1, np.nan)
    df = pd.DataFrame({"i": idx, "v": values}).groupby("i")["v"].median()
    out[df.index.to_numpy()] = df.to_numpy()
    return pd.Series(out).ffill().bfill().to_numpy()


def _bucket_rate(times: np.ndarray, start: int, end: int, width: int) -> np.ndarray:
    edges = np.arange(start, end + width, width)
    counts, _ = np.histogram(times, bins=edges)
    return counts.astype(float)


def _signal(column, service, kind, series_before, series_after, whole, bucket_s) -> MetricSignal | None:
    if len(series_before) < 3 or len(series_after) < 3:
        return None
    med, spread = _robust(series_before, whole)
    after_med = float(np.nanmedian(series_after))
    sustained = abs(after_med - med) / spread
    peak = float(np.nanmax(np.abs(series_after - med))) / spread
    score = max(sustained, 0.5 * peak)
    direction = 1 if after_med >= med else -1
    if sustained < 0.5 * peak:
        direction = 1 if float(np.nanmax(series_after - med)) >= abs(float(np.nanmin(series_after - med))) else -1
    out = np.where(np.abs(series_after - med) > 3 * spread)[0]
    onset = float(out[0] * bucket_s) if len(out) else None
    ratio = after_med / med if abs(med) > 1e-9 else (float("inf") if after_med > 0 else 1.0)
    return MetricSignal(column, service, kind, med, spread, after_med, score, direction, onset, ratio)


def add_traces(ev: CaseEvidence, case_dir: Path, bucket_s: int = 10) -> None:
    path = case_dir / "traces.parquet"
    if not path.exists():
        return
    t = pd.read_parquet(path, columns=["spanID", "parentSpanID", "serviceName", "startTimeMillis", "duration", "statusCode"])
    if t.empty:
        return
    t["sec"] = (t["startTimeMillis"] // 1000).astype("int64")
    child = t.groupby("parentSpanID")["duration"].sum()
    t["self"] = (t["duration"] - t["spanID"].map(child).fillna(0)).clip(lower=0)
    err = t["statusCode"].fillna(0).astype(str).isin(["0", "0.0", "200", "OK", "Unset", "UNSET", ""]) == False  # noqa: E712
    child_err = t.loc[err].groupby("parentSpanID").size()
    t["own_err"] = err & ~t["spanID"].isin(child_err.index)
    start, end = int(t["sec"].min()), int(t["sec"].max())
    names = _map_names(sorted(t["serviceName"].dropna().unique()), ev.services)
    for raw, svc in names.items():
        if svc not in ev.services:
            ev.services.append(svc)
        s = t[t["serviceName"] == raw]
        times = s["sec"].to_numpy()
        before = times < ev.inject_time
        # self time, median per bucket
        whole = _bucket_series(times, s["self"].to_numpy(float), start, end, bucket_s)
        edges_mid = np.arange(start, end, bucket_s)[: len(whole)]
        b = whole[edges_mid < ev.inject_time]
        a = whole[edges_mid >= ev.inject_time]
        sig = _signal(f"{svc}_trace-self", svc, "latency", b, a, whole, bucket_s)
        if sig:
            ev.signals.append(sig)
        # own error rate per bucket
        n_all = _bucket_rate(times, start, end, bucket_s)
        n_err = _bucket_rate(times[s["own_err"].to_numpy()], start, end, bucket_s)
        rate = np.where(n_all > 0, n_err / np.maximum(n_all, 1), 0.0)
        mids = np.arange(start, end + bucket_s, bucket_s)[: len(rate)]
        sig = _signal(f"{svc}_trace-error", svc, "error", rate[mids < ev.inject_time], rate[mids >= ev.inject_time], rate, bucket_s)
        if sig:
            ev.signals.append(sig)
        del before


def add_logs(ev: CaseEvidence, case_dir: Path, bucket_s: int = 60) -> None:
    path = case_dir / "logs.parquet"
    if not path.exists():
        return
    lg = pd.read_parquet(path)
    if lg.empty:
        return
    ts = pd.to_numeric(lg["timestamp"], errors="coerce")
    if ts.median() > 1e12:  # milliseconds
        ts = ts // 1000
    lg = lg.assign(sec=ts.astype("int64"), message=lg["message"].fillna("").astype(str))
    start, end = int(lg["sec"].min()), int(lg["sec"].max())
    names = _map_names(sorted(lg["container_name"].dropna().unique()), ev.services)
    for raw, svc in names.items():
        if svc not in ev.services:
            ev.services.append(svc)
        s = lg[lg["container_name"] == raw]
        times = s["sec"].to_numpy()
        is_err = s["message"].str.contains(ERROR_WORDS).to_numpy()
        rate = _bucket_rate(times[is_err], start, end, bucket_s)
        mids = np.arange(start, end + bucket_s, bucket_s)[: len(rate)]
        sig = _signal(f"{svc}_log-error", svc, "error", rate[mids < ev.inject_time], rate[mids >= ev.inject_time], rate, bucket_s)
        if sig:
            ev.signals.append(sig)
        templates = s["message"].str.slice(0, 200).str.replace(MASK, "#", regex=True)
        seen = set(templates[times < ev.inject_time])
        new_mask = (times >= ev.inject_time) & ~templates.isin(seen).to_numpy()
        n_new_lines = int(new_mask.sum())
        n_new_templates = int(templates[new_mask].nunique())
        if n_new_lines:
            top = s["message"][new_mask].str.slice(0, 160).value_counts().head(3)
            ev.new_logs[svc] = [f"{c}x {m}" for m, c in top.items()]
            # A count, not a shift: score grows with how many distinct new messages appeared,
            # on the same 0-20 scale as the others.
            score = min(20.0, 4.0 * np.log1p(n_new_templates) + np.log1p(n_new_lines))
            ev.signals.append(
                MetricSignal(f"{svc}_log-new-templates", svc, "error", 0.0, 1.0, float(n_new_templates), score, 1, None, float("inf"))
            )
