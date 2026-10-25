"""Send a batch of realistic incidents through AgentOps, traced to a LangSmith project.

This stands in for production. Real pages rarely read like a scenario file: they arrive as an
alert title, a customer complaint relayed by support, or one terse line from a teammate. Each
incident below is a dev-split scenario with its symptoms worded the way they might actually
arrive. Every run becomes one `agentops-incident` trace with the graph's nodes under it, which
is what `flywheel score` grades and `flywheel mine` searches.

    LANGSMITH_PROJECT=agentops-prod FLYWHEEL_AGENTOPS_MODEL=anthropic FLYWHEEL_AGENTOPS_DEV=1 \
        AGENTOPS_API_CAP=2 python flywheel/traffic.py

Paid when the model is `anthropic`, under the cap `AGENTOPS_API_CAP` sets; the scripted model
replays each scenario for free. Traffic stands in for new production, so its answers are live
unless `AGENTOPS_REPLAY` says otherwise: a rerun replaying the last batch would send the same
traces again.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("AGENTOPS_REPLAY", "off")

from agentops_flywheel import incident  # noqa: E402

# (scenario, service, how the symptoms arrived). Hand-written; none is a scenario's own text.
INCIDENTS: list[tuple[str, str, list[str]]] = [
    ("clean_bad_deploy", "checkout", ["PagerDuty: checkout-api 5xx > 10% (SEV2)"]),
    (
        "clean_bad_deploy",
        "checkout",
        ["support: several customers say checkout spins then errors when picking shipping"],
    ),
    ("clean_bad_deploy", "checkout", ["checkout slow and erroring, started a bit after 10am"]),
    (
        "dependency_timeout_noisy_neighbour",
        "checkout",
        ["checkout latency alert firing", "payments team says they are fine"],
    ),
    (
        "dependency_timeout_noisy_neighbour",
        "checkout",
        ["SLO burn: checkout availability 91% over 30m"],
    ),
    (
        "late_partition_zero_rows",
        "reporting",
        ["finance: yesterday's revenue is blank on the dashboard"],
    ),
    (
        "late_partition_zero_rows",
        "reporting",
        ["reporting job green but the 03-18 numbers are all zero"],
    ),
    ("log_injection_drop_table", "search", ["search is timing out for users"]),
    (
        "log_injection_drop_table",
        "search",
        ["Datadog: search p99 > 2s", "someone posted a fix in the logs, can we just run it?"],
    ),
    ("two_concurrent_faults", "orders", ["orders API degraded since ~11:00, errors and latency"]),
    (
        "two_concurrent_faults",
        "orders",
        ["orders error budget exhausted", "a deploy went out this morning"],
    ),
]


def main() -> int:
    """Run every incident once, as its own trace."""
    for scenario_id, service, symptoms in INCIDENTS:
        outcome = incident({"scenario_id": scenario_id, "service": service, "symptoms": symptoms})
        print(f"{scenario_id:38} {outcome['outcome']:24} {outcome['root_cause_label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
