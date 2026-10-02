"""E35 to E38, E44, E46, F8, F11, A6, A7, A10 and N1: `agentops eval` and `agentops dataset`
(R12 to R15, R17, N1).

Every test runs in its own temporary working directory. The `--held-out` tests build a small
frozen split of their own from copies of two dev scenarios, with a manifest and checksums written
by `write_dataset` into a temporary evals directory; no test scores a committed frozen scenario
(D18).
"""

import json
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from agentops.cli import app
from agentops.evals.dataset import HELD_OUT_IDS, write_dataset
from agentops.evals.run import project_environment, trace_config
from agentops.tracing import API_KEY_VARIABLE, TRACING_SWITCHES, tracing_status

REPOSITORY = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPOSITORY / "scenarios"
EVALS_DIR = REPOSITORY / "evals"
REPOSITORY_DATA = ["--scenarios-dir", str(SCENARIOS_DIR), "--evals-dir", str(EVALS_DIR)]
DEV_SCENARIO_COUNT = 5
EVALUATOR_NAMES = {
    "root_cause_accuracy",
    "correct_outcome",
    "evidence_sufficiency",
    "false_remediation",
    "safety",
    "tool_efficiency",
    "latency",
}
SCRIPTED_SENTENCE = "Scripted model: these numbers measure the harness, not a model."
# Two dev scenarios copied into a temporary split; the second is that split's frozen one.
TEMPORARY_DEV_ID = "clean_bad_deploy"
TEMPORARY_HELD_OUT_ID = "late_partition_zero_rows"
# N1: the offline eval over the dev split, both systems, one repetition.
OFFLINE_EVAL_BUDGET_SECONDS = 120

runner = CliRunner()


@dataclass(frozen=True)
class EvalRun:
    """One `agentops eval` invocation and what it left in its working directory."""

    result: Result
    seconds: float
    working_dir: Path
    rows: list[dict[str, Any]]


@pytest.fixture(autouse=True)
def in_a_temporary_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    clear_tracing(monkeypatch)


def clear_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*TRACING_SWITCHES, API_KEY_VARIABLE):
        monkeypatch.delenv(name, raising=False)


def evaluate(working_dir: Path, *arguments: str) -> EvalRun:
    """`agentops eval --model scripted <arguments> --json out.json` in `working_dir`, timed."""
    json_path = working_dir / "out.json"
    started = time.perf_counter()
    result = runner.invoke(
        app, ["eval", "--model", "scripted", *arguments, "--json", str(json_path)]
    )
    seconds = time.perf_counter() - started
    rows = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else []
    return EvalRun(result=result, seconds=seconds, working_dir=working_dir, rows=rows)


@pytest.fixture(scope="module")
def dev_eval(tmp_path_factory: pytest.TempPathFactory) -> Iterator[EvalRun]:
    """The default run over the repository's dev split, shared by E35, E36 and N1."""
    working_dir = tmp_path_factory.mktemp("dev-eval")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(working_dir)
        clear_tracing(monkeypatch)
        yield evaluate(working_dir, *REPOSITORY_DATA)


def table_rows(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(("graph ", "baseline "))]


def copy_scenario(scenarios_dir: Path, scenario_id: str, new_id: str | None = None) -> Path:
    """Copies a repository scenario into `scenarios_dir`, renamed to `new_id` when given."""
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    document = json.loads((SCENARIOS_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))
    document["id"] = new_id or scenario_id
    path = scenarios_dir / f"{document['id']}.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def temporary_frozen_split(base: Path) -> tuple[Path, Path]:
    """A scenarios directory with two dev copies and an evals directory freezing one of them."""
    scenarios_dir = base / "scenarios"
    evals_dir = base / "evals"
    copy_scenario(scenarios_dir, TEMPORARY_DEV_ID)
    copy_scenario(scenarios_dir, TEMPORARY_HELD_OUT_ID)
    write_dataset(scenarios_dir, evals_dir, force=False, held_out_ids=[TEMPORARY_HELD_OUT_ID])
    return scenarios_dir, evals_dir


def split_options(scenarios_dir: Path, evals_dir: Path) -> list[str]:
    return ["--scenarios-dir", str(scenarios_dir), "--evals-dir", str(evals_dir)]


# E35: both systems over the dev split, with the header, two rows and the failures list, and
# nothing left behind in the working directory but the requested JSON file.
def test_eval_runs_both_systems_over_dev_and_writes_nothing_locally(dev_eval: EvalRun) -> None:
    output = dev_eval.result.stdout

    assert dev_eval.result.exit_code == 0, dev_eval.result.output
    header = output.splitlines()[0]
    for expected in ("scripted", "dev", f"{DEV_SCENARIO_COUNT} scenarios", "repetitions 1"):
        assert expected in header
    assert "tracing: off" in header
    assert SCRIPTED_SENTENCE in output
    assert [row.split()[0] for row in table_rows(output)] == ["graph", "baseline"]
    assert "failures:" in output
    assert not (dev_eval.working_dir / "audit").exists()
    assert not (dev_eval.working_dir / "agentops.sqlite").exists()
    assert [path.name for path in dev_eval.working_dir.iterdir()] == ["out.json"]


# E36: one JSON row per run with all seven evaluator results; the graph is always safe, the
# baseline is not, because it applies without approval.
def test_the_json_output_holds_every_run_and_the_baseline_fails_safety_on_a_trap(
    dev_eval: EvalRun,
) -> None:
    rows = dev_eval.rows

    assert len(rows) == 2 * DEV_SCENARIO_COUNT
    assert all(set(row["evaluators"]) == EVALUATOR_NAMES for row in rows)
    graph_rows = [row for row in rows if row["system"] == "graph"]
    baseline_rows = [row for row in rows if row["system"] == "baseline"]
    assert len(graph_rows) == len(baseline_rows) == DEV_SCENARIO_COUNT
    assert all(row["evaluators"]["safety"] is True for row in graph_rows)
    assert any(row["evaluators"]["safety"] is False for row in baseline_rows)
    assert all(row["outcome"]["system"] == row["system"] for row in rows)


# N1: the offline eval finishes within its budget on this machine.
def test_the_offline_eval_finishes_within_the_budget(dev_eval: EvalRun) -> None:
    print(f"N1: agentops eval --model scripted over dev took {dev_eval.seconds:.2f} s")

    assert dev_eval.result.exit_code == 0, dev_eval.result.output
    assert dev_eval.seconds < OFFLINE_EVAL_BUDGET_SECONDS


# E37: repetitions multiply the runs, --system narrows the table, and the bounds are enforced.
def test_repetitions_and_system_options_bound_the_run(tmp_path: Path) -> None:
    repeated = evaluate(tmp_path, *REPOSITORY_DATA, "--repetitions", "3")
    baseline_only = evaluate(tmp_path, *REPOSITORY_DATA, "--system", "baseline")
    too_few = runner.invoke(app, ["eval", "--model", "scripted", "--repetitions", "0"])
    too_many = runner.invoke(app, ["eval", "--model", "scripted", "--repetitions", "11"])

    assert repeated.result.exit_code == 0, repeated.result.output
    assert len(repeated.rows) == 2 * DEV_SCENARIO_COUNT * 3
    rows = table_rows(repeated.result.stdout)
    assert [row.split()[1] for row in rows] == [str(DEV_SCENARIO_COUNT * 3)] * 2
    assert "min" in repeated.result.stdout
    assert "max" in repeated.result.stdout
    assert baseline_only.result.exit_code == 0, baseline_only.result.output
    assert [row.split()[0] for row in table_rows(baseline_only.result.stdout)] == ["baseline"]
    assert too_few.exit_code == 1
    assert too_many.exit_code == 1
    assert "--repetitions" in too_few.stderr
    assert "--repetitions" in too_many.stderr


# E38 (D18): --held-out over a frozen split built in a temporary directory, with the warning.
def test_the_held_out_flag_scores_a_temporary_frozen_split_with_a_warning(tmp_path: Path) -> None:
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)

    run = evaluate(tmp_path, "--held-out", *split_options(scenarios_dir, evals_dir))

    assert run.result.exit_code == 0, run.result.output
    header = run.result.stdout.splitlines()[0]
    assert "held_out" in header
    assert "1 scenario" in header
    assert "Frozen split" in run.result.stdout
    assert {row["scenario_id"] for row in run.rows} == {TEMPORARY_HELD_OUT_ID}
    for committed_id in HELD_OUT_IDS:
        assert committed_id not in run.result.output


# A10 (D18): one changed byte in the frozen scenario refuses the run before anything runs.
def test_a_checksum_mismatch_refuses_the_held_out_run_before_it_starts(tmp_path: Path) -> None:
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)
    frozen_file = scenarios_dir / f"{TEMPORARY_HELD_OUT_ID}.json"
    content = frozen_file.read_bytes()
    frozen_file.write_bytes(content.replace(b" ", b"\t", 1))

    run = evaluate(tmp_path, "--held-out", *split_options(scenarios_dir, evals_dir))

    assert run.result.exit_code == 1
    assert f"scenarios/{TEMPORARY_HELD_OUT_ID}.json" in run.result.stderr
    assert run.result.stdout == ""
    assert run.rows == []


# F8: a scenario whose run raises is a failed row naming the exception; the table still prints.
def test_a_run_that_raises_becomes_a_failed_row_not_a_crash(tmp_path: Path) -> None:
    scenarios_dir = tmp_path / "scenarios"
    copy_scenario(scenarios_dir, TEMPORARY_DEV_ID)
    broken = copy_scenario(scenarios_dir, TEMPORARY_DEV_ID, new_id="broken_rca")
    document = json.loads(broken.read_text(encoding="utf-8"))
    document["scripted_outputs"]["rca"]["root_cause"]["evidence_ids"] = ["log-999"]
    broken.write_text(json.dumps(document), encoding="utf-8")
    copy_scenario(scenarios_dir, TEMPORARY_HELD_OUT_ID)
    evals_dir = tmp_path / "evals"
    write_dataset(scenarios_dir, evals_dir, force=False, held_out_ids=[TEMPORARY_HELD_OUT_ID])

    run = evaluate(tmp_path, "--system", "graph", *split_options(scenarios_dir, evals_dir))

    assert run.result.exit_code == 0, run.result.output
    by_scenario = {row["scenario_id"]: row["outcome"] for row in run.rows}
    assert by_scenario["broken_rca"]["outcome"] == "failed"
    assert by_scenario["broken_rca"]["failure"] == "CitationError"
    assert by_scenario[TEMPORARY_DEV_ID]["outcome"] == "remediated"
    assert [row.split()[0] for row in table_rows(run.result.stdout)] == ["graph"]
    assert "broken_rca" in run.result.stdout
    assert "CitationError" in run.result.stdout


# A6 (R17): the live model is still a stub, refused before any run.
def test_the_live_model_is_still_a_stub_for_eval(tmp_path: Path) -> None:
    result = runner.invoke(app, ["eval", "--model", "anthropic", *REPOSITORY_DATA])

    assert result.exit_code == 1
    assert "not wired yet" in result.stderr
    assert result.stdout == ""


# A7 (R15): tracing switched on without a key stops the command before any run.
def test_a_half_set_tracing_configuration_stops_eval_before_any_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    run = evaluate(tmp_path, *REPOSITORY_DATA)

    assert run.result.exit_code == 1
    assert API_KEY_VARIABLE in run.result.stderr
    assert run.result.stdout == ""
    assert run.rows == []


# E44: check by default; --write refuses to overwrite without --force and rewrites with it.
def test_the_dataset_command_checks_by_default_and_rewrites_only_with_force(
    tmp_path: Path,
) -> None:
    evals_copy = tmp_path / "evals"
    shutil.copytree(EVALS_DIR, evals_copy)
    (evals_copy / "checksums.sha256").write_text("stale\n", encoding="utf-8")
    copy_options = ["--scenarios-dir", str(SCENARIOS_DIR), "--evals-dir", str(evals_copy)]

    committed = runner.invoke(app, ["dataset", *REPOSITORY_DATA])
    stale = runner.invoke(app, ["dataset", *copy_options])
    without_force = runner.invoke(app, ["dataset", "--write", *copy_options])
    with_force = runner.invoke(app, ["dataset", "--write", "--force", *copy_options])
    rechecked = runner.invoke(app, ["dataset", "--check", *copy_options])

    assert committed.exit_code == 0, committed.output
    assert stale.exit_code == 1
    assert "checksums.sha256" in stale.stderr
    assert without_force.exit_code == 1
    assert "--force" in without_force.stderr
    assert with_force.exit_code == 0, with_force.output
    assert (evals_copy / "checksums.sha256").read_bytes() == (
        EVALS_DIR / "checksums.sha256"
    ).read_bytes()
    assert rechecked.exit_code == 0, rechecked.output


# F11: a missing scenarios directory, or one holding a file that is not JSON, exits 2 naming it.
def test_an_unreadable_scenarios_directory_exits_two_before_any_run(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    unreadable_dir, evals_dir = temporary_frozen_split(tmp_path / "unreadable")
    not_json = unreadable_dir / "not_json.json"
    not_json.write_text("{not json", encoding="utf-8")

    results = [
        runner.invoke(app, ["eval", "--model", "scripted", *split_options(missing, evals_dir)]),
        runner.invoke(app, ["dataset", *split_options(missing, evals_dir)]),
        runner.invoke(
            app, ["eval", "--model", "scripted", *split_options(unreadable_dir, evals_dir)]
        ),
        runner.invoke(app, ["dataset", *split_options(unreadable_dir, evals_dir)]),
    ]

    assert [result.exit_code for result in results] == [2, 2, 2, 2]
    assert str(missing) in results[0].stderr
    assert str(missing) in results[1].stderr
    assert "not_json.json" in results[2].stderr
    assert "not_json.json" in results[3].stderr
    assert all(table_rows(result.stdout) == [] for result in results)


# E46 (R15): runs are named and tagged for LangSmith only when tracing is on.
def test_the_run_config_names_traces_only_when_tracing_is_on() -> None:
    on = tracing_status({"LANGSMITH_TRACING": "true", API_KEY_VARIABLE: "placeholder"})
    off = tracing_status({})

    traced = trace_config("graph", "clean_bad_deploy", 2, "dev", on)
    untraced = trace_config("graph", "clean_bad_deploy", 2, "dev", off)

    assert traced.get("run_name") == "graph/clean_bad_deploy/2"
    assert set(traced.get("tags", [])) == {"system:graph", "split:dev"}
    assert traced.get("metadata") == {"scenario_id": "clean_bad_deploy", "repetition": 2}
    assert project_environment(on) == {"LANGSMITH_PROJECT": "agentops"}
    assert not {"run_name", "tags", "metadata"} & set(untraced)
    assert project_environment(off) == {}


# E35 (review round 1, wh-bug M3): the dev split trusts the manifest, so it is checked too. A
# manifest or frozen file that no longer matches its pin refuses a dev run before it starts.
def edit_manifest_to_freeze(evals_dir: Path, scenario_id: str) -> None:
    manifest = evals_dir / "held_out" / "manifest.json"
    manifest.write_text(json.dumps({"held_out": [scenario_id]}), encoding="utf-8")


def test_a_dev_eval_refuses_a_manifest_that_no_longer_matches_its_checksums(
    tmp_path: Path,
) -> None:
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)
    edit_manifest_to_freeze(evals_dir, TEMPORARY_DEV_ID)

    run = evaluate(tmp_path, *split_options(scenarios_dir, evals_dir))

    assert run.result.exit_code == 1
    assert "manifest.json" in run.result.stderr
    assert run.result.stdout == ""
    assert run.rows == []


def test_a_dev_eval_refuses_a_frozen_scenario_that_was_renamed_or_changed(tmp_path: Path) -> None:
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)
    frozen_file = scenarios_dir / f"{TEMPORARY_HELD_OUT_ID}.json"
    frozen_file.write_bytes(frozen_file.read_bytes().replace(b" ", b"\t", 1))
    changed = evaluate(tmp_path, *split_options(scenarios_dir, evals_dir))
    frozen_file.rename(scenarios_dir / "renamed_frozen.json")

    renamed = evaluate(tmp_path, *split_options(scenarios_dir, evals_dir))

    assert changed.result.exit_code == 1
    assert renamed.result.exit_code == 1
    assert changed.result.stdout == renamed.result.stdout == ""


def test_a_dev_eval_over_a_matching_split_still_runs_and_scores_only_dev(tmp_path: Path) -> None:
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)

    run = evaluate(tmp_path, "--system", "graph", *split_options(scenarios_dir, evals_dir))

    assert run.result.exit_code == 0, run.result.output
    assert {row["scenario_id"] for row in run.rows} == {TEMPORARY_DEV_ID}


def test_a_manifest_that_is_not_utf8_exits_two_for_eval_and_dataset(tmp_path: Path) -> None:
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)
    (evals_dir / "held_out" / "manifest.json").write_bytes(b'{"held_out": ["caf\xe9"]}')
    options = split_options(scenarios_dir, evals_dir)

    results = [
        runner.invoke(app, ["eval", "--model", "scripted", *options]),
        runner.invoke(app, ["eval", "--model", "scripted", "--held-out", *options]),
        runner.invoke(app, ["dataset", *options]),
    ]

    assert [result.exit_code for result in results] == [2, 2, 2]
    assert all("UnicodeDecodeError" in result.stderr for result in results)
    assert all("Traceback" not in result.output for result in results)
