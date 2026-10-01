"""E32: the test-count floor script fails below the floor and passes at it (R17)."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FLOOR_SCRIPT = REPO_ROOT / "scripts" / "check_test_count.py"


def write_junit(path: Path, tests: int, skipped: int) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<testsuites>"
        f'<testsuite name="pytest" errors="0" failures="0" skipped="{skipped}" tests="{tests}" />'
        "</testsuites>\n",
        encoding="utf-8",
    )
    return path


def run_floor_script(junit_path: Path, floor: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FLOOR_SCRIPT), str(junit_path), str(floor)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_floor_script_fails_below_the_floor_and_passes_at_it(tmp_path: Path) -> None:
    below_floor = write_junit(tmp_path / "below.xml", tests=29, skipped=0)
    at_floor = write_junit(tmp_path / "at.xml", tests=31, skipped=1)

    failing_run = run_floor_script(below_floor, 30)
    passing_run = run_floor_script(at_floor, 30)

    assert failing_run.returncode == 1
    assert "29" in failing_run.stdout + failing_run.stderr
    assert "30" in failing_run.stdout + failing_run.stderr
    assert passing_run.returncode == 0


def test_floor_script_reports_bad_arguments_in_one_sentence_and_exits_one(tmp_path: Path) -> None:
    junit_path = write_junit(tmp_path / "ok.xml", tests=5, skipped=0)
    broken_path = tmp_path / "broken.xml"
    broken_path.write_text("<testsuites>", encoding="utf-8")

    for junit, floor in [
        (junit_path, "thirty"),
        (tmp_path / "missing.xml", "1"),
        (broken_path, "1"),
    ]:
        result = subprocess.run(
            [sys.executable, str(FLOOR_SCRIPT), str(junit), floor],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        assert "Cannot check the floor" in result.stderr
