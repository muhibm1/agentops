"""One line per graph step, printed while the run goes, for `--watch`.

The thread commands normally print one report when the graph pauses or ends. With `--watch`
they stream the run instead (LangGraph's `stream_mode="updates"`): each node's update arrives
as soon as the node finishes, and `step_line` turns it into one line naming the node and what
it changed. The lines are read only from the update, so they show exactly what the node wrote
to the checkpoint, nothing more.
"""

from collections.abc import Mapping
from typing import Any

# How many hypotheses a triage or rca line names.
TOP_HYPOTHESES = 2
# The key LangGraph streams an interrupt under (`langgraph.constants.INTERRUPT`).
INTERRUPT_KEY = "__interrupt__"


def step_line(node: str, update: Any) -> str:
    """One line for one streamed update: `node: what it changed`."""
    if node == INTERRUPT_KEY:
        return "approval: paused for a person's decision"
    if not isinstance(update, Mapping):
        return f"{node}: done"
    parts = [part for part in _parts(update) if part]
    return f"{node}: {'; '.join(parts)}" if parts else f"{node}: no change"


def _parts(update: Mapping[str, Any]) -> list[str]:
    """The phrases for each field the update wrote, in a fixed order."""
    parts: list[str] = []
    evidence = update.get("evidence")
    if evidence:
        sources = sorted({_field(item, "source") or "?" for item in evidence})
        parts.append(f"+{len(evidence)} evidence from {', '.join(sources)}")
    hypotheses = update.get("hypotheses")
    if hypotheses:
        parts.append("top " + _top(hypotheses))
    root_cause = update.get("root_cause")
    if root_cause is not None:
        parts.append(f"cause {_field(root_cause, 'label')}")
    remediation = update.get("remediation")
    if remediation is not None:
        parts.append(f"proposes {_field(remediation, 'action')} on {_field(remediation, 'target')}")
    approval = update.get("approval")
    if approval is not None:
        parts.append(f"decision {_field(approval, 'decision')} by {_field(approval, 'operator')}")
    execution = update.get("execution")
    if execution is not None:
        applied = _field(execution, "applied")
        word = {True: "applied", False: "not applied", None: "effect unknown"}.get(applied, "?")
        parts.append(f"{_field(execution, 'action')} {word}")
    if update.get("postmortem") is not None:
        parts.append("postmortem written")
    if update.get("escalation_reason") is not None:
        parts.append(f"escalation reason {update['escalation_reason']}")
    if "investigation_budget" in update:
        parts.append(f"budget {update['investigation_budget']} left")
    if "loops" in update:
        parts.append(f"loop {update['loops']}")
    if "status" in update:
        parts.append(f"status {update['status']}")
    return parts


def _top(hypotheses: list[Any]) -> str:
    """The highest-scored hypotheses as `name (score)`, best first."""
    ranked = sorted(hypotheses, key=lambda item: -float(_field(item, "score") or 0.0))
    return ", ".join(
        f"{_field(item, 'name')} ({float(_field(item, 'score') or 0.0):.2f})"
        for item in ranked[:TOP_HYPOTHESES]
    )


def _field(item: Any, name: str) -> Any:
    """`item.name` for a model, `item[name]` for a dict."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)
