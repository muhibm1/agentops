"""The spend ladder on the live models (`agentops.budget`): the cap and its ledger, recorded
answers, and a rerun that replays them with no key, no cap and nothing sent.

No test reaches the API. `ScriptedAnthropic` is the real ChatAnthropic class with its network
call replaced: it answers each request from the scenario's scripted outputs, by the schema the
request asks for, and stamps the usage the API would. Everything up to that call is the real
client: its settings, LangChain's cache slot, the request key and the recordings.
"""

import json
import re
from pathlib import Path
from typing import Any, ClassVar

import flywheel.spend as spend
import pytest
from flywheel.langchain_spend import SpendCache
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from test_eval_cli import split_options, table_rows, temporary_frozen_split
from typer.testing import CliRunner, Result

from agentops import budget, cli, models
from agentops.budget import CAP_VARIABLE, LEDGER_VARIABLE, REPLAY_DIR_VARIABLE, REPLAY_VARIABLE
from agentops.evals.judge import JudgeVerdict
from agentops.evals.run import PASS_FAIL_EVALUATORS
from agentops.fakes import ACTIONS_APPLIED_MARKER, entry_as_json_text, prompt_lines
from agentops.models import DEV_MODEL_ID, REPLAY_KEY, ROLES, LiveModels, model_factory
from agentops.nodes import ask_model, fence_markers, fence_rule
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.sources.scenario import Scenario, load_scenario
from agentops.state import Triage

REPOSITORY = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPOSITORY / "scenarios"
SCENARIO_ID = "clean_bad_deploy"
FAKE_KEY = "fake-key-123"
USAGE = {
    "input_tokens": 1000,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 100,
}
# One call on the dev model (Haiku 4.5): 1,000 input tokens at $1 and 100 output at $5 a million.
DEV_CALL_PRICE = 1000 * 1.00 / 1e6 + 100 * 5.00 / 1e6
IDEMPOTENCY_KEY = re.compile(r"[0-9a-f]{64}")
NONCE = "0123456789abcdef"
OTHER_NONCE = "fedcba9876543210"

runner = CliRunner()


class ScriptedAnthropic(ChatAnthropic):
    """ChatAnthropic answering from `scenario`'s script instead of the API, one entry in `sent`
    for every request that would have been sent."""

    scenario: ClassVar[Scenario]
    sent: ClassVar[list[str]] = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        type(self).sent.append(self.model)
        output_config = kwargs.get("output_config")
        if output_config:
            title = output_config["format"]["schema"]["title"]
            reply = AIMessage(content=structured_answer(title, messages, self.scenario))
        else:
            reply = baseline_step(messages, self.scenario)
        reply = reply.model_copy(
            update={
                "usage_metadata": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}
            }
        )
        metadata = {"model_name": self.model, "usage": dict(USAGE)}
        return ChatResult(generations=[ChatGeneration(message=reply)], llm_output=metadata)


def structured_answer(title: str, messages: list[BaseMessage], scenario: Scenario) -> str:
    """The scripted answer to a request for the schema named `title`, as the scripted model
    gives it; the judge always scores 1."""
    outputs = scenario.scripted_outputs
    if title == JudgeVerdict.__name__:
        return JudgeVerdict(score=1, comment="cites the deploy").model_dump_json()
    applied = any(line.startswith(ACTIONS_APPLIED_MARKER) for line in prompt_lines(messages))
    if title == "RcaOutput" and applied and outputs.rca_after_action is not None:
        return entry_as_json_text(outputs.rca_after_action)
    entries = {
        "Triage": outputs.triage,
        "RcaOutput": outputs.rca,
        "Postmortem": outputs.postmortem,
        "Escalation": outputs.escalate,
    }
    return entry_as_json_text(entries[title])  # type: ignore[arg-type]


def baseline_step(messages: list[BaseMessage], scenario: Scenario) -> AIMessage:
    """The baseline's next scripted reply: the next tool call, then the answer."""
    script = scenario.scripted_outputs.baseline
    assert script is not None
    step = sum(isinstance(message, AIMessage) for message in messages)
    if step < len(script.steps):
        call = {"name": script.steps[step].tool, "args": script.steps[step].args}
        return AIMessage(content="", tool_calls=[{**call, "id": f"call-{step + 1}"}])
    return AIMessage(content=entry_as_json_text(script.answer))


@pytest.fixture
def scripted_anthropic(monkeypatch: pytest.MonkeyPatch) -> type[ScriptedAnthropic]:
    """Every client the factory builds is a `ScriptedAnthropic` on the clean bad deploy."""
    ScriptedAnthropic.scenario = load_scenario(SCENARIOS_DIR / f"{SCENARIO_ID}.json")
    ScriptedAnthropic.sent = []
    monkeypatch.setattr(models, "chat_model_class", lambda: ScriptedAnthropic)
    return ScriptedAnthropic


@pytest.fixture
def spend_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The ledger and the recordings under the test's own directory, which is also the working
    directory; returned as the environment that names them."""
    monkeypatch.chdir(tmp_path)
    environ = {
        LEDGER_VARIABLE: str(tmp_path / "spend" / "ledger.jsonl"),
        REPLAY_DIR_VARIABLE: str(tmp_path / "spend" / "replay"),
    }
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    return environ


def ledger_rows(spend_files: dict[str, str]) -> list[dict[str, Any]]:
    return spend.read_ledger([spend_files[LEDGER_VARIABLE]])


def fenced_prompt(nonce: str, idempotency_key: str) -> tuple[str, str]:
    """A human prompt with fenced evidence holding an idempotency key, and its fence sentence."""
    opening, closing = fence_markers(nonce)
    evidence = json.dumps({"id": "action-1", "payload": {"idempotency_key": idempotency_key}})
    return f"Service: checkout\n\n{opening}\n{evidence}\n{closing}", fence_rule(nonce)


def thread_command(*arguments: str) -> Result:
    return runner.invoke(cli.app, [*arguments, "--scenarios-dir", str(SCENARIOS_DIR)])


def live_models(environ: dict[str, str]) -> LiveModels:
    """The dev-model factory `--model anthropic --dev` builds under `environ`."""
    live = model_factory("anthropic", dev=True, environ=environ)
    assert isinstance(live, LiveModels)
    return live


# The settings.


def test_the_settings_default_to_no_cap_and_recordings_in_the_home_folder() -> None:
    guard = budget.guard_from({})
    recorder = budget.recorder_from({})

    assert guard.cap == 0
    assert guard.ledger == budget.agentops_home() / "api-ledger.jsonl"
    assert (guard.project, guard.cap_variable) == ("agentops", CAP_VARIABLE)
    assert (recorder.mode, recorder.root) == ("record", budget.agentops_home() / "replay")


def test_the_settings_are_read_from_the_environment() -> None:
    environ = {
        CAP_VARIABLE: " 2.5 ",
        LEDGER_VARIABLE: "ledger.jsonl",
        REPLAY_VARIABLE: " Replay ",
        REPLAY_DIR_VARIABLE: "recordings",
    }

    guard = budget.guard_from(environ)
    recorder = budget.recorder_from(environ)

    assert (guard.cap, guard.ledger) == (2.5, Path("ledger.jsonl"))
    assert (recorder.mode, recorder.root) == ("replay", Path("recordings"))


@pytest.mark.parametrize("cap", ["two", "-1", "nan", "inf"])
def test_a_cap_that_is_not_a_dollar_amount_is_refused_without_quoting_it(cap: str) -> None:
    with pytest.raises(budget.SpendSettingError) as raised:
        budget.guard_from({CAP_VARIABLE: cap})

    assert CAP_VARIABLE in str(raised.value)
    assert cap not in str(raised.value)


def test_the_replay_mode_is_record_replay_or_off() -> None:
    with pytest.raises(budget.SpendSettingError, match=REPLAY_VARIABLE):
        budget.recorder_from({REPLAY_VARIABLE: "on"})


def test_the_run_codes_are_blanked_wherever_a_prompt_holds_them() -> None:
    """The fence code in the markers and in the sentence naming them, and the idempotency keys,
    as LangChain writes the prompt it keys (JSON, so the evidence's quotes are escaped)."""
    first = json.dumps([*fenced_prompt(NONCE, "ab" * 32)])
    second = json.dumps([*fenced_prompt(OTHER_NONCE, "cd" * 32)])

    assert first != second
    assert budget.without_run_codes(first) == budget.without_run_codes(second)
    assert NONCE not in budget.without_run_codes(first)
    assert "ab" * 32 not in budget.without_run_codes(first)
    # The same characters anywhere else are part of the question, and stay.
    assert budget.without_run_codes(f"deploy {NONCE} at 10:00") == f"deploy {NONCE} at 10:00"


# The factory.


class RecordingConstructor:
    """Stands in for ChatAnthropic and records each set of keyword arguments."""

    def __init__(self) -> None:
        self.built: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.built.append(kwargs)
        return kwargs


def test_every_client_the_factory_builds_asks_the_ladder_and_never_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor = RecordingConstructor()
    monkeypatch.setattr(models, "chat_model_class", lambda: constructor)
    live = model_factory("anthropic", dev=True, environ={"ANTHROPIC_API_KEY": "k"})
    assert isinstance(live, LiveModels)

    live.graph_models(None)  # type: ignore[arg-type]
    live.baseline_model(None)  # type: ignore[arg-type]
    live.judge_model()

    assert len(constructor.built) == len(ROLES)
    for kwargs in constructor.built:
        cache = kwargs["cache"]
        assert isinstance(cache, SpendCache)
        assert (cache.guard, cache.recorder) == (live.guard, live.recorder)
        assert kwargs["disable_streaming"] is True
    assert {kwargs["cache"].label for kwargs in constructor.built} == {
        f"agentops {role}" for role in ROLES
    }


def test_a_replay_needs_no_key_and_never_hands_one_over() -> None:
    keyless = model_factory("anthropic", dev=False, environ={REPLAY_VARIABLE: "replay"})
    keyed = model_factory(
        "anthropic", dev=False, environ={REPLAY_VARIABLE: "replay", "ANTHROPIC_API_KEY": FAKE_KEY}
    )

    assert isinstance(keyless, LiveModels) and isinstance(keyed, LiveModels)
    assert keyless.api_key.get_secret_value() == REPLAY_KEY
    assert keyed.api_key.get_secret_value() == REPLAY_KEY


# One client, end to end.


def test_with_no_cap_a_call_is_refused_before_anything_is_sent(
    scripted_anthropic: type[ScriptedAnthropic], spend_files: dict[str, str]
) -> None:
    client = live_models({"ANTHROPIC_API_KEY": FAKE_KEY}).client("triage")

    with pytest.raises(spend.SpendRefused, match=CAP_VARIABLE):
        ask_model(client, Triage, TRIAGE_INSTRUCTIONS, "Service: checkout")
    with pytest.raises(spend.SpendRefused, match=CAP_VARIABLE):
        list(client.stream("Service: checkout"))

    assert scripted_anthropic.sent == []
    assert ledger_rows(spend_files) == []


def test_a_live_answer_is_billed_recorded_and_replayed_on_a_rerun_with_no_key(
    scripted_anthropic: type[ScriptedAnthropic], spend_files: dict[str, str]
) -> None:
    """The rerun's prompt has a new fence code and new idempotency keys, as every run's does."""
    environ = {"ANTHROPIC_API_KEY": FAKE_KEY, CAP_VARIABLE: "1", **spend_files}
    live = live_models(environ)
    prompt, sentence = fenced_prompt(NONCE, "ab" * 32)
    first, _ = ask_model(
        live.client("triage"), Triage, TRIAGE_INSTRUCTIONS, prompt, fence_sentence=sentence
    )

    rerun = live_models({REPLAY_VARIABLE: "replay", **spend_files})
    prompt, sentence = fenced_prompt(OTHER_NONCE, "cd" * 32)
    again, _ = ask_model(
        rerun.client("triage"), Triage, TRIAGE_INSTRUCTIONS, prompt, fence_sentence=sentence
    )

    assert again == first
    assert scripted_anthropic.sent == [DEV_MODEL_ID]
    paid, replayed = ledger_rows(spend_files)
    assert (paid["project"], paid["label"], paid["route"]) == ("agentops", "agentops triage", "api")
    assert paid["billed"] == pytest.approx(DEV_CALL_PRICE)
    assert (replayed["route"], replayed["billed"]) == ("replay", 0)
    assert replayed["usd"] == pytest.approx(DEV_CALL_PRICE)
    with pytest.raises(spend.ReplayMiss):
        ask_model(rerun.client("triage"), Triage, TRIAGE_INSTRUCTIONS, "Service: payments")
    assert scripted_anthropic.sent == [DEV_MODEL_ID]


# The thread commands.


@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "--scenario", SCENARIO_ID, "--thread", "incident-1"],
        ["approve", "incident-1", "--decision", "approve"],
        ["resume", "incident-1"],
        ["fork", "incident-1", "--at", "checkpoint", "--set", "bad_deploy=0.9"],
    ],
)
def test_a_live_command_with_no_cap_exits_one_before_anything_runs(
    arguments: list[str],
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    result = thread_command(*arguments, "--model", "anthropic")

    assert result.exit_code == 1
    assert CAP_VARIABLE in result.stderr
    assert result.stdout == ""
    assert [path.name for path in tmp_path.iterdir()] == []
    assert scripted_anthropic.sent == []


def test_a_spent_cap_and_a_malformed_cap_exit_one_naming_the_setting(
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    guard = budget.guard_from({CAP_VARIABLE: "0.01", **spend_files})
    guard.record(DEV_MODEL_ID, spend.Usage(output=2000))  # $0.01 on Haiku 4.5
    run = ["run", "--scenario", SCENARIO_ID, "--model", "anthropic"]

    monkeypatch.setenv(CAP_VARIABLE, "0.01")
    spent = thread_command(*run)
    monkeypatch.setenv(CAP_VARIABLE, "a lot")
    malformed = thread_command(*run)

    assert spent.exit_code == 1
    assert "The API cap is spent: $0.0100" in spent.stderr
    assert malformed.exit_code == 1
    assert CAP_VARIABLE in malformed.stderr and "a lot" not in malformed.stderr
    assert scripted_anthropic.sent == []


def test_a_live_run_and_approval_replay_for_free_on_another_thread(
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second thread has its own incident id, so new idempotency keys, and new fence
    codes: it replays every call all the same, with no key and no cap."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv(CAP_VARIABLE, "1")
    run = ["run", "--scenario", SCENARIO_ID, "--model", "anthropic", "--dev"]
    first_run = thread_command(*run, "--thread", "first")
    first_approval = thread_command(
        "approve", "first", "--decision", "approve", "--model", "anthropic", "--dev"
    )
    sent = list(scripted_anthropic.sent)

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv(CAP_VARIABLE)
    monkeypatch.setenv(REPLAY_VARIABLE, "replay")
    second_run = thread_command(*run, "--thread", "second")
    second_approval = thread_command(
        "approve", "second", "--decision", "approve", "--model", "anthropic", "--dev"
    )

    for result in (first_run, first_approval, second_run, second_approval):
        assert result.exit_code == 0, result.output
    assert len(sent) >= 3
    assert scripted_anthropic.sent == sent
    assert same_report(second_run.stdout, first_run.stdout)
    assert same_report(second_approval.stdout, first_approval.stdout)
    assert "resolved" in second_approval.stdout
    paid = sum(row["billed"] for row in ledger_rows(spend_files))
    assert paid == pytest.approx(len(sent) * DEV_CALL_PRICE)
    assert (
        "API credit: $" in first_run.stderr and "answered from recordings" not in first_run.stderr
    )
    assert "API credit: $0.0000 billed for 0 live calls; " in second_run.stderr
    assert "answered from recordings" in second_approval.stderr


def same_report(second: str, first: str) -> bool:
    """The two threads' reports agree but for the thread id and the idempotency keys it
    shapes."""
    return IDEMPOTENCY_KEY.sub("-", second.replace("second", "first")) == IDEMPOTENCY_KEY.sub(
        "-", first
    )


def test_a_replay_with_no_recording_stops_the_run_before_anything_is_sent(
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REPLAY_VARIABLE, "replay")

    result = thread_command(
        "run", "--scenario", SCENARIO_ID, "--model", "anthropic", "--thread", "replayed"
    )

    assert result.exit_code == 2
    assert "The run failed: ReplayMiss: no recording for this call" in result.stderr
    assert f"{REPLAY_VARIABLE}=record and a cap" in result.stderr
    assert "agentops resume replayed --model anthropic" in result.stderr
    assert scripted_anthropic.sent == []


# The eval.


def live_eval(tmp_path: Path, *arguments: str) -> Result:
    """`agentops eval --model anthropic --dev` over a split whose dev side is the clean bad
    deploy alone, writing its rows to `rows.json`."""
    scenarios_dir, evals_dir = tmp_path / "scenarios", tmp_path / "evals"
    if not scenarios_dir.exists():
        temporary_frozen_split(tmp_path)
    options = split_options(scenarios_dir, evals_dir)
    return runner.invoke(
        cli.app,
        ["eval", "--model", "anthropic", "--dev", *options, "--json", "rows.json", *arguments],
    )


def test_the_live_plan_names_the_cap_and_the_recordings_and_yes_needs_a_cap(
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    planned = live_eval(tmp_path)
    refused = live_eval(tmp_path, "--yes")

    assert planned.exit_code == 1
    assert f"API cap: none; export {CAP_VARIABLE} (in dollars) before --yes" in planned.stdout
    assert f"recordings: {spend_files[REPLAY_DIR_VARIABLE]}" in planned.stdout
    assert refused.exit_code == 1
    assert CAP_VARIABLE in refused.stderr
    assert table_rows(refused.stdout) == []
    assert scripted_anthropic.sent == []


def test_a_live_eval_replays_for_free_with_no_key_and_no_yes(
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv(CAP_VARIABLE, "1")
    first = live_eval(tmp_path, "--repetitions", "2", "--yes")
    first_rows = json.loads((tmp_path / "rows.json").read_text(encoding="utf-8"))
    sent = len(scripted_anthropic.sent)

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv(CAP_VARIABLE)
    monkeypatch.setenv(REPLAY_VARIABLE, "replay")
    again = live_eval(tmp_path, "--repetitions", "2")
    again_rows = json.loads((tmp_path / "rows.json").read_text(encoding="utf-8"))

    assert first.exit_code == 0, first.output
    assert again.exit_code == 0, again.output
    assert len(scripted_anthropic.sent) == sent
    assert f"API credit: ${sent * DEV_CALL_PRICE:.4f} billed for {sent} live calls." in first.stdout
    assert f"0 live calls; {sent} answered from recordings" in again.stdout
    assert "replay: every answer comes from the recordings" in again.stdout
    assert "estimated cost" not in again.stdout
    assert [verdicts(row) for row in again_rows] == [verdicts(row) for row in first_rows]
    assert sum(row["api"]["calls"] for row in first_rows) == sent
    assert all(row["api"]["replayed"] == 0 for row in first_rows)
    assert all(row["api"]["replayed"] == row["api"]["calls"] for row in again_rows)


def verdicts(row: dict[str, Any]) -> tuple[Any, ...]:
    outcome = row["outcome"]
    passes = tuple(row["evaluators"][name] for name in PASS_FAIL_EVALUATORS)
    return (row["system"], row["repetition"], outcome["root_cause_label"], passes)


def test_an_eval_the_cap_stops_is_not_failed_rows_and_its_rerun_replays_what_ran(
    scripted_anthropic: type[ScriptedAnthropic],
    spend_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cap below one call's price lets the first call through and stops the second; the
    rerun with room replays the first call and pays only for the rest."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv(CAP_VARIABLE, "0.001")
    stopped = live_eval(tmp_path, "--system", "graph", "--yes")

    monkeypatch.setenv(CAP_VARIABLE, "1")
    finished = live_eval(tmp_path, "--system", "graph", "--yes")

    assert stopped.exit_code == 1
    assert "The eval stopped: SpendRefused" in stopped.stderr
    assert "The answers so far are recorded" in stopped.stderr
    assert table_rows(stopped.stdout) == []
    assert finished.exit_code == 0, finished.output
    assert "; 1 answered from recordings" in finished.stdout
    billed = sum(row["billed"] for row in ledger_rows(spend_files))
    assert billed == pytest.approx(len(scripted_anthropic.sent) * DEV_CALL_PRICE)
