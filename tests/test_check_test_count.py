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
