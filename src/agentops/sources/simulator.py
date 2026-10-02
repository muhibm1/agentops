"""`SimulatorSource`: an `EvidenceSource` that serves one scenario's signals from memory.

It holds its own deep copy of the signals, so applying an action changes this source only,
never the loaded `Scenario` or another source built from it. Every read returns copies for the
same reason. The grading key and scripted outputs have no accessor here.

Given a ledger path, it also remembers applied actions across processes: each apply appends one
JSON line to that file before returning, and a source built on the file replays it (ADR 0002).
"""

import json
import os
import re
from pathlib import Path

from pydantic import ValidationError

from agentops.sources.base import (
    SIGNAL_ID_PATTERN,
    ActionRequest,
    ActionResult,
    AppliedAction,
    ConfigValue,
    DependencyStatus,
    Deployment,
    LogLine,
    Series,
    Window,
)
from agentops.sources.scenario import PostActionWorld, Scenario, Signals, combined_world_key

LEDGER_SUFFIX = ".ledger.jsonl"


class LedgerError(ValueError):
    """The ledger file cannot be used: a bad name, or a line that is not an applied action."""


def ledger_path_for(directory: Path, incident_id: str) -> Path:
    """The ledger file for `incident_id` in `directory`. Opens nothing.

    Raises `LedgerError` when the id is not a signal id, so it can never leave `directory`.
    """
    if re.fullmatch(SIGNAL_ID_PATTERN, incident_id) is None:
        raise LedgerError(f"The incident id {incident_id!r} is not a valid signal id.")
    return directory / f"{incident_id}{LEDGER_SUFFIX}"


class SimulatorSource:
    def __init__(self, scenario: Scenario, ledger_path: Path | None = None) -> None:
        """Raises `LedgerError` when `ledger_path` is not named `<incident id>.ledger.jsonl` or
        holds a line that is not an action this scenario can model."""
        self._service = scenario.service
        self._signals: Signals = scenario.signals.model_copy(deep=True)
        self._worlds: dict[str, PostActionWorld] = {
            action: world.model_copy(deep=True)
            for action, world in scenario.post_action_world.items()
        }
        self._ledger: list[AppliedAction] = []
        # Keys of the worlds already merged, so one world is never merged twice.
        self._merged_worlds: set[str] = set()
        self._ledger_path = ledger_path
        # Numbers of ledger lines skipped on replay because a crash tore them mid-write.
        self.torn_ledger_lines: list[int] = []
        if ledger_path is not None:
            check_ledger_file_name(ledger_path)
            self._replay(ledger_path)

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        """Log lines for `service` inside `window`; `query` is a case-insensitive substring."""
        needle = query.casefold() if query else None
        return [
            line.model_copy(deep=True)
            for line in self._signals.logs
            if line.service == service
            and window.contains(line.timestamp)
            and (needle is None or needle in line.message.casefold())
        ]

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        """Named series trimmed to `window`. A series with no point in the window is omitted."""
        if service != self._service:
            return []
        trimmed = [
            series.model_copy(
                update={"points": [p.model_copy() for p in series.points if window.contains(p.at)]}
            )
            for series in self._signals.metrics
            if series.name in names
        ]
        return [series for series in trimmed if series.points]

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        if service != self._service:
            return []
        return [
            deployment.model_copy(deep=True)
            for deployment in self._signals.deployments
            if window.contains(deployment.deployed_at)
        ]

    def get_config(self, service: str) -> dict[str, ConfigValue]:
        if service != self._service:
            return {}
        return dict(self._signals.config)

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        if service != self._service:
            return []
        return [status.model_copy(deep=True) for status in self._signals.dependencies]

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        """Change the served world to the scenario's post-action world for `action.action`.

        Once two or more distinct actions are applied, the world declared for all of them
        together is served instead, when the scenario declares one. A key already in the ledger
        applies nothing and reports `applied=False`. An action the scenario has no post-action
        world for is refused with `ok=False`. Never raises, except when the ledger file cannot
        be written, in which case nothing is applied.
        """
        if any(entry.idempotency_key == idempotency_key for entry in self._ledger):
            return ActionResult(
                ok=True,
                applied=False,
                idempotency_key=idempotency_key,
                detail=f"Key {idempotency_key} was already applied; nothing changed.",
            )
        world_key = self._world_key_after(action.action)
        if world_key is None:
            return ActionResult(
                ok=False,
                applied=False,
                idempotency_key=idempotency_key,
                detail=f"The simulator cannot model the action {action.action}; nothing changed.",
            )
        entry = AppliedAction(
            idempotency_key=idempotency_key,
            action=action.action,
            target=action.target,
            params=dict(action.params),
        )
        if self._ledger_path is not None:
            append_ledger_line(self._ledger_path, entry)
        self._record(entry, world_key)
        return ActionResult(
            ok=True,
            applied=True,
            idempotency_key=idempotency_key,
            detail=f"Applied {action.action} to {action.target}.",
        )

    def ledger(self) -> list[AppliedAction]:
        return [entry.model_copy(deep=True) for entry in self._ledger]

    def _world_key_after(self, action_name: str) -> str | None:
        """The key of the world to serve once `action_name` is applied, or None when the
        scenario declares none: the combined world of every applied action when it has one."""
        applied_names = {entry.action for entry in self._ledger} | {action_name}
        if len(applied_names) > 1:
            combined = combined_world_key(applied_names)
            if combined in self._worlds:
                return combined
        return action_name if action_name in self._worlds else None

    def _record(self, entry: AppliedAction, world_key: str) -> None:
        if world_key not in self._merged_worlds:
            self._merge_world(self._worlds[world_key])
            self._merged_worlds.add(world_key)
        self._ledger.append(entry)

    def _replay(self, ledger_path: Path) -> None:
        """Replays every line of the ledger file into this source.

        Only a final line with no newline after it can be torn by a crash mid-write, and the
        simulator applied nothing for it, so such a line that is not JSON is skipped and its
        number kept in `torn_ledger_lines`. Any other line that is not an applied action this
        scenario can model raises `LedgerError` naming it (D17).
        """
        if not ledger_path.exists():
            return
        complete_lines, final_line = read_ledger_lines(ledger_path)
        for line_number, line in enumerate(complete_lines, start=1):
            self._replay_line(ledger_path, line_number, line)
        if final_line is None:
            return
        final_line_number = len(complete_lines) + 1
        if is_json(final_line):
            self._replay_line(ledger_path, final_line_number, final_line)
        else:
            self.torn_ledger_lines.append(final_line_number)

    def _replay_line(self, ledger_path: Path, line_number: int, line: str) -> None:
        entry = parse_ledger_line(ledger_path, line_number, line)
        world_key = self._world_key_after(entry.action)
        if world_key is None:
            raise LedgerError(
                f"Ledger {ledger_path} line {line_number} names the action "
                f"{entry.action!r}, which this scenario cannot model."
            )
        self._record(entry, world_key)

    def _merge_world(self, world: PostActionWorld) -> None:
        series_by_id = {series.id: series for series in self._signals.metrics}
        for update in world.metrics:
            series_by_id[update.id].points.extend(p.model_copy() for p in update.points)
        self._signals.logs.extend(line.model_copy(deep=True) for line in world.logs)


def check_ledger_file_name(ledger_path: Path) -> None:
    if not ledger_path.name.endswith(LEDGER_SUFFIX):
        raise LedgerError(
            f"The ledger file {ledger_path.name!r} must be named <incident id>{LEDGER_SUFFIX}; "
            "it names no incident id."
        )
    ledger_path_for(ledger_path.parent, ledger_path.name.removesuffix(LEDGER_SUFFIX))


def parse_ledger_line(ledger_path: Path, line_number: int, line: str) -> AppliedAction:
    try:
        return AppliedAction.model_validate_json(line)
    except ValidationError as error:
        raise LedgerError(
            f"Ledger {ledger_path} line {line_number} is not an applied action "
            f"({error.error_count()} problems, first: {error.errors()[0]['msg']})."
        ) from error


def read_ledger_lines(ledger_path: Path) -> tuple[list[str], str | None]:
    """The lines that end in a newline, and the text after the last newline (None when the
    file ends in one). Only that final text can be torn, so only it is decoded leniently: a
    crash can cut a multibyte character, and a cut line is never valid JSON either way."""
    raw = ledger_path.read_bytes()
    complete_part, newline, final_part = raw.rpartition(b"\n")
    complete_lines = (complete_part + newline).decode("utf-8").splitlines()
    if not final_part:
        return complete_lines, None
    return complete_lines, final_part.decode("utf-8", errors="replace")


def is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def append_ledger_line(ledger_path: Path, entry: AppliedAction) -> None:
    """Appends one JSON line and syncs it to disk, so a process killed right after `apply`
    returns still leaves the entry behind. The file is only ever opened in append mode.

    When a crash left the file without a final newline, the entry starts on a fresh line so
    it never joins the torn fragment."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    separator = "" if ends_in_newline_or_is_empty(ledger_path) else "\n"
    with ledger_path.open("a", encoding="utf-8") as ledger_file:
        ledger_file.write(separator + entry.model_dump_json() + "\n")
        ledger_file.flush()
        os.fsync(ledger_file.fileno())


def ends_in_newline_or_is_empty(ledger_path: Path) -> bool:
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        return True
    with ledger_path.open("rb") as ledger_file:
        ledger_file.seek(-1, os.SEEK_END)
        return ledger_file.read(1) == b"\n"
