"""Runbook preconditions, checked against the evidence source before anything is applied.

One function per check kind. Each reads the source only, and turns any source error into a
failed result: a check that cannot see the system must not let an action through, and must
not crash the run either.
"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from agentops.actions.runbook import (
    ActionEntry,
    ConfigTrue,
    DependencyNotHealthy,
    DeploymentWithinMinutes,
    MetricAbove,
    MetricBelow,
    Precondition,
    PreviousVersionAvailable,
)
from agentops.sources.base import ConfigValue, Deployment, EvidenceSource, Window
from agentops.state import IncidentState, Remediation

# Metrics are read from an hour before the incident opened to thirty minutes after, so a check
# made after an earlier action sees the post-action points (the verify window, ADR 0003).
METRIC_LOOKBACK = timedelta(minutes=60)
METRIC_LOOKAHEAD = timedelta(minutes=30)
# Deployments are read a day back, so a deployment too old to roll back is still reported.
DEPLOYMENT_LOOKBACK = timedelta(hours=24)

Observed = ConfigValue | None


class PreconditionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    passed: bool
    observed: Observed
    detail: str


def evaluate(
    entry: ActionEntry, remediation: Remediation, state: IncidentState, source: EvidenceSource
) -> list[PreconditionResult]:
    """Every precondition `entry` declares, in order, evaluated for `remediation`. Never raises."""
    return [
        guarded(precondition, remediation, state, source) for precondition in entry.preconditions
    ]


def guarded(
    precondition: Precondition,
    remediation: Remediation,
    state: IncidentState,
    source: EvidenceSource,
) -> PreconditionResult:
    try:
        return check_one(precondition, remediation, state, source)
    except Exception as error:  # A source error of any type fails the check (spec R8).
        return PreconditionResult(
            check=precondition.check,
            passed=False,
            observed=None,
            detail=f"The source raised {type(error).__name__} while checking {precondition.check}.",
        )


def check_one(
    precondition: Precondition,
    remediation: Remediation,
    state: IncidentState,
    source: EvidenceSource,
) -> PreconditionResult:
    match precondition:
        case MetricAbove() | MetricBelow():
            return metric_threshold(precondition, state, source)
        case DeploymentWithinMinutes():
            return deployment_within_minutes(precondition, remediation, state, source)
        case PreviousVersionAvailable():
            return previous_version_available(precondition, remediation, state, source)
        case DependencyNotHealthy():
            return dependency_not_healthy(precondition, remediation, state, source)
        case ConfigTrue():
            return config_true(precondition, state, source)


def metric_threshold(
    precondition: MetricAbove | MetricBelow, state: IncidentState, source: EvidenceSource
) -> PreconditionResult:
    """The latest point of the metric in the window, compared strictly with the threshold."""
    window = Window(
        start=state["opened_at"] - METRIC_LOOKBACK, end=state["opened_at"] + METRIC_LOOKAHEAD
    )
    series = source.get_metrics(state["service"], [precondition.metric], window)
    points = [point for item in series if item.name == precondition.metric for point in item.points]
    if not points:
        return failed(precondition.check, None, f"{precondition.metric} has no points.")
    latest = max(points, key=lambda point: point.at).value
    is_above = isinstance(precondition, MetricAbove)
    passed = latest > precondition.value if is_above else latest < precondition.value
    relation = "above" if is_above else "below"
    verdict = "is" if passed else "is not"
    return PreconditionResult(
        check=precondition.check,
        passed=passed,
        observed=latest,
        detail=f"{precondition.metric} {verdict} {relation} {precondition.value:g} "
        f"(latest {latest:g}).",
    )


def deployment_within_minutes(
    precondition: DeploymentWithinMinutes,
    remediation: Remediation,
    state: IncidentState,
    source: EvidenceSource,
) -> PreconditionResult:
    latest = latest_deployment(state, source)
    if latest is None:
        return failed(precondition.check, None, "No deployment in the last 24 hours.")
    if latest.id != remediation.target:
        return not_the_target(precondition.check, latest, remediation)
    age = state["opened_at"] - latest.deployed_at
    minutes_ago = int(age.total_seconds() // 60)
    passed = minutes_ago <= precondition.minutes
    verdict = "within" if passed else "older than"
    return PreconditionResult(
        check=precondition.check,
        passed=passed,
        observed=minutes_ago,
        detail=f"The latest deployment is {minutes_ago} minutes old, {verdict} the "
        f"{precondition.minutes} minute limit.",
    )


def previous_version_available(
    precondition: PreviousVersionAvailable,
    remediation: Remediation,
    state: IncidentState,
    source: EvidenceSource,
) -> PreconditionResult:
    """A previous version exists and, when `to_version` is given, it is that version."""
    latest = latest_deployment(state, source)
    if latest is None or not latest.previous_version:
        return failed(precondition.check, None, "No previous version is recorded.")
    if latest.id != remediation.target:
        return not_the_target(precondition.check, latest, remediation)
    wanted = remediation.params.get("to_version")
    passed = wanted is None or wanted == latest.previous_version
    detail = (
        f"Previous version {latest.previous_version} is available."
        if passed
        else f"The requested version is not the previous version {latest.previous_version}."
    )
    return PreconditionResult(
        check=precondition.check, passed=passed, observed=latest.previous_version, detail=detail
    )


def dependency_not_healthy(
    precondition: DependencyNotHealthy,
    remediation: Remediation,
    state: IncidentState,
    source: EvidenceSource,
) -> PreconditionResult:
    statuses = source.get_dependency_health(state["service"])
    status = next((item.status for item in statuses if item.name == remediation.target), None)
    if status is None:
        return failed(precondition.check, None, f"No dependency named {remediation.target}.")
    passed = status != "healthy"
    return PreconditionResult(
        check=precondition.check,
        passed=passed,
        observed=status,
        detail=f"Dependency {remediation.target} is {status}.",
    )


def config_true(
    precondition: ConfigTrue, state: IncidentState, source: EvidenceSource
) -> PreconditionResult:
    config = source.get_config(state["service"])
    if precondition.key not in config:
        return failed(precondition.check, None, f"Config has no key {precondition.key}.")
    value = config[precondition.key]
    passed = value is True
    verdict = "is" if passed else "is not"
    return PreconditionResult(
        check=precondition.check,
        passed=passed,
        observed=value,
        detail=f"Config {precondition.key} {verdict} true.",
    )


def latest_deployment(state: IncidentState, source: EvidenceSource) -> Deployment | None:
    window = Window(start=state["opened_at"] - DEPLOYMENT_LOOKBACK, end=state["opened_at"])
    deployments = source.get_deployments(state["service"], window)
    return max(deployments, key=lambda item: item.deployed_at, default=None)


def not_the_target(check: str, latest: Deployment, remediation: Remediation) -> PreconditionResult:
    """The checks read the service's latest deployment, so they only vouch for an action that
    targets that deployment."""
    return failed(
        check,
        latest.id,
        f"The latest deployment is {latest.id}, not the target {remediation.target}.",
    )


def failed(check: str, observed: Observed, detail: str) -> PreconditionResult:
    return PreconditionResult(check=check, passed=False, observed=observed, detail=detail)
