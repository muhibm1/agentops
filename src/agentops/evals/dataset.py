"""The dev and frozen (held-out) evaluation split, pinned by a manifest and sha256 checksums.

`evals/held_out/manifest.json` names the frozen scenario ids. `evals/checksums.sha256` pins the
bytes of the manifest and of each `scenarios/<id>.json` it names, in `sha256sum` format with
paths relative to the repository root (ADR 0002). The dev split is every other scenario file.
Loading the frozen split needs `allow_held_out=True`, because it is scored only when a version is
frozen.
"""

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import Field, StringConstraints, ValidationError, field_validator

from agentops.sources.base import SIGNAL_ID_PATTERN
from agentops.sources.scenario import Scenario, list_scenario_ids, load_scenario
from agentops.state import StrictModel

# The committed frozen split; `write_dataset` pins these unless told otherwise.
HELD_OUT_IDS = ("ambiguous_two_causes", "deploy_red_herring_db", "schema_drift_ingestion")
HELD_OUT_DIR = "held_out"
MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.sha256"
MANIFEST_LABEL = f"evals/{HELD_OUT_DIR}/{MANIFEST_NAME}"
CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")
WRITE_HINT = "write it with `agentops dataset --write`"

Split = Literal["dev", "held_out"]
SPLITS: tuple[str, ...] = get_args(Split)
ScenarioId = Annotated[str, StringConstraints(pattern=SIGNAL_ID_PATTERN)]


class HeldOutRefusedError(Exception):
    """The frozen split was requested without `allow_held_out=True`."""


class DatasetExistsError(Exception):
    """The manifest or the checksums already exist and `force` was not given."""


class HeldOutManifest(StrictModel):
    """The ids are file stems under the scenarios directory, so only safe stems are accepted."""

    held_out: list[ScenarioId] = Field(min_length=1)

    @field_validator("held_out")
    @classmethod
    def ids_are_unique(cls, ids: list[str]) -> list[str]:
        if len(set(ids)) != len(ids):
            raise ValueError("held_out names the same scenario id more than once")
        return ids


def split_scenarios(
    split: Split, scenarios_dir: Path, evals_dir: Path, *, allow_held_out: bool = False
) -> list[Scenario]:
    """Load the scenarios of one split, sorted by id, using the manifest in `evals_dir`.

    Raises `HeldOutRefusedError` for `held_out` without `allow_held_out`, before reading any file;
    `ValueError` for an unknown split; `FileNotFoundError`, `UnicodeDecodeError` or
    `pydantic.ValidationError` for a missing, unreadable or malformed manifest or scenario file.
    """
    if split not in SPLITS:
        raise ValueError(f"Unknown split {split!r}; expected one of: {', '.join(SPLITS)}.")
    if split == "held_out" and not allow_held_out:
        raise HeldOutRefusedError(
            "The held-out split is frozen: score it only when a version is frozen, "
            "with `agentops eval --held-out`."
        )
    held_out = held_out_ids_of(evals_dir)
    if split == "held_out":
        ids = sorted(held_out)
    else:
        ids = [stem for stem in list_scenario_ids(scenarios_dir) if stem not in held_out]
    return [load_scenario(scenarios_dir / f"{scenario_id}.json") for scenario_id in ids]


def write_dataset(
    scenarios_dir: Path,
    evals_dir: Path,
    *,
    force: bool,
    held_out_ids: Sequence[str] = HELD_OUT_IDS,
) -> list[Path]:
    """Write the manifest, then the checksums of the manifest and each held-out scenario file.

    Raises `pydantic.ValidationError` for an empty, unsafe or repeated id list,
    `DatasetExistsError` when either file exists and `force` is false, and `FileNotFoundError`
    when a held-out id has no scenario file. Nothing is written when any of these is raised.
    Returns the two paths written.
    """
    manifest = HeldOutManifest(held_out=list(held_out_ids))
    manifest_file = manifest_path(evals_dir)
    checksums_file = evals_dir / CHECKSUMS_NAME
    existing = [str(path) for path in (manifest_file, checksums_file) if path.exists()]
    if existing and not force:
        raise DatasetExistsError(
            f"{' and '.join(existing)} already exist; pass --force to overwrite the pinned split."
        )
    missing = [
        stem for stem in manifest.held_out if not scenario_path(scenarios_dir, stem).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"No scenario file in {scenarios_dir} for held-out id(s): {', '.join(missing)}."
        )

    manifest_text = manifest.model_dump_json(indent=2) + "\n"
    lines = checksum_lines(scenarios_dir, manifest_text, manifest.held_out)

    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(manifest_text, encoding="utf-8", newline="\n")
    checksums_file.write_text("".join(lines), encoding="utf-8", newline="\n")
    return [manifest_file, checksums_file]


def checksum_lines(
    scenarios_dir: Path, manifest_text: str, held_out_ids: Sequence[str]
) -> list[str]:
    """The `sha256sum` lines for the manifest text about to be written and each held-out file.

    Every file is read here, before anything is written, so an unreadable scenario file fails
    the command while the pinned split is still whole."""
    manifest_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    lines = [f"{manifest_digest}  {MANIFEST_LABEL}\n"]
    for stem in held_out_ids:
        digest = sha256_of(scenario_path(scenarios_dir, stem))
        lines.append(f"{digest}  scenarios/{stem}.json\n")
    return lines


def check_dataset(scenarios_dir: Path, evals_dir: Path) -> list[str]:
    """Recompute every pinned checksum; return one message per mismatch, the empty list if none.

    Only the files the manifest names are opened; a path written in the checksums file is
    compared as text and never opened.
    """
    if not manifest_path(evals_dir).is_file():
        return [f"{MANIFEST_LABEL} is missing from {evals_dir}; {WRITE_HINT}."]
    try:
        held_out = held_out_ids_of(evals_dir)
    except ValidationError as error:
        return [f"{MANIFEST_LABEL} is not a valid manifest: {error}"]
    checksums_file = evals_dir / CHECKSUMS_NAME
    if not checksums_file.is_file():
        return [f"{CHECKSUMS_NAME} is missing from {evals_dir}; {WRITE_HINT}."]

    recorded, problems = recorded_checksums(checksums_file.read_text(encoding="utf-8"))
    for label, path in pinned_files(scenarios_dir, evals_dir, held_out):
        digest = recorded.pop(label, None)
        if not path.is_file():
            problems.append(f"{MANIFEST_LABEL} names a held-out scenario with no file: {label}.")
        elif digest is None:
            problems.append(f"{CHECKSUMS_NAME} has no line for {label}.")
        elif digest != sha256_of(path):
            problems.append(f"{label} does not match its pinned sha256 in {CHECKSUMS_NAME}.")
    problems.extend(
        f"{CHECKSUMS_NAME} pins {label}, which the manifest does not name." for label in recorded
    )
    return problems


def held_out_ids_of(evals_dir: Path) -> list[str]:
    """The ids in `evals_dir/held_out/manifest.json`, in file order.

    Raises `FileNotFoundError` or `pydantic.ValidationError`.
    """
    text = manifest_path(evals_dir).read_text(encoding="utf-8")
    return HeldOutManifest.model_validate_json(text).held_out


def sha256_of(path: Path) -> str:
    """The lowercase hex sha256 of the file's bytes, as `sha256sum` prints it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_path(evals_dir: Path) -> Path:
    return evals_dir / HELD_OUT_DIR / MANIFEST_NAME


def scenario_path(scenarios_dir: Path, scenario_id: str) -> Path:
    return scenarios_dir / f"{scenario_id}.json"


def pinned_files(
    scenarios_dir: Path, evals_dir: Path, held_out_ids: Sequence[str]
) -> list[tuple[str, Path]]:
    """(label in the checksums file, path on disk) for the manifest and each held-out scenario."""
    scenarios = [
        (f"scenarios/{stem}.json", scenario_path(scenarios_dir, stem)) for stem in held_out_ids
    ]
    return [(MANIFEST_LABEL, manifest_path(evals_dir)), *scenarios]


def recorded_checksums(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse `sha256sum` lines into {label: digest}; one message per malformed or repeated line."""
    recorded: dict[str, str] = {}
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = CHECKSUM_LINE.match(line)
        if match is None:
            problems.append(f"{CHECKSUMS_NAME} line {number} is not `<sha256>  <path>`.")
            continue
        digest, label = match.groups()
        if label in recorded:
            problems.append(f"{CHECKSUMS_NAME} line {number} pins {label} a second time.")
            continue
        recorded[label] = digest
    return recorded, problems
