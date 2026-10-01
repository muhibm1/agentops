"""E10, E50: the append-only JSONL audit log, one file per incident."""

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from agentops.actions import audit
from agentops.actions.audit import AuditLog, InvalidIncidentIdError

TRAVERSAL_IDS = ["../x", "a/b", "C:\\x", ""]


class OpenRecorder:
    """Wraps the built-in `open` and remembers the mode of every call."""

    def __init__(self) -> None:
        self.modes: list[str] = []

    def __call__(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        self.modes.append(mode)
        return builtins.open(file, mode, *args, **kwargs)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> OpenRecorder:
    recording = OpenRecorder()
    monkeypatch.setattr(audit, "open", recording, raising=False)
    return recording


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# E10


def test_three_appends_write_three_parseable_lines_to_one_growing_file(
    tmp_path: Path, recorder: OpenRecorder
) -> None:
    log = AuditLog(tmp_path / "audit")
    sizes: list[int] = []

    for event in ["proposal", "approval", "dry_run"]:
        log.append("clean_bad_deploy", "thread-1", {"event": event, "idempotency_key": "k1"})
        sizes.append(log.path_for("clean_bad_deploy").stat().st_size)

    files = list((tmp_path / "audit").iterdir())
    assert files == [tmp_path / "audit" / "clean_bad_deploy.jsonl"]
    assert sizes == sorted(set(sizes))
    assert recorder.modes == ["a", "a", "a"]
    lines = read_lines(files[0])
    assert [line["event"] for line in lines] == ["proposal", "approval", "dry_run"]


def test_every_line_carries_the_event_time_incident_and_thread(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)

    log.append("clean_bad_deploy", "thread-1", {"event": "proposal", "action": "rollback_deploy"})
    log.append("clean_bad_deploy", "fork-2", {"event": "escalation", "reason": "ambiguous"})

    lines = read_lines(tmp_path / "clean_bad_deploy.jsonl")
    assert [line["thread_id"] for line in lines] == ["thread-1", "fork-2"]
    for line in lines:
        assert line["incident_id"] == "clean_bad_deploy"
        assert line["at"]
        assert line["event"] in {"proposal", "escalation"}
    assert lines[0]["action"] == "rollback_deploy"


def test_an_existing_file_is_appended_to_never_rewritten(tmp_path: Path) -> None:
    earlier = tmp_path / "clean_bad_deploy.jsonl"
    earlier.write_text('{"event": "proposal"}\n', encoding="utf-8")

    AuditLog(tmp_path).append("clean_bad_deploy", "thread-1", {"event": "approval"})

    text = earlier.read_text(encoding="utf-8")
    assert text.startswith('{"event": "proposal"}\n')
    assert len(text.splitlines()) == 2


def test_an_unknown_event_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="deleted"):
        AuditLog(tmp_path).append("clean_bad_deploy", "thread-1", {"event": "deleted"})


@pytest.mark.parametrize("reserved", ["at", "incident_id", "thread_id"])
def test_a_record_cannot_override_the_fields_the_log_stamps(tmp_path: Path, reserved: str) -> None:
    with pytest.raises(ValueError, match=reserved):
        AuditLog(tmp_path).append(
            "clean_bad_deploy", "thread-1", {"event": "proposal", reserved: "forged"}
        )


# E50


@pytest.mark.parametrize("incident_id", TRAVERSAL_IDS)
def test_an_incident_id_outside_the_signal_pattern_is_refused_before_any_open(
    tmp_path: Path, recorder: OpenRecorder, incident_id: str
) -> None:
    directory = tmp_path / "audit"

    with pytest.raises(InvalidIncidentIdError):
        AuditLog(directory).append(incident_id, "thread-1", {"event": "proposal"})

    assert recorder.modes == []
    assert not directory.exists()
