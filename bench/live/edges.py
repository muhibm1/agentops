"""Call edges between the shop's services, read from its stock telemetry.

An edge is caller -> callee. The callee is recovered generically, with no service-specific rule:
- a CLIENT span whose name is a SERVER span name of another service, or the end of one
  (`oteldemo.CartService/GetCart` matches the server span `POST /oteldemo.CartService/GetCart`);
- a CLIENT span named after a service, with or without a port (`valkey-cart:6379`);
- an HTTP client request whose `server_address` is a service name.

Counts are `increase()` over a window evaluated at its end, so one instant query per window.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .telemetry import PROMETHEUS, SERVICES

MIN_CALLS = 3


def instant(query: str, at: float) -> list[dict]:
    q = urllib.parse.urlencode({"query": query, "time": at})
    with urllib.request.urlopen(f"{PROMETHEUS}/api/v1/query?{q}", timeout=30) as r:
        body = json.load(r)
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus refused a query: {body.get('error', '?')}")
    return body["data"]["result"]


def _val(r: dict) -> float:
    v = float(r["value"][1])
    return v if v == v else 0.0


@dataclass
class Edge:
    caller: str
    callee: str
    calls: float = 0.0
    errors: float = 0.0
    p99_ms: float | None = None

    @property
    def error_ratio(self) -> float:
        return self.errors / self.calls if self.calls > 0 else 0.0


@dataclass
class Window:
    """Edges, per-service server call counts and per-service server error counts in one window."""

    edges: dict[tuple[str, str], Edge] = field(default_factory=dict)
    served: dict[str, float] = field(default_factory=dict)
    server_errors: dict[str, float] = field(default_factory=dict)
    restarts: dict[str, float] = field(default_factory=dict)
    uptime: dict[str, float] = field(default_factory=dict)


def _callee_of(span: str, server_spans: dict[str, str]) -> str | None:
    base = span.split(":")[0]
    if base in SERVICES:
        return base
    if span in server_spans:
        return server_spans[span]
    for name, svc in server_spans.items():
        if "/" in span and (name.endswith(" " + span) or name.endswith(" /" + span)):
            return svc
    return None


def window(start: float, end: float) -> Window:
    """Everything measured between `start` and `end` (epoch seconds)."""
    rng = f"{max(int(end - start), 60)}s"
    w = Window()
    server_spans: dict[str, str] = {}
    for r in instant(f'sum by (service_name, span_name, status_code) (increase(traces_span_metrics_calls_total{{span_kind="SPAN_KIND_SERVER"}}[{rng}]))', end):
        svc, span = r["metric"].get("service_name"), r["metric"].get("span_name", "")
        if svc not in SERVICES:
            continue
        server_spans.setdefault(span, svc)
        w.served[svc] = w.served.get(svc, 0.0) + _val(r)
        if r["metric"].get("status_code") == "STATUS_CODE_ERROR":
            w.server_errors[svc] = w.server_errors.get(svc, 0.0) + _val(r)
    # Server span names seen any time in the last day, so a callee that went silent still maps.
    for r in instant('count by (service_name, span_name) (last_over_time(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[1d]))', end):
        svc = r["metric"].get("service_name")
        if svc in SERVICES:
            server_spans.setdefault(r["metric"].get("span_name", ""), svc)

    def edge(caller: str, callee: str) -> Edge:
        return w.edges.setdefault((caller, callee), Edge(caller, callee))

    for r in instant(f'sum by (service_name, span_name, status_code) (increase(traces_span_metrics_calls_total{{span_kind="SPAN_KIND_CLIENT"}}[{rng}]))', end):
        caller = r["metric"].get("service_name")
        callee = _callee_of(r["metric"].get("span_name", ""), server_spans)
        if caller not in SERVICES or callee is None or callee == caller:
            continue
        e = edge(caller, callee)
        e.calls += _val(r)
        if r["metric"].get("status_code") == "STATUS_CODE_ERROR":
            e.errors += _val(r)
    for r in instant(f'histogram_quantile(0.99, sum by (service_name, span_name, le) (increase(traces_span_metrics_duration_milliseconds_bucket{{span_kind="SPAN_KIND_CLIENT"}}[{rng}])))', end):
        caller = r["metric"].get("service_name")
        callee = _callee_of(r["metric"].get("span_name", ""), server_spans)
        if (caller, callee) in w.edges and _val(r) > 0:
            e = w.edges[(caller, callee)]
            e.p99_ms = max(e.p99_ms or 0.0, _val(r))
    # HTTP clients whose spans carry no callee name: the stock HTTP client metric has the address.
    http: dict[tuple[str, str], Edge] = {}
    for r in instant(f'sum by (service_name, server_address, error_type) (increase(http_client_request_duration_seconds_count[{rng}]))', end):
        caller, callee = r["metric"].get("service_name"), r["metric"].get("server_address")
        if caller in SERVICES and callee in SERVICES and callee != caller:
            e = http.setdefault((caller, callee), Edge(caller, callee))
            e.calls += _val(r)
            if r["metric"].get("error_type"):
                e.errors += _val(r)
    for r in instant(f'histogram_quantile(0.99, sum by (service_name, server_address, le) (increase(http_client_request_duration_seconds_bucket[{rng}])))', end):
        key = (r["metric"].get("service_name"), r["metric"].get("server_address"))
        if key in http and _val(r) > 0:
            http[key].p99_ms = _val(r) * 1000
    for key, e in http.items():
        if key not in w.edges or e.calls > w.edges[key].calls:
            w.edges[key] = e
    for r in instant(f"resets(container_uptime_seconds[{rng}])", end):
        name = r["metric"].get("container_name")
        if name in SERVICES:
            w.restarts[name] = _val(r)
    for r in instant("container_uptime_seconds", end):
        name = r["metric"].get("container_name")
        if name in SERVICES:
            w.uptime[name] = _val(r)
    return w
