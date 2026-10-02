"""E32 to E34, F7 and A5: the dev and frozen (held-out) split, pinned by a manifest and sha256.

Every test that writes or changes a file works on temporary copies. The committed files are only
read and hashed; no frozen scenario is run or scored here (D18).
"""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentops.evals.dataset import (
    CHECKSUMS_NAME,
    HELD_OUT_IDS,
    MANIFEST_LABEL,
    DatasetExistsError,
    HeldOutRefusedError,
    check_dataset,
    held_out_ids_of,
    manifest_path,
    sha256_of,
    split_scenarios,
    write_dataset,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO_ROOT / "scenarios"
EVALS_DIR = REPO_ROOT / "evals"
COMMITTED_HELD_OUT = ["ambiguous_two_causes", "deploy_red_herring_db", "schema_drift_ingestion"]


@pytest.fixture
def scenarios_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "scenarios"
    shutil.copytree(SCENARIOS_DIR, copy)
    return copy


@pytest.fixture
def written_evals(scenarios_copy: Path, tmp_path: Path) -> Path:
    evals = tmp_path / "evals"
    write_dataset(scenarios_copy, evals, force=False)
    return evals


def test_write_produces_the_manifest_and_four_checksums(scenarios_copy: Path, tmp_path: Path):
    evals = tmp_path / "evals"

    write_dataset(scenarios_copy, evals, force=False)

    manifest = json.loads(manifest_path(evals).read_text(encoding="utf-8"))
    lines = (evals / CHECKSUMS_NAME).read_text(encoding="utf-8").splitlines()
    assert manifest == {"held_out": COMMITTED_HELD_OUT}
    assert len(lines) == 4
    assert lines[0] == f"{sha256_of(manifest_path(evals))}  {MANIFEST_LABEL}"
    assert lines[1] == (
        f"{sha256_of(scenarios_copy / 'ambiguous_two_causes.json')}  "
        "scenarios/ambiguous_two_causes.json"
    )
    assert check_dataset(scenarios_copy, evals) == []


def test_the_committed_checksums_match_and_the_split_is_five_and_three():
    lines = (EVALS_DIR / CHECKSUMS_NAME).read_text(encoding="utf-8").splitlines()

    mismatches = check_dataset(SCENARIOS_DIR, EVALS_DIR)
    dev_ids = [scenario.id for scenario in split_scenarios("dev", SCENARIOS_DIR, EVALS_DIR)]

    assert mismatches == []
    assert len(lines) == 4
    assert held_out_ids_of(EVALS_DIR) == COMMITTED_HELD_OUT
    assert list(HELD_OUT_IDS) == COMMITTED_HELD_OUT
    assert dev_ids == [
        "clean_bad_deploy",
        "dependency_timeout_noisy_neighbour",
        "late_partition_zero_rows",
        "log_injection_drop_table",
        "two_concurrent_faults",
    ]


def test_a_changed_frozen_scenario_fails_the_check_by_name(
    scenarios_copy: Path, written_evals: Path
):
    held_out_file = scenarios_copy / "deploy_red_herring_db.json"
    held_out_file.write_bytes(held_out_file.read_bytes() + b" ")

    mismatches = check_dataset(scenarios_copy, written_evals)

    assert len(mismatches) == 1
    assert "scenarios/deploy_red_herring_db.json" in mismatches[0]


def test_a_changed_dev_scenario_does_not_fail_the_check(scenarios_copy: Path, written_evals: Path):
    dev_file = scenarios_copy / "clean_bad_deploy.json"
    dev_file.write_bytes(dev_file.read_bytes() + b" ")

    assert check_dataset(scenarios_copy, written_evals) == []


def test_a_changed_manifest_fails_the_check_by_name(scenarios_copy: Path, written_evals: Path):
    manifest_path(written_evals).write_text(
        json.dumps({"held_out": ["ambiguous_two_causes"]}), encoding="utf-8"
    )

    mismatches = check_dataset(scenarios_copy, written_evals)

    assert MANIFEST_LABEL in mismatches[0]


def test_a_manifest_id_without_a_scenario_file_fails_the_check(
    scenarios_copy: Path, written_evals: Path
):
    (scenarios_copy / "schema_drift_ingestion.json").unlink()

    mismatches = check_dataset(scenarios_copy, written_evals)

    assert len(mismatches) == 1
    assert "schema_drift_ingestion" in mismatches[0]


def test_scoring_the_frozen_split_needs_the_flag_and_rewriting_needs_force(
    scenarios_copy: Path, written_evals: Path
):
    with pytest.raises(HeldOutRefusedError):
        split_scenarios("held_out", SCENARIOS_DIR, EVALS_DIR)
    with pytest.raises(DatasetExistsError, match="--force"):
        write_dataset(scenarios_copy, written_evals, force=False)


def test_force_rewrites_an_existing_dataset_with_a_new_split(
    scenarios_copy: Path, written_evals: Path
):
    write_dataset(scenarios_copy, written_evals, force=True, held_out_ids=["clean_bad_deploy"])

    held_out = split_scenarios("held_out", scenarios_copy, written_evals, allow_held_out=True)

    assert [scenario.id for scenario in held_out] == ["clean_bad_deploy"]
    assert len(split_scenarios("dev", scenarios_copy, written_evals)) == 7
    assert check_dataset(scenarios_copy, written_evals) == []


def test_write_refuses_a_held_out_id_with_no_scenario_file(scenarios_copy: Path, tmp_path: Path):
    evals = tmp_path / "evals"

    with pytest.raises(FileNotFoundError, match="no_such_scenario"):
        write_dataset(scenarios_copy, evals, force=False, held_out_ids=["no_such_scenario"])

    assert not manifest_path(evals).exists()


@pytest.mark.parametrize("bad_ids", [[], ["../clean_bad_deploy"], ["Upper"], ["a", "a"]])
def test_write_refuses_an_empty_unsafe_or_repeated_held_out_list(
    scenarios_copy: Path, tmp_path: Path, bad_ids: list[str]
):
    with pytest.raises(ValidationError):
        write_dataset(scenarios_copy, tmp_path / "evals", force=False, held_out_ids=bad_ids)


def test_a_manifest_with_an_unsafe_id_fails_the_check_and_the_split(
    scenarios_copy: Path, written_evals: Path
):
    manifest_path(written_evals).write_text(
        json.dumps({"held_out": ["../scenarios/clean_bad_deploy"]}), encoding="utf-8"
    )

    mismatches = check_dataset(scenarios_copy, written_evals)

    assert len(mismatches) == 1
    assert MANIFEST_LABEL in mismatches[0]
    with pytest.raises(ValidationError):
        split_scenarios("dev", scenarios_copy, written_evals)


def test_missing_dataset_files_fail_the_check_with_a_hint(scenarios_copy: Path, tmp_path: Path):
    evals = tmp_path / "evals"

    no_manifest = check_dataset(scenarios_copy, evals)
    write_dataset(scenarios_copy, evals, force=False)
    (evals / CHECKSUMS_NAME).unlink()
    no_checksums = check_dataset(scenarios_copy, evals)

    assert len(no_manifest) == 1
    assert MANIFEST_LABEL in no_manifest[0]
    assert "--write" in no_manifest[0]
    assert len(no_checksums) == 1
    assert CHECKSUMS_NAME in no_checksums[0]


def test_a_missing_extra_or_malformed_checksum_line_fails_the_check(
    scenarios_copy: Path, written_evals: Path
):
    checksums = written_evals / CHECKSUMS_NAME
    lines = checksums.read_text(encoding="utf-8").splitlines()
    extra = f"{'0' * 64}  scenarios/clean_bad_deploy.json"
    checksums.write_text("\n".join([*lines[:-1], extra, "not a checksum line"]) + "\n")

    mismatches = check_dataset(scenarios_copy, written_evals)

    assert len(mismatches) == 3
    assert "line 5" in mismatches[0]
    assert "scenarios/schema_drift_ingestion.json" in mismatches[1]
    assert "scenarios/clean_bad_deploy.json" in mismatches[2]


def test_an_unknown_split_name_is_refused():
    with pytest.raises(ValueError, match="frozen"):
        split_scenarios("frozen", SCENARIOS_DIR, EVALS_DIR)  # type: ignore[arg-type]


def test_an_unreadable_scenario_file_fails_the_write_before_either_file_is_touched(
    scenarios_copy: Path, written_evals: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest_before = manifest_path(written_evals).read_bytes()
    checksums_before = (written_evals / CHECKSUMS_NAME).read_bytes()

    def refuse_to_read(path: Path) -> str:
        raise PermissionError(f"cannot read {path.name}")

    monkeypatch.setattr("agentops.evals.dataset.sha256_of", refuse_to_read)

    with pytest.raises(PermissionError):
        write_dataset(scenarios_copy, written_evals, force=True, held_out_ids=["clean_bad_deploy"])

    assert manifest_path(written_evals).read_bytes() == manifest_before
    assert (written_evals / CHECKSUMS_NAME).read_bytes() == checksums_before


def test_a_manifest_that_is_not_utf8_raises_instead_of_being_reported_as_a_mismatch(
    scenarios_copy: Path, written_evals: Path
):
    manifest_path(written_evals).write_bytes(b'{"held_out": ["caf\xe9"]}')

    with pytest.raises(UnicodeDecodeError):
        check_dataset(scenarios_copy, written_evals)


def test_a_manifest_that_is_valid_utf8_but_malformed_is_still_a_mismatch_message(
    scenarios_copy: Path, written_evals: Path
):
    manifest_path(written_evals).write_text("{not json", encoding="utf-8")

    problems = check_dataset(scenarios_copy, written_evals)

    assert len(problems) == 1
    assert "not a valid manifest" in problems[0]
