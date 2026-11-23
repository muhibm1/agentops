"""Run AgentOps' unchanged graph on a live incident in the OpenTelemetry demo shop.

    python -m live.harness open    --work W --id INC     # page: open the incident and run
    python -m live.harness resume  --work W --id INC     # after a model answer was written
    python -m live.harness approve --work W --id INC     # the on-call approves the proposal

Each command runs the graph until it needs a model answer (exit 3, the request is in
W/model/requests/), stops at the approval pause (exit 4), or ends (exit 0). Every step is
appended to W/<INC>/events.jsonl with a wall-clock time. The approver is a script standing in
for the on-call engineer's click: it approves exactly the proposal shown, never an edit, and
the action can only run through the graph's approval node and the runbook in this folder.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command

from agentops.actions.audit import AuditLog
from agentops.actions.registry import Registry
from agentops.actions.runbook import load_runbook
from agentops.graph import build_graph, checkpoint_serializer
from agentops.models import GRAPH_ROLES
from agentops.state import new_incident

from . import fired
from .handoff import AnswerNeeded, HandoffChatModel
from .source import OtelDemoSource

FLAG_FILE = Path("/home/user/otel-demo/src/flagd/demo.flagd.json")
RUNBOOK = Path(__file__).with_name("runbook.yaml")
SERVICE = "frontend"
SYMPTOMS = ["The on-call was paged for the shop: customers report pages failing or slow."]


def event(work: Path, inc: str, **fields: object) -> None:
    d = work / inc
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "events.jsonl", "a") as f:
        f.write(json.dumps({"t": time.time(), **fields}, default=str) + "\n")


def build(work: Path, inc: str, conn: sqlite3.Connection):
    meta = json.loads((work / inc / "meta.json").read_text())
    opened = datetime.fromisoformat(meta["opened_at"])
    source = OtelDemoSource(FLAG_FILE, work / inc / "ledger.jsonl", opened)
    model = HandoffChatModel(folder=work / "model")
    saver = SqliteSaver(conn, serde=checkpoint_serializer())
    saver.setup()
    store = SqliteStore(conn)
    store.setup()
    graph = build_graph(
        source,
        dict.fromkeys(GRAPH_ROLES, model),  # type: ignore[arg-type]
        Registry(load_runbook(RUNBOOK)),
        AuditLog(work / "audit"),
        saver,
        store=store,
    )
    return graph, {"configurable": {"thread_id": inc}, "recursion_limit": 80}


def drive(work: Path, inc: str, value: object) -> int:
    conn = sqlite3.connect(work / "state.sqlite", check_same_thread=False)
    graph, config = build(work, inc, conn)
    try:
        graph.invoke(value, config, durability="sync")
    except AnswerNeeded as need:
        event(work, inc, step="needs_answer", key=need.key)
        print(json.dumps({"status": "needs_answer", "key": need.key}))
        return 3
    snap = graph.get_state(config)
    if snap.interrupts:
        payload = snap.interrupts[0].value
        event(work, inc, step="awaiting_approval", proposal=payload)
        print(json.dumps({"status": "awaiting_approval", "proposal": payload}, default=str)[:3000])
        return 4
    st = snap.values
    summary = {
        "status": st.get("status"),
        "root_cause": st.get("root_cause"),
        "remediation": st.get("remediation"),
        "execution": st.get("execution"),
        "escalation_reason": st.get("escalation_reason"),
    }
    event(work, inc, step="ended", **summary)
    print(json.dumps({"status": "ended", **summary}, default=str)[:3000])
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["open", "resume", "approve", "advance"])
    p.add_argument("--work", required=True)
    p.add_argument("--id", required=True)
    a = p.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    if a.cmd == "open":
        opened = datetime.now(UTC)
        (work / a.id).mkdir(parents=True, exist_ok=True)
        (work / a.id / "meta.json").write_text(json.dumps({"opened_at": opened.isoformat()}))
        event(work, a.id, step="opened")
        # Whether the shop showed a customer-visible symptom at the page, judged by verify's own
        # rule before the agent takes a step, so nothing the agent does can change it.
        (work / a.id / "fired.json").write_text(json.dumps(fired.symptoms(opened.timestamp())))
        value: object = new_incident(
            SERVICE, SYMPTOMS, opened_at=opened, incident_id=a.id, scenario_id="otel-demo-live"
        )
    elif a.cmd == "advance":
        # Take the next step the last one calls for: resume once a needed answer exists, approve
        # a pending proposal. Exit 5 when still waiting for an answer.
        last = json.loads((work / a.id / "events.jsonl").read_text().splitlines()[-1])
        if last["step"] == "needs_answer":
            if not (work / "model" / "answers" / f"{last['key']}.json").exists():
                print(json.dumps({"status": "needs_answer", "key": last["key"]}))
                sys.exit(5)
            event(work, a.id, step="resumed")
            value = None
        elif last["step"] == "awaiting_approval":
            event(work, a.id, step="approved", operator="on-call")
            value = Command(resume={"decision": "approve", "operator": "on-call"})
        else:
            print(json.dumps({"status": last["step"]}))
            sys.exit(0)
    elif a.cmd == "resume":
        event(work, a.id, step="resumed")
        value = None
    else:
        event(work, a.id, step="approved", operator="on-call")
        value = Command(resume={"decision": "approve", "operator": "on-call"})
    sys.exit(drive(work, a.id, value))


if __name__ == "__main__":
    main()
