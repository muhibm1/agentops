"""A live run of the graph on Claude Sonnet 5.5, recorded once and replayed on every test run.

`evals/recordings/dev-sonnet-2026-11-06/` holds every answer the model gave in one live eval of
the graph over the five dev scenarios (21 calls, the explanation judge's included), recorded with
`AGENTOPS_REPLAY=record` on 2026-11-06. A recording is keyed by its request, so replaying needs no
key and spends nothing, and the graph's code still runs for real around the recorded answers.

What this catches: any change that alters a request the model saw (a prompt, the evidence it is
shown, the schema it answers in, the model or its settings). That call has no recording, the
replay stops, and this test fails naming the call. A change to code that leaves every request as it
was replays in full and must still score what the live run scored.

When a prompt change is intended, record a fresh run (about $0.25 of API credit on Sonnet 5.5) into
a new directory, point `RECORDINGS` at it, and say in the commit what the live scores were.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentops.budget import REPLAY_DIR_VARIABLE, REPLAY_VARIABLE
from agentops.cli import app

REPOSITORY = Path(__file__).resolve().parents[1]
RECORDINGS = REPOSITORY / "evals" / "recordings" / "dev-sonnet-2026-11-06"
LIVE_ROW = (
    "graph   5     5/5 100%             5/5 100%         5/5 100%              5/5 100%           "
    "5/5 100%  5/5 100%"
)


def test_the_recorded_live_run_replays_in_full_and_scores_what_it_scored_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REPLAY_VARIABLE, "replay")
    monkeypatch.setenv(REPLAY_DIR_VARIABLE, str(RECORDINGS))
    monkeypatch.setenv("AGENTOPS_API_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--model",
            "anthropic",
            "--system",
            "graph",
            "--scenarios-dir",
            str(REPOSITORY / "scenarios"),
            "--evals-dir",
            str(REPOSITORY / "evals"),
        ],
    )
    assert "ReplayMiss" not in result.output, (
        "A request the live run never made: a prompt, the evidence shown or the model changed. "
        "Record a new run if the change is intended (see this file's docstring).\n" + result.output
    )
    assert result.exit_code == 0, result.output
    assert LIVE_ROW in result.output
    assert "failures: none" in result.output
    assert "0 live calls; 21 answered from recordings" in result.output
