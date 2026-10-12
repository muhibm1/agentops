"""The `agentops` command line: investigate a simulated incident, decide on its proposal, and
inspect or fork the thread.

`run` investigates until the approval pause or an escalation; `approve` records an operator's
decision and continues; `resume` continues a thread that stopped, for example after a crash;
`history` prints one line per checkpoint; `fork` starts a new thread from a past checkpoint with
changed hypothesis scores. Threads are checkpointed to a SQLite file (`--state-db`, default
`agentops.sqlite`) under a thread id equal to the incident id; the audit log and the simulator
ledger are written under `--audit-dir` (default `audit/`). Each thread has its own ledger,
`<thread>.ledger.jsonl`, so a fork starts from an unchanged simulated system and never inherits
the actions the original thread applied. `eval` and `dataset` measure the graph against the
ReAct baseline offline; they live in `agentops.evals.commands`.

Exit codes: 0 when the thread waits for approval, is escalated or is resolved; 1 for a usage
problem (unknown scenario or thread, malformed thread id, thread not waiting, bad decision,
a decision the approval node refused, unknown hypothesis, unknown model, `--model anthropic`
without `ANTHROPIC_API_KEY`); 2 when the run fails or the state file cannot be used.
Every failure prints one sentence to stderr naming the cause, and every string a model or a
scenario wrote is escaped before it is printed.
"""

import json
import math
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, NoReturn, get_args

import typer
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from pydantic import ValidationError

from agentops import __version__
from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.evals.commands import dataset_command, eval_command
from agentops.evals.compare import compare_command
from agentops.graph import build_graph, checkpoint_serializer
from agentops.models import (
    MissingApiKeyError,
    ModelFactory,
    ModelName,
    model_factory,
    redact_api_key,
)
from agentops.nodes.verify import verify_rounds
from agentops.sources.base import SIGNAL_ID_PATTERN
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.sources.simulator import SimulatorSource, ledger_path_for
from agentops.state import (
    INITIAL_INVESTIGATION_BUDGET,
    ApprovalChoice,
    Hypothesis,
    IncidentState,
    OperatorRole,
    new_incident,
)

USAGE_ERROR = 1
RUN_FAILED = 2
RECURSION_LIMIT = 60
# A failure message is shown cut to this many characters: it may quote a long model string.
ERROR_TEXT_MAX_LENGTH = 200
SCRIPTED_MODEL = "scripted"
LIVE_MODEL = "anthropic"
MODEL_METADATA_KEY = "agentops_model"
STATE_DB = Path("agentops.sqlite")
AUDIT_DIR = Path("audit")
SCENARIOS_DIR = Path("scenarios")
DECISIONS: tuple[str, ...] = get_args(ApprovalChoice)
OPERATOR_ROLES: tuple[str, ...] = get_args(OperatorRole)
HISTORY_TOP_HYPOTHESES = 2

ModelOption = Annotated[
    str,
    typer.Option(
        "--model",
        help=f"{SCRIPTED_MODEL} (no API key) or {LIVE_MODEL} (reads ANTHROPIC_API_KEY).",
    ),
]
DevOption = Annotated[
    bool,
    typer.Option("--dev", help="With --model anthropic, run every role on the cheaper dev model."),
]
StateDbOption = Annotated[Path, typer.Option("--state-db", help="The SQLite checkpoint file.")]
AuditDirOption = Annotated[
    Path, typer.Option("--audit-dir", help="Directory for the audit log and simulator ledger.")
]
ScenariosDirOption = Annotated[
    Path, typer.Option("--scenarios-dir", help="Directory holding the scenario files.")
]
ThreadArgument = Annotated[str, typer.Argument(help="The thread id `run` printed.")]

app = typer.Typer(help="Investigate a simulated production incident with cited evidence.")
app.command("eval")(eval_command)
app.command("dataset")(dataset_command)
app.command("compare")(compare_command)


def print_version_and_exit(is_requested: bool) -> None:
    if is_requested:
        typer.echo(f"agentops {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=print_version_and_exit,
            is_eager=True,
            help="Print the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Investigate a simulated production incident with cited evidence."""


@app.command()
def run(
    scenario: Annotated[
        str, typer.Option("--scenario", help="Scenario id: a file stem in the scenarios directory.")
    ],
    model: ModelOption,
    dev: DevOption = False,
    thread: Annotated[
        str | None,
        typer.Option("--thread", help="Thread id for the new incident; generated when absent."),
    ] = None,
    scenarios_dir: ScenariosDirOption = SCENARIOS_DIR,
    state_db: StateDbOption = STATE_DB,
    audit_dir: AuditDirOption = AUDIT_DIR,
) -> None:
    """Investigate one scenario until the approval pause or an escalation."""
    models = models_for(model, dev)
    if thread is not None:
        check_thread_id(thread)
    loaded = load_named_scenario(scenario, scenarios_dir)
    incident = new_incident(
        loaded.service,
        loaded.symptoms,
        opened_at=loaded.incident_time,
        incident_id=thread,
        scenario_id=loaded.id,
    )
    with open_checkpoints(state_db) as saver:
        thread_run = ThreadRun.start(loaded, incident["incident_id"], saver, audit_dir, models)
        if thread_run.exists():
            fail(USAGE_ERROR, f"Thread {thread_run.thread_id!r} already exists; choose another.")
        thread_run.invoke(incident)
        thread_run.print_outcome()


@app.command()
def approve(
    thread: ThreadArgument,
    decision: Annotated[str, typer.Option("--decision", help="approve, reject or edit.")],
    reason: Annotated[
        str | None,
        typer.Option(
            "--reason",
            help="Why, required for reject. Describe the evidence, not people.",
        ),
    ] = None,
    remediation_file: Annotated[
        Path | None,
        typer.Option("--remediation-file", help="JSON file holding the edited remediation."),
    ] = None,
    operator: Annotated[
        str, typer.Option("--operator", help="Your role: operator, on-call or reviewer.")
    ] = "operator",
    crash_after_apply: Annotated[
        bool,
        typer.Option(
            "--crash-after-apply",
            hidden=True,
            help="Demo only: end the process right after the action is applied and recorded.",
        ),
    ] = False,
    model: ModelOption = SCRIPTED_MODEL,
    dev: DevOption = False,
    scenarios_dir: ScenariosDirOption = SCENARIOS_DIR,
    state_db: StateDbOption = STATE_DB,
    audit_dir: AuditDirOption = AUDIT_DIR,
) -> None:
    """Record an operator's decision on the pending proposal and continue the thread."""
    models = models_for(model, dev)
    answer = operator_answer(decision, reason, remediation_file, operator)
    with opened_thread(thread, state_db, audit_dir, scenarios_dir, models) as thread_run:
        if not thread_run.snapshot().interrupts:
            fail(USAGE_ERROR, f"Thread {thread!r} is not waiting for approval.")
        # The crash switch reaches the executor only through this run's configurable (ADR 0002).
        switches = {"crash_after_apply": True} if crash_after_apply else {}
        thread_run.invoke(Command(resume=answer), **switches)
        thread_run.print_outcome()
        refusal = thread_run.refusal()
        if refusal is not None:
            fail(USAGE_ERROR, f"The decision was not recorded: {refusal}")


@app.command()
def resume(
    thread: ThreadArgument,
    model: ModelOption = SCRIPTED_MODEL,
    dev: DevOption = False,
    scenarios_dir: ScenariosDirOption = SCENARIOS_DIR,
    state_db: StateDbOption = STATE_DB,
    audit_dir: AuditDirOption = AUDIT_DIR,
) -> None:
    """Continue a thread that stopped before its end; a thread waiting for a decision is shown."""
    models = models_for(model, dev)
    with opened_thread(thread, state_db, audit_dir, scenarios_dir, models) as thread_run:
        snapshot = thread_run.snapshot()
        if snapshot.next and not snapshot.interrupts:
            thread_run.invoke(None)
        thread_run.print_outcome()


@app.command()
def history(
    thread: ThreadArgument,
    scenarios_dir: ScenariosDirOption = SCENARIOS_DIR,
    state_db: StateDbOption = STATE_DB,
    audit_dir: AuditDirOption = AUDIT_DIR,
) -> None:
    """Print one line per checkpoint of the thread, newest first. No model is called, so the
    scripted models stand in."""
    models = models_for(SCRIPTED_MODEL, dev=False)
    with opened_thread(
        thread, state_db, audit_dir, scenarios_dir, models, calls_models=False
    ) as thread_run:
        for snapshot in thread_run.graph.get_state_history(thread_run.config()):
            typer.echo(history_line(snapshot))


@app.command()
def fork(
    thread: ThreadArgument,
    at: Annotated[str, typer.Option("--at", help="A checkpoint id printed by `history`.")],
    scores: Annotated[
        list[str], typer.Option("--set", help="hypothesis=score, from 0 to 1; repeatable.")
    ],
    model: ModelOption = SCRIPTED_MODEL,
    dev: DevOption = False,
    scenarios_dir: ScenariosDirOption = SCENARIOS_DIR,
    state_db: StateDbOption = STATE_DB,
    audit_dir: AuditDirOption = AUDIT_DIR,
) -> None:
    """Start a new thread from a past checkpoint with changed scores, and continue it."""
    models = models_for(model, dev)
    requested = parse_scores(scores)
    with opened_thread(thread, state_db, audit_dir, scenarios_dir, models) as thread_run:
        snapshot = checkpoint_named(thread_run, at)
        hypotheses = rescored(snapshot.values.get("hypotheses", []), requested, at)
        fork_run = thread_run.next_fork()
        # Written as if triage had produced it, so the router decides what to read next.
        fork_run.graph.update_state(
            fork_run.config(), {**snapshot.values, "hypotheses": hypotheses}, as_node="triage"
        )
        fork_run.invoke(None)
        typer.echo(f"forked from thread {thread} at checkpoint {at}")
        fork_run.print_outcome()


@dataclass(frozen=True)
class ThreadRun:
    """One thread of the graph over an open checkpoint file."""

    graph: CompiledStateGraph
    saver: SqliteSaver
    scenario: Scenario
    thread_id: str
    audit_dir: Path
    models: ModelFactory

    @classmethod
    def start(
        cls,
        scenario: Scenario,
        thread_id: str,
        saver: SqliteSaver,
        audit_dir: Path,
        models: ModelFactory,
    ) -> "ThreadRun":
        """The graph for `scenario`, its simulator remembering applied actions in the ledger
        file of `thread_id`: a fork has its own ledger and never inherits the original's applied
        actions. Exits 2 when that ledger cannot be read; prints one stderr notice per line a
        crash tore mid-write and the simulator skipped."""
        ledger_path = ledger_path_for(audit_dir, thread_id)
        try:
            source = SimulatorSource(scenario, ledger_path=ledger_path)
        except ValueError as error:
            fail(RUN_FAILED, f"The ledger could not be used: {error}")
        # Not an audit event: the notice concerns the simulator's own file, not the incident.
        for line_number in source.torn_ledger_lines:
            typer.echo(
                escape_for_terminal(f"ledger: skipped torn line {line_number} in {ledger_path}"),
                err=True,
            )
        graph = build_graph(
            source,
            models.graph_models(scenario),  # type: ignore[arg-type]
            default_registry(),
            AuditLog(audit_dir),
            saver,
        )
        return cls(
            graph=graph,
            saver=saver,
            scenario=scenario,
            thread_id=thread_id,
            audit_dir=audit_dir,
            models=models,
        )

    def config(self, **switches: bool) -> RunnableConfig:
        return {
            "configurable": {"thread_id": self.thread_id, **switches},
            # Saved in every checkpoint's metadata, so a later command can refuse to continue the
            # thread on a different model (`check_same_model`).
            "metadata": {MODEL_METADATA_KEY: self.models.label},
            "recursion_limit": RECURSION_LIMIT,
        }

    def exists(self) -> bool:
        return self.saver.get_tuple(self.config()) is not None

    def snapshot(self) -> StateSnapshot:
        return self.graph.get_state(self.config())

    def invoke(self, value: Any, **switches: bool) -> None:
        """Run until the graph pauses or ends, saving each checkpoint before the next step."""
        try:
            self.graph.invoke(value, self.config(**switches), durability="sync")
        except Exception as error:  # the command's outer boundary: any failure becomes exit 2
            text = str(error)[:ERROR_TEXT_MAX_LENGTH]
            fail(RUN_FAILED, f"The run failed: {type(error).__name__}: {text}")

    def refusal(self) -> str | None:
        """Why the node refused the last answer and asked again, or None."""
        return self.snapshot().values.get("approval_error")

    def next_fork(self) -> "ThreadRun":
        """A run under the first unused id `<thread>-fork-<n>`, with a ledger of its own."""
        number = 1
        while replace(self, thread_id=f"{self.thread_id}-fork-{number}").exists():
            number += 1
        fork_id = f"{self.thread_id}-fork-{number}"
        return ThreadRun.start(self.scenario, fork_id, self.saver, self.audit_dir, self.models)

    def print_outcome(self) -> None:
        snapshot = self.snapshot()
        pending = snapshot.interrupts[0].value if snapshot.interrupts else None
        typer.echo(
            report(
                IncidentState(**snapshot.values),
                thread_id=self.thread_id,
                audit_dir=self.audit_dir,
                pending=pending,
            )
        )


@contextmanager
def open_checkpoints(state_db: Path) -> Iterator[SqliteSaver]:
    """The checkpointer over `state_db`. Exits 2 naming the path when it cannot be used."""
    try:
        connection = sqlite3.connect(state_db, check_same_thread=False)
    except sqlite3.Error as error:
        fail(RUN_FAILED, f"The state file {state_db} cannot be used: {type(error).__name__}.")
    with closing(connection):
        saver = SqliteSaver(connection, serde=checkpoint_serializer())
        try:
            saver.setup()
        except sqlite3.Error as error:
            fail(RUN_FAILED, f"The state file {state_db} cannot be used: {type(error).__name__}.")
        yield saver


@contextmanager
def opened_thread(
    thread_id: str,
    state_db: Path,
    audit_dir: Path,
    scenarios_dir: Path,
    models: ModelFactory,
    *,
    calls_models: bool = True,
) -> Iterator[ThreadRun]:
    """An existing thread, rebuilt from the scenario named in its latest checkpoint.

    The thread id is checked before any file is opened, and a missing state file means an
    unknown thread: neither creates a file. When the command `calls_models`, it exits 1 unless
    `models` is the model the thread last ran on, before anything runs.
    """
    check_thread_id(thread_id)
    if not state_db.exists():
        fail(USAGE_ERROR, f"Unknown thread {thread_id!r}: {state_db} holds no checkpoints.")
    with open_checkpoints(state_db) as saver:
        latest = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        if latest is None:
            fail(USAGE_ERROR, f"Unknown thread {thread_id!r}: no checkpoint in {state_db}.")
        scenario_id = latest.checkpoint["channel_values"].get("scenario_id")
        if not isinstance(scenario_id, str):
            fail(
                USAGE_ERROR, f"Thread {thread_id!r} has no usable checkpoint: it names no scenario."
            )
        if calls_models:
            check_same_model(thread_id, latest.metadata.get(MODEL_METADATA_KEY), models)
        scenario = load_named_scenario(scenario_id, scenarios_dir)
        yield ThreadRun.start(scenario, thread_id, saver, audit_dir, models)


def check_same_model(thread_id: str, recorded: object, models: ModelFactory) -> None:
    """Exit 1 when the thread last ran on another `--model` than `models`. Without this, an
    `approve` that forgot `--model anthropic` resumed a live thread on the scripted model, which
    has no answers for it: the run failed after the approval was spent (live run, 2026-10-12).
    A thread saved before the model was recorded is not checked."""
    if not isinstance(recorded, str) or recorded == models.label:
        return
    fail(
        USAGE_ERROR,
        f"Thread {thread_id!r} runs on --model {recorded}; pass --model {recorded} to continue it.",
    )


def check_thread_id(thread_id: str) -> None:
    """Exit 1 unless the thread id has the signal id shape, so it can never name a path."""
    if re.fullmatch(SIGNAL_ID_PATTERN, thread_id) is None:
        fail(
            USAGE_ERROR,
            f"The thread id {thread_id!r} is not valid; use lower-case letters, digits, - and _.",
        )


def check_model_name(model: str) -> ModelName:
    """`model` as a model name; exits 1 for any other value."""
    if model == SCRIPTED_MODEL:
        return "scripted"
    if model == LIVE_MODEL:
        return "anthropic"
    fail(USAGE_ERROR, f"Unknown model {model!r}; choose {SCRIPTED_MODEL} or {LIVE_MODEL}.")


def models_for(model: str, dev: bool) -> ModelFactory:
    """The models behind `--model` and `--dev`. Exits 1 naming `ANTHROPIC_API_KEY`, never its
    value, when the live model is asked for without a key; nothing is built or written first."""
    name = check_model_name(model)
    try:
        return model_factory(name, dev)
    except MissingApiKeyError as error:
        fail(USAGE_ERROR, str(error))


def load_named_scenario(scenario_id: str, scenarios_dir: Path) -> Scenario:
    """Load `<scenarios_dir>/<scenario_id>.json`, refusing any id that is not a listed file stem.

    The id must match the scenario id pattern, so no path separator or `..` can reach the
    filesystem and only files inside `scenarios_dir` are ever opened.
    """
    if not scenarios_dir.is_dir():
        fail(USAGE_ERROR, f"The scenarios directory {scenarios_dir} does not exist.")
    available = list_scenario_ids(scenarios_dir)
    if not re.fullmatch(SIGNAL_ID_PATTERN, scenario_id) or scenario_id not in available:
        fail(
            USAGE_ERROR,
            f"Unknown scenario {scenario_id!r}; available: {', '.join(available) or 'none'}.",
        )
    path = scenarios_dir / f"{scenario_id}.json"
    try:
        return load_scenario(path)
    except (UnicodeDecodeError, OSError) as error:
        fail(USAGE_ERROR, f"The scenario file {path} could not be read: {type(error).__name__}.")
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "the file as a whole"
        fail(USAGE_ERROR, f"The scenario file {path} is invalid at {field}: {first['msg']}.")


def operator_answer(
    decision: str, reason: str | None, remediation_file: Path | None, operator: str
) -> dict[str, Any]:
    """The resume value for the approval node. The node validates it again in full."""
    if decision not in DECISIONS:
        fail(USAGE_ERROR, f"Unknown decision {decision!r}; choose {', '.join(DECISIONS)}.")
    if operator not in OPERATOR_ROLES:
        fail(
            USAGE_ERROR, f"Unknown operator role {operator!r}; choose {', '.join(OPERATOR_ROLES)}."
        )
    answer: dict[str, Any] = {"decision": decision, "operator": operator}
    if reason is not None:
        answer["reason"] = reason
    if remediation_file is not None:
        answer["remediation"] = read_remediation_file(remediation_file)
    return answer


def read_remediation_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(
            USAGE_ERROR,
            f"The remediation file {path} is not readable JSON: {type(error).__name__}.",
        )


def parse_scores(assignments: list[str]) -> dict[str, float]:
    """`name=score` pairs as a mapping. Exits 1 on a pair without `=` or a score outside 0 to 1."""
    scores: dict[str, float] = {}
    for assignment in assignments:
        name, separator, text = assignment.rpartition("=")
        try:
            score = float(text)
        except ValueError:
            score = math.nan
        if not separator or not name or not 0.0 <= score <= 1.0:
            fail(USAGE_ERROR, f"--set {assignment!r} must be hypothesis=score, from 0 to 1.")
        scores[name] = score
    return scores


def checkpoint_named(thread_run: ThreadRun, checkpoint_id: str) -> StateSnapshot:
    for snapshot in thread_run.graph.get_state_history(thread_run.config()):
        if checkpoint_id_of(snapshot) == checkpoint_id:
            return snapshot
    fail(USAGE_ERROR, f"Unknown checkpoint {checkpoint_id!r} in thread {thread_run.thread_id}.")


def checkpoint_id_of(snapshot: StateSnapshot) -> str:
    return str(snapshot.config.get("configurable", {}).get("checkpoint_id", ""))


def rescored(
    hypotheses: list[Hypothesis], requested: dict[str, float], checkpoint_id: str
) -> list[Hypothesis]:
    """The hypotheses with the requested scores. Exits 1 naming any name not among them."""
    known = {item.name for item in hypotheses}
    unknown = sorted(set(requested) - known)
    if unknown:
        fail(
            USAGE_ERROR,
            f"Unknown hypothesis {', '.join(unknown)} at checkpoint {checkpoint_id}; known: "
            f"{', '.join(sorted(known)) or 'none'}.",
        )
    return [
        item.model_copy(update={"score": requested[item.name]}) if item.name in requested else item
        for item in hypotheses
    ]


def escape_for_terminal(text: str) -> str:
    """Show control characters (newline, ESC and the like) as visible escapes, and replace the
    API key's value wherever it appears.

    Applied to every string a model wrote before it is printed, so model output cannot start a
    fake line or send terminal control sequences, and an SDK error quoting the key prints none.
    """
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in redact_api_key(text)
    )


def history_line(snapshot: StateSnapshot) -> str:
    """Checkpoint id, time, next nodes, status, loops, top hypotheses and evidence count."""
    values = snapshot.values
    ranked = sorted(values.get("hypotheses", []), key=lambda item: item.score, reverse=True)
    top = ", ".join(
        f"{escape_for_terminal(item.name)} ({item.score:.2f})"
        for item in ranked[:HISTORY_TOP_HYPOTHESES]
    )
    return (
        f"{checkpoint_id_of(snapshot)} {snapshot.created_at} "
        f"next: {','.join(snapshot.next) or 'end'} status: {values.get('status', 'none')} "
        f"loops: {values.get('loops', 0)} top: {top or 'none'} "
        f"evidence: {len(values.get('evidence', []))}"
    )


def report(
    state: IncidentState,
    *,
    thread_id: str,
    audit_dir: Path,
    pending: dict[str, Any] | None = None,
) -> str:
    """The thread's outcome in plain lines, the pending proposal when it waits for a decision,
    and where the audit log is. Every model string is escaped for the terminal."""
    lines = [f"incident: {state['incident_id']}", f"thread: {thread_id}"]
    lines += investigation_lines(state)
    lines += action_lines(state)
    if pending is not None:
        lines += pending_proposal_lines(pending)
    lines.append(f"audit: {audit_dir / (state['incident_id'] + '.jsonl')}")
    lines.append(closing_sentence(state, thread_id, is_waiting=pending is not None))
    return "\n".join(lines)


def investigation_lines(state: IncidentState) -> list[str]:
    lines = [
        f"status: {state['status']}",
        f"loops: {state['loops']}",
        f"budget remaining: {state['investigation_budget']} of {INITIAL_INVESTIGATION_BUDGET}",
        "hypotheses:",
    ]
    lines += [
        f"  - {escape_for_terminal(item.name)} (score {item.score:.2f})"
        for item in state["hypotheses"]
    ]
    lines.append(f"evidence: {len(state['evidence'])} items")
    failed_actions = sum(1 for action in state["actions"] if not action.ok)
    lines.append(f"actions: {len(state['actions'])} ({failed_actions} failed)")
    if state["root_cause"] is not None:
        lines.append(f"root cause: {escape_for_terminal(state['root_cause'].label)}")
        lines.append(f"  {escape_for_terminal(state['root_cause'].explanation)}")
    if state["escalation_reason"] is not None:
        lines.append(f"escalation: {state['escalation_reason']}")
    if state["escalation"] is not None:
        lines.append(f"  {escape_for_terminal(state['escalation'].summary)}")
    if state["remediation"] is not None:
        remediation = state["remediation"]
        lines.append(
            f"remediation: {escape_for_terminal(remediation.action)} on "
            f"{escape_for_terminal(remediation.target)} (risk {remediation.risk})"
        )
    return lines


def action_lines(state: IncidentState) -> list[str]:
    """The approval, the execution, the verify rounds and the postmortem, when they exist."""
    lines: list[str] = []
    approval = state["approval"]
    if approval is not None:
        lines.append(f"approval: {approval.decision} by {approval.operator}")
    execution = state["execution"]
    if execution is not None:
        lines.append(
            f"execution: {escape_for_terminal(execution.result)} {execution.action} on "
            f"{execution.target}, key {execution.idempotency_key}"
        )
    rounds = verify_rounds(state)
    if rounds:
        lines.append(f"verify rounds: {rounds}")
    if state["postmortem"] is not None:
        lines.append(f"postmortem: {escape_for_terminal(state['postmortem'].summary)}")
    return lines


def pending_proposal_lines(pending: dict[str, Any]) -> list[str]:
    """The dry run and fingerprint of the proposal waiting for a decision, and the error of the
    last refused answer when there is one."""
    lines = [
        f"pending approval: {escape_for_terminal(str(pending.get('dry_run', '')))}",
        f"  idempotency key: {escape_for_terminal(str(pending.get('idempotency_key', '')))}",
    ]
    if "error" in pending:
        lines.append(f"  last answer refused: {escape_for_terminal(str(pending['error']))}")
    return lines


def closing_sentence(state: IncidentState, thread_id: str, *, is_waiting: bool) -> str:
    """What happens next, and whether anything was changed so far."""
    changes = changes_sentence(state)
    if is_waiting:
        return (
            f"Waiting for an operator's decision on thread {thread_id}: "
            f"agentops approve {thread_id} --decision approve|reject|edit. {changes}"
        )
    if state["status"] == "escalated":
        return f"Handed to a human. {changes}"
    return f"The run ended with status {state['status']}. {changes}"


def changes_sentence(state: IncidentState) -> str:
    """Whether an approved action changed the system: applied, possibly applied (the apply
    raised, so its effect is unknown), or nothing changed."""
    # Action evidence outlives the execution record, which a later loop replaces.
    actions = [item.payload for item in state["evidence"] if item.kind == "action"]
    if any(payload.get("applied") is True for payload in actions):
        return "An approved action was applied."
    if any(payload.get("effect") == "unknown" for payload in actions):
        return "An approved action may have been applied; its effect is unknown."
    return "Nothing was changed."


def fail(exit_code: int, sentence: str) -> NoReturn:
    typer.echo(escape_for_terminal(sentence), err=True)
    raise typer.Exit(code=exit_code)
