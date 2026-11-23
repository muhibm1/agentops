"""`OtelDemoSource`: AgentOps' `EvidenceSource` over the OpenTelemetry demo shop.

The graph cannot tell this from the simulator: it reads logs, metrics and dependency health, and
it can apply exactly one kind of change, which the live runbook (`bench/live/runbook.yaml`)
allowlists and the approval node gates. Semantics an operator should know:

- `get_dependency_health(service)` lists every other shop service with a status and the
  strongest code-ranked anomalies (the frozen RCAEval statistics), comparing the last
  `recent_s` seconds before the incident opened with the `baseline_s` seconds before that.
- `get_logs` returns WARN-or-worse lines from the shop's services whose pattern was not
  already being logged before the incident (pre-incident noise is left out).
- After an action is applied, `get_metrics` and `get_logs` first wait until `settle_s` seconds
  have passed since the change, and return only what was observed after that, so verify judges
  the shop as it is after the change, not the incident it already knows about.
- `get_config` and `get_deployments` return nothing: the shop exposes neither, and the agent is
  never told which feature flags exist or are on.
- `apply` turns off every fault feature flag owned by the target service in flagd's file. It
  changes nothing for a service that owns none, and reports that it changed nothing.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from agentops.sources.base import (
    ActionRequest,
    ActionResult,
    AppliedAction,
    DependencyStatus,
    Deployment,
    LogLine,
    Point,
    Series,
    Window,
)

from . import blame, edges, stats, telemetry

ACTION = "turn_off_service_feature_flags"
# The flags in the demo that inject a fault, by the service whose code reads them. Flags that
# only matter on Kubernetes, in the AI or Kafka parts the bench does not run, or that change the
# load generator are left out.
SERVICE_FLAGS: dict[str, list[str]] = {
    "ad": ["adFailure", "adHighCpu", "adManualGc"],
    "cart": ["cartFailure"],
    "email": ["emailMemoryLeak"],
    "payment": ["paymentFailure", "paymentUnreachable"],
    "product-catalog": ["productCatalogFailure", "productCatalogLockContention"],
    "recommendation": ["recommendationCacheFailure"],
    "shipping": ["intlShippingSlowdown"],
}


def targeted_variant(flag: dict) -> str | None:
    """The variant a flag's simple `if` targeting rule serves when its condition holds."""
    rule = (flag.get("targeting") or {}).get("if")
    return rule[1] if isinstance(rule, list) and len(rule) >= 2 and isinstance(rule[1], str) else None


def set_targeted_variant(flag: dict, variant: str) -> None:
    rule = (flag.get("targeting") or {}).get("if")
    if isinstance(rule, list) and len(rule) >= 2 and isinstance(rule[1], str):
        rule[1] = variant


def iso(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


class OtelDemoSource:
    def __init__(
        self,
        flag_file: Path,
        ledger_path: Path,
        opened_at: datetime,
        *,
        baseline_s: int = 600,
        recent_s: int = 180,
        settle_s: int = 150,
        observe_s: int = 180,
        max_observe_s: int = 360,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.flag_file = flag_file
        self.ledger_path = ledger_path
        self.opened = opened_at.timestamp()
        self.baseline_s, self.recent_s, self.settle_s = baseline_s, recent_s, settle_s
        self.observe_s, self.max_observe_s = observe_s, max_observe_s
        self.clock, self.sleep = clock, sleep
        self._analysis: tuple[list[blame.Health], dict, dict[str, list[str]], edges.Window] | None = None

    # ---------- reads ----------

    def analysis(self) -> tuple[list[blame.Health], dict, dict[str, list[str]], edges.Window]:
        """Per-service health, signals by service, new log lines and the baseline edges, computed
        once per incident from the window that ends when the incident opened."""
        if self._analysis is None:
            split = self.opened - self.recent_s
            start = split - self.baseline_s
            df = telemetry.metrics_frame(start, self.opened)
            ev = stats.evidence(df, split)
            before_edges = edges.window(start, split)
            health = blame.analyse(ev, before_edges, edges.window(split, self.opened), self.recent_s, self.baseline_s)
            before = telemetry.logs(start, split)
            after = telemetry.logs(split, self.opened)
            self._analysis = (health, ev.by_service(), stats.new_log_lines(before, after), before_edges)
            # Kept for the scorer's ablations; the agent never reads these files.
            d = self.ledger_path.parent
            (d / "ranking.json").write_text(json.dumps([h.service for h in health if h.service != "frontend"]))
            (d / "stats_ranking.json").write_text(json.dumps([sv for sv, _ in stats.ranked(ev) if sv != "frontend"]))
        return self._analysis

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        health, by_service, new_logs, _ = self.analysis()
        rows = [h for h in health if h.service != service]
        out = []
        for pos, h in enumerate(rows, 1):
            sigs = sorted(by_service.get(h.service, []), key=lambda g: -g.score)[:3]
            parts = [
                f"{g.column} {stats_fmt(g.baseline_median)} -> {stats_fmt(g.after_median)} (shift {min(g.score, 99):.1f})"
                for g in sigs
            ]
            text = f"anomaly rank {pos} of {len(rows)}, code score {h.score:.1f}; " + "; ".join(parts + h.reasons)
            for line in new_logs.get(h.service, []):
                text += f"; new log line: {line}"
            out.append(DependencyStatus(id=f"dep-{h.service}", name=h.service, status=h.status, detail=text[:1200]))  # type: ignore[arg-type]
        return out

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        if self.applied_at() is not None:
            return self._post_change_metrics(service, names)
        start = max(window.start.timestamp(), self.opened - self.recent_s - self.baseline_s)
        end = min(window.end.timestamp(), self.clock())
        if end <= start:
            return []
        out = []
        if "error_rate" in names:
            errs = dict(telemetry.scalar_series(telemetry.ERRS % service, start, end).get(service, []))
            calls = telemetry.scalar_series(telemetry.CALLS % service, start, end).get(service, [])
            pts = [Point(at=iso(t), value=(errs.get(t, 0.0) / v if v > 0 else 0.0)) for t, v in calls][-24:]
            if pts:
                out.append(Series(id=f"{service}-error-rate", name="error_rate", unit="ratio", points=pts))
        if "p99_latency_ms" in names:
            pts = [Point(at=iso(t), value=v) for t, v in telemetry.scalar_series(telemetry.P99 % service, start, end).get(service, [])][-24:]
            if pts:
                out.append(Series(id=f"{service}-p99-latency", name="p99_latency_ms", unit="ms", points=pts))
        return out

    def _post_change_metrics(self, service: str, names: list[str]) -> list[Series]:
        """After a change: wait for it to settle, then observe at least `observe_s`, longer (up to
        `max_observe_s`) until every call edge that was impaired at analysis has been called
        `MIN_CALLS` times. Report one point each for the whole observed window:
        - error_rate: the larger of the paged service's server error ratio and the worst rise,
          over its pre-incident level, of any call edge's error ratio in the shop;
        - p99_latency_ms: the paged service's p99;
        - dependency_p99_ms: the worst call edge's p99.
        Raises when the impaired edges were not called enough to judge: silence is not health."""
        applied = self.applied_at() or self.clock()
        start = applied + self.settle_s
        health, _, _, before = self.analysis()
        impaired = {(c, x) for h in health for r in h.reasons if r.startswith("calls to ") for c, x in [(h.service, r[9:].split(":")[0])]}
        deadline = start + self.max_observe_s
        self._wait_until(start + self.observe_s)
        while True:
            now = self.clock()
            w = edges.window(start, now)
            short = [k for k in impaired if (w.edges.get(k).calls if k in w.edges else 0.0) < edges.MIN_CALLS]
            if not short or now >= deadline:
                break
            self.sleep(min(30.0, deadline - now))
        if short:
            raise RuntimeError("Too few calls since the change to judge recovery: " + ", ".join(f"{c}->{x}" for c, x in short))
        at = iso(now)
        served = w.served.get(service, 0.0)
        own = w.server_errors.get(service, 0.0) / served if served else 0.0
        rises = [
            e.error_ratio - (before.edges[k].error_ratio if k in before.edges else 0.0)
            for k, e in w.edges.items() if e.calls >= edges.MIN_CALLS
        ]
        out = []
        if "error_rate" in names:
            out.append(Series(id=f"{service}-error-rate-after-change", name="error_rate", unit="ratio",
                              points=[Point(at=at, value=max([own, *rises]))]))
        if "p99_latency_ms" in names:
            p99 = telemetry.instant_p99(service, start, now)
            if p99 is not None:
                out.append(Series(id=f"{service}-p99-after-change", name="p99_latency_ms", unit="ms", points=[Point(at=at, value=p99)]))
        if "dependency_p99_ms" in names:
            worst = max((e.p99_ms or 0.0 for e in w.edges.values() if e.calls >= edges.MIN_CALLS), default=None)
            if worst is not None:
                out.append(Series(id="shop-worst-call-p99-after-change", name="dependency_p99_ms", unit="ms", points=[Point(at=at, value=worst)]))
        return out

    def _wait_until(self, t: float) -> None:
        wait = t - self.clock()
        if wait > 0:
            self.sleep(wait)

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        if self.applied_at() is not None:
            start = (self.applied_at() or 0.0) + self.settle_s
            self._wait_until(start + self.observe_s)
        else:
            start = max(window.start.timestamp(), self.opened - self.recent_s - self.baseline_s)
        end = min(window.end.timestamp(), self.clock())
        if end <= start:
            return []
        noise_start = self.opened - self.recent_s - self.baseline_s
        noise = {(r.service, stats.template(r.body)) for r in telemetry.logs(noise_start, self.opened - self.recent_s)}
        rows = [r for r in telemetry.logs(start, end) if (r.service, stats.template(r.body)) not in noise]
        if query:
            rows = [r for r in rows if query.lower() in r.body.lower() or query.lower() == r.service]
        firsts: dict[tuple[str, str], telemetry.LogRow] = {}
        for r in rows:
            firsts.setdefault((r.service, stats.template(r.body)), r)
        picked = sorted(firsts.values(), key=lambda r: (r.severity != "ERROR", r.ts))[:40]
        return [
            LogLine(id=f"log-{i}", timestamp=iso(r.ts), level=r.severity, message=r.body[:300], service=r.service)
            for i, r in enumerate(sorted(picked, key=lambda r: r.ts), 1)
        ]

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        return []

    def get_config(self, service: str) -> dict[str, str | int | float | bool]:
        return {}

    # ---------- the one change ----------

    def ledger(self) -> list[AppliedAction]:
        if not self.ledger_path.exists():
            return []
        out = []
        for line in self.ledger_path.read_text().splitlines():
            try:
                out.append(AppliedAction.model_validate_json(json.loads(line)["action"]))
            except (ValueError, KeyError):
                continue
        return out

    def applied_at(self) -> float | None:
        if not self.ledger_path.exists():
            return None
        times = [json.loads(x)["at"] for x in self.ledger_path.read_text().splitlines() if x.strip()]
        return max(times) if times else None

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        if action.action != ACTION:
            return ActionResult(ok=False, applied=False, idempotency_key=idempotency_key,
                                detail=f"This source cannot apply {action.action}.")
        if any(a.idempotency_key == idempotency_key for a in self.ledger()):
            return ActionResult(ok=True, applied=False, idempotency_key=idempotency_key,
                                detail="Already applied under this key.")
        flags = SERVICE_FLAGS.get(action.target, [])
        doc = json.loads(self.flag_file.read_text())
        changed = []
        for name in flags:
            flag = doc["flags"].get(name)
            if flag and (flag.get("defaultVariant") != "off" or targeted_variant(flag) not in (None, "off")):
                flag["defaultVariant"] = "off"
                set_targeted_variant(flag, "off")
                changed.append(name)
        if changed:
            # In place, not by rename: flagd watches this file.
            with open(self.flag_file, "r+") as f:
                text = json.dumps(doc, indent=2)
                f.seek(0)
                f.write(text)
                f.truncate()
        record = AppliedAction(idempotency_key=idempotency_key, action=action.action,
                               target=action.target, params=action.params)
        with open(self.ledger_path, "a") as f:
            f.write(json.dumps({"at": self.clock(), "changed": len(changed),
                                "action": record.model_dump_json()}) + "\n")
        detail = (f"Turned off {len(changed)} feature flag(s) of {action.target}." if changed
                  else f"{action.target} had no fault feature flag turned on; nothing changed.")
        return ActionResult(ok=True, applied=bool(changed), idempotency_key=idempotency_key, detail=detail)


def stats_fmt(v: float) -> str:
    if v is None or not pd.notna(v):
        return "nan"
    a = abs(v)
    return f"{v:.3e}" if a >= 1e6 else f"{v:.0f}" if a >= 100 else f"{v:.2f}" if a >= 1 else f"{v:.4f}"
