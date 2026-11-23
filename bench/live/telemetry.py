"""Read the OpenTelemetry demo shop's own telemetry: Prometheus for metrics, OpenSearch for logs.

The shop is a system AgentOps did not build. This module only reads what its stock
observability stack already collects: span metrics from the collector (calls, errors, latency per
service), Docker container stats (cpu, memory, network), and the logs the services export.
Everything comes back in the same shape the RCAEval harness uses (`CaseEvidence`), so the same
statistics rank the services.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

PROMETHEUS = "http://localhost:9090"
STEP_S = 15
# Containers that are the shop's own services. Infrastructure (collector, Prometheus, Jaeger,
# OpenSearch, flagd, the load generator, the docs and proxies) is never a candidate cause.
SERVICES = [
    "ad", "cart", "checkout", "currency", "email", "frontend", "image-provider", "payment",
    "product-catalog", "quote", "recommendation", "shipping", "valkey-cart", "astronomy-db",
]
# The span-metrics service names differ from container names for a few services.
SPAN_SERVICE = {"product-catalog": "product-catalog", "valkey-cart": "valkey-cart"}

QUERIES = {
    # server-side request rate, error ratio and p95 latency from the collector's span metrics
    "load": 'sum by (service_name) (rate(traces_span_metrics_calls_total{{span_kind="SPAN_KIND_SERVER"}}[3m]))',
    "errcount": 'sum by (service_name) (rate(traces_span_metrics_calls_total{{span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}}[3m]))',
    "latency": 'histogram_quantile(0.95, sum by (service_name, le) (rate(traces_span_metrics_duration_milliseconds_bucket{{span_kind="SPAN_KIND_SERVER"}}[3m])))',
    # client-side view: what callers see when they call out (catches a callee that is down)
    "clienterror": 'sum by (service_name) (rate(traces_span_metrics_calls_total{{span_kind="SPAN_KIND_CLIENT",status_code="STATUS_CODE_ERROR"}}[3m]))',
    # container stats
    "cpu": "sum by (container_name) (container_cpu_utilization_ratio)",
    "mem": "sum by (container_name) (container_memory_usage_total_bytes)",
    "netrx": "sum by (container_name) (rate(container_network_io_usage_rx_bytes_total[3m]))",
}
LABEL = {"cpu": "container_name", "mem": "container_name", "netrx": "container_name"}


def prom_range(query: str, start: float, end: float, step: int = STEP_S) -> list[dict]:
    q = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{PROMETHEUS}/api/v1/query_range?{q}", timeout=30) as r:
        body = json.load(r)
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus refused a query: {body.get('error', '?')}")
    return body["data"]["result"]


def metrics_frame(start: float, end: float) -> pd.DataFrame:
    """One column per `<service>_<kind>`, one row per step, like an RCAEval metrics file."""
    cols: dict[str, pd.Series] = {}
    for kind, query in QUERIES.items():
        label = LABEL.get(kind, "service_name")
        for series in prom_range(query.replace("{{", "{").replace("}}", "}"), start, end):
            name = series["metric"].get(label)
            if not name or name not in SERVICES:
                continue
            vals = pd.Series(
                {int(float(t)): float(v) if v not in ("NaN", "+Inf", "-Inf") else np.nan for t, v in series["values"]}
            )
            col = f"{name}_{ {'netrx': 'net'}.get(kind, kind)}"
            cols[col] = vals
    df = pd.DataFrame(cols).sort_index()
    # Error ratio per service: error calls / all calls, 0 when no error series exists (Prometheus
    # has no error series at all for a service that never failed).
    for svc in SERVICES:
        load = df.get(f"{svc}_load")
        if load is None:
            continue
        errs = df.get(f"{svc}_errcount", pd.Series(0.0, index=df.index)).fillna(0.0)
        df[f"{svc}_error"] = (errs / load.where(load > 0)).fillna(0.0)
        if f"{svc}_errcount" in df:
            del df[f"{svc}_errcount"]
        if f"{svc}_clienterror" in df:
            df[f"{svc}_clienterror"] = df[f"{svc}_clienterror"].fillna(0.0)
    df.index.name = "time"
    return df.reset_index()


@dataclass
class LogRow:
    ts: float
    service: str
    severity: str
    body: str


def opensearch_url() -> str:
    ip = subprocess.run(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", "opensearch"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return f"http://{ip}:9200"


def logs(start: float, end: float, size: int = 5000, min_severity: int = 13) -> list[LogRow]:
    """Log records the shop's services exported between `start` and `end` (epoch seconds), at
    OpenTelemetry severity number `min_severity` or above (13 is WARN, 17 is ERROR)."""
    query = {
        "size": size,
        "sort": [{"@timestamp": "asc"}],
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {
                "gte": datetime.fromtimestamp(start, UTC).isoformat(),
                "lte": datetime.fromtimestamp(end, UTC).isoformat(),
            }}},
            {"range": {"severity.number": {"gte": min_severity}}},
            {"terms": {"resource.service.name.keyword": SERVICES}},
        ]}},
    }
    req = urllib.request.Request(
        f"{opensearch_url()}/otel-logs-*/_search", data=json.dumps(query).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        hits = json.load(r)["hits"]["hits"]
    rows = []
    for h in hits:
        s = h["_source"]
        res = s.get("resource") or {}
        svc = res.get("service.name") or (res.get("service") or {}).get("name") or "?"
        ts = s.get("@timestamp") or s.get("observedTimestamp")
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        rows.append(LogRow(t, svc, severity_word((s.get("severity") or {}).get("number")), str(s.get("body", ""))[:300]))
    return rows


def severity_word(number: object) -> str:
    """OpenTelemetry severity number to the usual word: 17 and up ERROR, 13 and up WARN."""
    try:
        n = int(number)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "INFO"
    return "FATAL" if n >= 21 else "ERROR" if n >= 17 else "WARN" if n >= 13 else "INFO"


def scalar_series(query: str, start: float, end: float) -> dict[str, list[tuple[float, float]]]:
    """A PromQL range query, one (time, value) list per service_name; NaN points dropped."""
    out: dict[str, list[tuple[float, float]]] = {}
    for series in prom_range(query, start, end):
        name = series["metric"].get("service_name")
        if name:
            pts = [(float(t), float(v)) for t, v in series["values"] if v not in ("NaN", "+Inf", "-Inf")]
            out[name] = pts
    return out


P99 = 'histogram_quantile(0.99, sum by (service_name, le) (rate(traces_span_metrics_duration_milliseconds_bucket{span_kind="SPAN_KIND_SERVER",service_name="%s"}[2m])))'
ERRS = 'sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR",service_name="%s"}[2m]))'
CALLS = 'sum by (service_name) (rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER",service_name="%s"}[2m]))'


def instant_p99(service: str, start: float, end: float) -> float | None:
    """The service's server p99 over the whole window, in ms."""
    rng = f"{max(int(end - start), 60)}s"
    q = f'histogram_quantile(0.99, sum by (le) (increase(traces_span_metrics_duration_milliseconds_bucket{{span_kind="SPAN_KIND_SERVER",service_name="{service}"}}[{rng}])))'
    r = urllib.request.urlopen(f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": q, "time": end}), timeout=30)
    res = json.load(r)["data"]["result"]
    if not res:
        return None
    v = float(res[0]["value"][1])
    return v if v == v else None
