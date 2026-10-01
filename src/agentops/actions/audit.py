"""The append-only audit log: one JSONL file per incident, one JSON object per line.

Every write opens the file in append mode, writes one line, flushes, syncs to disk and closes,
so a crash can lose at most the line being written and never rewrites an earlier one.
"""

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentops.sources.base import SIGNAL_ID_PATTERN

AUDIT_EVENTS = frozenset(
    {"proposal", "approval", "precondition", "dry_run", "apply", "verify", "escalation"}
)
# Fields the log stamps itself; a caller's record may not supply or override them.
STAMPED_FIELDS = ("at", "incident_id", "thread_id")


class InvalidIncidentIdError(ValueError):
    """The incident id would not name a single file inside the audit directory."""


class AuditLog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, incident_id: str) -> Path:
        """The incident's audit file. Raises InvalidIncidentIdError before touching the disk."""
        if re.fullmatch(SIGNAL_ID_PATTERN, incident_id) is None:
            raise InvalidIncidentIdError(
                "The incident id must match the signal id pattern "
                f"{SIGNAL_ID_PATTERN}; it is refused as an audit file name."
            )
        return self.directory / f"{incident_id}.jsonl"

    def records(self, incident_id: str) -> list[dict[str, Any]]:
        """The incident's audit records in write order; a bad id raises InvalidIncidentIdError."""
        return read_records(self.path_for(incident_id))

    def append(self, incident_id: str, thread_id: str, record: Mapping[str, Any]) -> None:
        """Append `record` as one line, stamped with the time, the incident and the thread.

        `record["event"]` must be one of AUDIT_EVENTS. Raises before opening any file when the
        incident id, the thread id, the event or a stamped field is wrong.
        """
        path = self.path_for(incident_id)
        check_record(thread_id, record)
        line = {
            "event": record["event"],
            "at": datetime.now(UTC).isoformat(),
            "incident_id": incident_id,
            "thread_id": thread_id,
            **{key: value for key, value in record.items() if key != "event"},
        }
        # default=str writes datetimes and other plain values as text instead of failing.
        text = json.dumps(line, default=str) + "\n"
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every complete JSON line of an audit file; a missing file is empty and a line cut short by
    a crash is skipped, so a reader never fails on the damage it is looking for."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def check_record(thread_id: str, record: Mapping[str, Any]) -> None:
    if not thread_id:
        raise ValueError("An audit record needs a thread id.")
    event = record.get("event")
    if event not in AUDIT_EVENTS:
        raise ValueError(f"Unknown audit event {event!r}; expected one of {sorted(AUDIT_EVENTS)}.")
    overridden = [name for name in STAMPED_FIELDS if name in record]
    if overridden:
        raise ValueError(f"An audit record may not set {', '.join(overridden)}; the log stamps it.")
