"""E14 to E18, E22, E25, E31, E37 and E38 of the live-model change: the cost columns, the spend
guard, `--dev` and `--yes`, the judge column, the missing-key contract and an SDK error inside a
node (R4, R8, R9, R11).

No test reaches a live model. Two spies stand in for it: `constructor` replaces the ChatAnthropic
class and fails the test if anything builds a client, and `live_factory` replaces the factory the
command line builds with one that answers from the scenario scripts while claiming to be the live
model, so the commands take their live path offline.
"""

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from test_eval_cli import (
    REPOSITORY_DATA,
    TEMPORARY_HELD_OUT_ID,
    split_options,
    table_rows,
    temporary_frozen_split,
)
from typer.testing import CliRunner, Result

from agentops import cli, models
from agentops.evals.judge import JUDGE_KEY, JudgeVerdict
from agentops.evals.outcome import TokenUsage
from agentops.evals.run import EvalRow, render_table, run_split
from agentops.fakes import ScriptedChatModel
from agentops.models import (
    DEFAULT_MODEL_ID,
    DEV_MODEL_ID,
    GRAPH_ROLES,
    ModelName,
    ModelRole,
    ModelSettings,
    ScriptedModels,
)
from agentops.pricing import PRICES_AS_OF, estimated_cost
from agentops.sources.scenario import Scenario, load_scenario
from agentops.tracing import tracing_status

REPOSITORY = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPOSITORY / "scenarios"
DEV_SCENARIO_COUNT = 5
FAKE_KEY = "fake-key-123"
UNPRICED_ID = "claude-x"

runner = CliRunner()


@pytest.fixture(autouse=True)
def in_a_temporary_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for role in models.ROLES:
        monkeypatch.delenv(f"{models.ROLE_OVERRIDE_PREFIX}{role.upper()}", raising=False)


class ClientBuilt(AssertionError):
    """A test reached the point where a live client would be built."""


@dataclass
class Constructor:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> BaseChatModel:
        self.calls.append(kwargs)
        raise ClientBuilt(f"a live client was built for {kwargs.get('model')}")


@pytest.fixture
def constructor(monkeypatch: pytest.MonkeyPatch) -> Constructor:
    recording = Constructor()
    monkeypatch.setattr(models, "chat_model_class", lambda: recording)
    return recording


def judge_model() -> ScriptedChatModel:
    return ScriptedChatModel(
        script_by_schema={JudgeVerdict: [JudgeVerdict(score=1, comment="cites the deploy")]}
    )


@dataclass
class LiveLookingModels(ScriptedModels):
    """The scripted models under the live label, with a judge, recording which roles were asked
    for. `graph_model` replaces the graph's models when set."""

    dev: bool = False
    graph_model: Callable[[Scenario], BaseChatModel] | None = None
    asked: list[str] = field(default_factory=list)
    label: ModelName = "anthropic"  # type: ignore[assignment]
    runs_judge = True  # type: ignore[assignment]

    def graph_models(self, scenario: Scenario) -> dict[ModelRole, BaseChatModel]:
        self.asked.extend(GRAPH_ROLES)
        if self.graph_model is not None:
            return dict.fromkeys(GRAPH_ROLES, self.graph_model(scenario))  # type: ignore[return-value]
        return super().graph_models(scenario)

    def baseline_model(self, scenario: Scenario) -> BaseChatModel:
        self.asked.append("baseline")
        return super().baseline_model(scenario)

    def judge_model(self) -> BaseChatModel:
        self.asked.append("judge")
        return judge_model()

    def model_ids(self) -> dict[ModelRole, str]:
        return ModelSettings.from_environment(self.dev, os.environ).model_ids


@pytest.fixture
def live_factory(monkeypatch: pytest.MonkeyPatch) -> list[LiveLookingModels]:
    """Every factory the command line built, each a `LiveLookingModels`."""
    built: list[LiveLookingModels] = []

    def factory(model: ModelName, dev: bool, environ: Any = None) -> Any:
        if model == "scripted":
            return ScriptedModels()
        models.require_api_key(os.environ if environ is None else environ)
        built.append(LiveLookingModels(dev=dev))
        return built[-1]

    monkeypatch.setattr(cli, "model_factory", factory)
    return built


def live_eval(*arguments: str) -> Result:
    return runner.invoke(cli.app, ["eval", "--model", "anthropic", *arguments])


# E14: the scripted table has the token and cost columns, and its cost is unpriced.
def test_the_scripted_table_has_token_and_cost_columns_and_prices_nothing(tmp_path: Path) -> None:
    json_path = tmp_path / "out.json"
    result = runner.invoke(
        cli.app, ["eval", "--model", "scripted", *REPOSITORY_DATA, "--json", str(json_path)]
    )

    assert result.exit_code == 0, result.output
    heading = next(line for line in result.stdout.splitlines() if line.startswith("system"))
    for column in ("input", "output", "cache_read", "cache_write", "cost_usd"):
        assert column in heading
    for row in table_rows(result.stdout):
        assert "n/a (unpriced: scripted)" in row
    rows = json.loads(json_path.read_text(encoding="utf-8"))
    assert rows
    for row in rows:
        assert set(row["outcome"]["usage_by_model"]) == {"scripted"}
        assert row["cost_usd"] is None
    assert "planned:" not in result.stdout
    assert JUDGE_KEY not in heading
    assert "judge cost" not in result.stdout


def priced_row(scenario_id: str, input_tokens: int, output_tokens: int) -> EvalRow:
    scenario = load_scenario(SCENARIOS_DIR / "clean_bad_deploy.json")
    rows = run_split(
        [scenario],
        systems=["graph"],
        repetitions=1,
        split="dev",
        tracing=tracing_status(),
    )
    usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    outcome = rows[0].outcome.model_copy(update={"usage_by_model": {DEFAULT_MODEL_ID: usage}})
    return EvalRow(
        system="graph",
        scenario_id=scenario_id,
        repetition=1,
        outcome=outcome,
        evaluators=rows[0].evaluators,
    )


# E15: priced rows print dollars with two decimals and integer token spreads.
def test_priced_rows_print_a_dollar_spread_and_integer_token_spreads() -> None:
    # 1M input tokens and 100k output on the default model: $2.00 + $1.00 = $3.00.
    dearer = priced_row("clean_bad_deploy", 1_000_000, 100_000)
    # Half of each: $1.50.
    cheaper = priced_row("clean_bad_deploy", 500_000, 50_000)

    table = render_table([dearer, cheaper])

    graph = next(line for line in table if line.startswith("graph "))
    assert "$2.25/$1.50/$3.00" in graph
    assert "750000/500000/1000000" in graph
    assert "75000/50000/100000" in graph


# E16: the live eval without --yes prints the plan and the estimate, builds nothing and stops.
def test_the_spend_guard_stops_a_live_eval_without_yes_before_any_client(
    constructor: Constructor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

    result = live_eval(*REPOSITORY_DATA, "--repetitions", "2")

    assert result.exit_code == 1
    assert "planned: 20 runs (2 × 5 × 2) plus 10 judge calls" in result.stdout
    assert "estimated cost: $" in result.stdout
    assert PRICES_AS_OF in result.stdout
    assert "--yes" in result.stdout
    assert constructor.calls == []
    assert table_rows(result.stdout) == []


# E17: with --yes the run goes ahead, every role is asked for and the header names the model.
def test_yes_lets_the_live_eval_run_and_asks_every_role(
    live_factory: list[LiveLookingModels], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

    result = live_eval(*REPOSITORY_DATA, "--yes")

    assert result.exit_code == 0, result.output
    assert len(live_factory) == 1
    asked = live_factory[0].asked
    assert set(asked) == {*GRAPH_ROLES, "baseline", "judge"}
    assert asked.count("judge") == DEV_SCENARIO_COUNT
    header = next(line for line in result.stdout.splitlines() if line.startswith("model:"))
    assert "model: anthropic" in header
    assert [row.split()[0] for row in table_rows(result.stdout)] == ["graph", "baseline"]
    assert f"judge cost: n/a (unpriced: scripted) ({DEV_SCENARIO_COUNT} calls)" in result.stdout


# E18: --yes changes nothing on the scripted model, and the checksums are checked before the guard.
def test_yes_is_inert_on_the_scripted_model() -> None:
    result = runner.invoke(cli.app, ["eval", "--model", "scripted", *REPOSITORY_DATA, "--yes"])

    assert result.exit_code == 0, result.output
    assert "planned:" not in result.stdout
    assert "estimated cost" not in result.stdout


def test_a_stale_checksum_stops_a_live_held_out_eval_before_the_guard(
    tmp_path: Path, constructor: Constructor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    scenarios_dir, evals_dir = temporary_frozen_split(tmp_path)
    frozen_file = scenarios_dir / f"{TEMPORARY_HELD_OUT_ID}.json"
    frozen_file.write_bytes(frozen_file.read_bytes().replace(b" ", b"\t", 1))

    result = live_eval("--held-out", "--yes", *split_options(scenarios_dir, evals_dir))

    assert result.exit_code == 1
    assert "checksums" in result.stderr
    assert result.stdout == ""
    assert constructor.calls == []


# E22: a factory that runs the judge adds its column and its cost line; the scripted one adds
# neither.
def test_the_judge_column_and_cost_line_appear_only_when_the_judge_runs() -> None:
    scenarios = [load_scenario(SCENARIOS_DIR / "clean_bad_deploy.json")]
    common: dict[str, Any] = {
        "systems": ["graph"],
        "repetitions": 2,
        "split": "dev",
        "tracing": tracing_status(),
    }

    judged = render_table(run_split(scenarios, models=LiveLookingModels(), **common))
    plain = render_table(run_split(scenarios, models=ScriptedModels(), **common))

    judged_heading = next(line for line in judged if line.startswith("system"))
    judged_graph = next(line for line in judged if line.startswith("graph "))
    assert JUDGE_KEY in judged_heading
    assert judged_graph.split()[judged_heading.split().index(JUDGE_KEY)] == "100%"
    assert "judge cost: n/a (unpriced: scripted) (2 calls)" in judged
    assert JUDGE_KEY not in next(line for line in plain if line.startswith("system"))
    assert not any(line.startswith("judge cost") for line in plain)


# E25: without the key every live command exits 1 naming the variable, prints nothing on
# stdout, writes nothing and builds no model.
@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "--scenario", "clean_bad_deploy", "--thread", "incident-1"],
        ["approve", "incident-1", "--decision", "approve"],
        ["resume", "incident-1"],
        ["fork", "incident-1", "--at", "checkpoint", "--set", "bad_deploy=0.9"],
        ["eval", *REPOSITORY_DATA, "--yes"],
    ],
)
def test_a_live_command_without_the_key_exits_one_naming_the_variable(
    arguments: list[str], tmp_path: Path, constructor: Constructor
) -> None:
    result = runner.invoke(
        cli.app,
        [
            *arguments,
            "--model",
            "anthropic",
            *(["--scenarios-dir", str(SCENARIOS_DIR)] if arguments[0] != "eval" else []),
        ],
    )

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.stderr
    assert result.stdout == ""
    assert sorted(path.name for path in tmp_path.iterdir()) == []
    assert constructor.calls == []


class APIConnectionError(Exception):
    """Named like the SDK's own, which is not a ValidationError, so no node retries it."""


class FailingModel(ScriptedChatModel):
    """Raises an SDK-style error that quotes the key, as a careless SDK message might."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Sequence[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        raise APIConnectionError(f"connection refused for key {FAKE_KEY}")


# E31: an SDK error inside a node is not retried, fails the run with exit 2 naming its type, and
# never prints the key.
def test_an_sdk_error_inside_a_node_fails_the_run_without_retry_or_the_key(
    live_factory: list[LiveLookingModels], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    failing = FailingModel()
    monkeypatch.setattr(
        cli,
        "model_factory",
        lambda model, dev, environ=None: LiveLookingModels(graph_model=lambda _: failing),
    )

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--scenario",
            "clean_bad_deploy",
            "--model",
            "anthropic",
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
    )

    assert result.exit_code == 2
    assert len(failing.calls) == 1
    assert "APIConnectionError" in result.stderr
    assert FAKE_KEY not in result.stdout
    assert FAKE_KEY not in result.stderr


def test_an_sdk_error_inside_a_node_is_a_failed_eval_row_without_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(
        cli,
        "model_factory",
        lambda model, dev, environ=None: LiveLookingModels(graph_model=lambda _: FailingModel()),
    )
    json_path = tmp_path / "out.json"

    result = live_eval(*REPOSITORY_DATA, "--yes", "--system", "graph", "--json", str(json_path))

    written = json_path.read_text(encoding="utf-8")
    rows = json.loads(written)
    assert rows
    assert {row["outcome"]["outcome"] for row in rows} == {"failed"}
    assert {row["outcome"]["failure"] for row in rows} == {"APIConnectionError"}
    assert FAKE_KEY not in written
    assert FAKE_KEY not in result.stdout
    assert FAKE_KEY not in result.stderr
    assert "APIConnectionError" in result.stderr


def estimate_line(output: str) -> str:
    return next(line for line in output.splitlines() if line.startswith("estimated cost:"))


def dollars(line: str) -> float:
    return float(line.removeprefix("estimated cost: $").split()[0])


# E37: the estimate is `estimated_cost` to the cent, priced role by role.
def test_the_estimate_is_priced_role_by_role(
    constructor: Constructor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    runs = {"graph": 10, "baseline": 10}

    haiku = live_eval(*REPOSITORY_DATA, "--repetitions", "2", "--dev")
    monkeypatch.setenv("AGENTOPS_MODEL_RCA", DEFAULT_MODEL_ID)
    mixed = live_eval(*REPOSITORY_DATA, "--repetitions", "2", "--dev")

    haiku_ids = ModelSettings.from_environment(dev=True, environ={}).model_ids
    mixed_ids: dict[ModelRole, str] = {**haiku_ids, "rca": DEFAULT_MODEL_ID}
    expected_haiku = estimated_cost(haiku_ids, runs, 10)
    expected_mixed = estimated_cost(mixed_ids, runs, 10)
    assert isinstance(expected_haiku, float)
    assert isinstance(expected_mixed, float)
    assert haiku.exit_code == 1
    assert f"on {DEV_MODEL_ID}" in haiku.stdout
    assert dollars(estimate_line(haiku.stdout)) == round(expected_haiku, 2)
    assert dollars(estimate_line(mixed.stdout)) == round(expected_mixed, 2)
    assert DEFAULT_MODEL_ID in mixed.stdout
    assert expected_mixed > expected_haiku
    assert constructor.calls == []


# E38: an unpriced id makes the estimate unknown, never a figure, and --yes is still needed.
def test_an_unpriced_id_makes_the_estimate_unknown(
    constructor: Constructor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("AGENTOPS_MODEL_RCA", UNPRICED_ID)

    result = live_eval(*REPOSITORY_DATA)

    assert result.exit_code == 1
    assert "planned:" in result.stdout
    assert estimate_line(result.stdout) == f"estimated cost: unknown (unpriced: {UNPRICED_ID})"
    assert "$" not in result.stdout
    assert "KeyError" not in result.output
    assert constructor.calls == []


def test_an_unpriced_id_still_runs_with_yes_and_prints_no_figure(
    live_factory: list[LiveLookingModels], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("AGENTOPS_MODEL_RCA", UNPRICED_ID)

    result = live_eval(*REPOSITORY_DATA, "--yes")

    assert result.exit_code == 0, result.output
    graph = next(row for row in table_rows(result.stdout) if row.startswith("graph "))
    assert "n/a (unpriced: " in graph
