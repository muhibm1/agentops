"""What a live run may spend, and the recorded answers that make a rerun free.

Every call of a client `model_factory` builds goes through Flywheel's spend ladder
(`flywheel.spend`), which LangChain asks through its cache slot (`flywheel.langchain_spend`): an
answer recorded before is replayed for free; any other call needs a dollar cap with room left,
and is added to the ledger and recorded once it returns. A refusal or a replay miss is raised
before anything is sent. The settings come from the environment, like the key:

- `AGENTOPS_API_CAP`: the dollars the ledger may reach in total. Unset or 0, no paid call is
  sent; raise it to spend more.
- `AGENTOPS_API_LEDGER`: the ledger, `~/.agentops/api-ledger.jsonl` unless set. One row per
  call: tokens, list price and what was billed. `flywheel spend <file>` totals it.
- `AGENTOPS_REPLAY`: `record` (the default: replay what was recorded, record the rest),
  `replay` (recordings only: a call with none stops the run, and no key or cap is needed) or
  `off` (every call live, nothing recorded or replayed).
- `AGENTOPS_REPLAY_DIR`: the recordings, `~/.agentops/replay` unless set.

A recording is keyed by its request with the prompt's per-run codes blanked
(`without_run_codes`): the fence code and the idempotency keys change on every run without
changing the question, so a rerun of the same work replays. The prompt sent is never changed.
"""

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import flywheel.spend as spend
from flywheel.langchain_spend import SpendCache

CAP_VARIABLE = "AGENTOPS_API_CAP"
LEDGER_VARIABLE = "AGENTOPS_API_LEDGER"
REPLAY_VARIABLE = "AGENTOPS_REPLAY"
REPLAY_DIR_VARIABLE = "AGENTOPS_REPLAY_DIR"
VARIABLES = (CAP_VARIABLE, LEDGER_VARIABLE, REPLAY_VARIABLE, REPLAY_DIR_VARIABLE)
DEFAULT_MODE = "record"
PROJECT = "agentops"
# The code `agentops.nodes.fence_markers` and `fence_rule` put in the markers and in the
# sentence that names them, and an idempotency key (`agentops.actions.registry`) in an evidence
# payload, as they appear in a prompt.
FENCE_CODE = re.compile(r"(untrusted evidence |end )[0-9a-f]{16}(?=>>>|')")
IDEMPOTENCY_KEY = re.compile(r"(idempotency_key\W{1,8})[0-9a-f]{64}")


class SpendSettingError(ValueError):
    """A spend setting in the environment is not one AgentOps can use."""


def agentops_home() -> Path:
    """Where the ledger and the recordings live unless the environment says otherwise."""
    return Path.home() / ".agentops"


def guard_from(environ: Mapping[str, str]) -> spend.Guard:
    """The cap and the ledger the environment sets. Raises `SpendSettingError`, naming the
    variable and never its value, when the cap is not a dollar amount."""
    text = environ.get(CAP_VARIABLE, "").strip()
    try:
        cap = float(text) if text else 0.0
    except ValueError:
        cap = math.nan
    if not math.isfinite(cap) or cap < 0:
        raise SpendSettingError(f"{CAP_VARIABLE} must be a dollar amount, such as 2 or 0.50.")
    ledger = environ.get(LEDGER_VARIABLE, "").strip() or agentops_home() / "api-ledger.jsonl"
    return spend.Guard(cap, ledger, project=PROJECT, cap_variable=CAP_VARIABLE)


def recorder_from(environ: Mapping[str, str]) -> spend.Recorder:
    """The recordings the environment sets, in `record` mode unless it names another. Raises
    `SpendSettingError` for a mode that is not `record`, `replay` or `off`."""
    mode = environ.get(REPLAY_VARIABLE, "").strip().lower() or DEFAULT_MODE
    if mode not in spend.MODES:
        raise SpendSettingError(f"{REPLAY_VARIABLE} must be record, replay or off.")
    root = environ.get(REPLAY_DIR_VARIABLE, "").strip() or agentops_home() / "replay"
    return spend.Recorder(root, mode)


def is_replaying(recorder: spend.Recorder | None) -> bool:
    return recorder is not None and recorder.mode == "replay"


def without_run_codes(prompt: str) -> str:
    """`prompt` with its fence code and idempotency keys blanked, for keying a recording."""
    return IDEMPOTENCY_KEY.sub(r"\1-", FENCE_CODE.sub(r"\1-", prompt))


def answer_cache(
    guard: spend.Guard | None, recorder: spend.Recorder | None, role: str
) -> SpendCache:
    """The cache slot a client for `role` asks before every call."""
    return SpendCache(guard, recorder, label=f"{PROJECT} {role}", normalize=without_run_codes)


def refusal(guard: spend.Guard | None, recorder: spend.Recorder | None) -> str | None:
    """Why a live run may not start, or None: it spends nothing, it replays, or the cap has
    room left. The same refusal stops any call once the run is going."""
    if guard is None or is_replaying(recorder):
        return None
    if guard.cap <= 0:
        return (
            f"--model anthropic spends API credit, and {CAP_VARIABLE} is not set: export it as "
            f"a dollar cap, or replay recorded answers with {REPLAY_VARIABLE}=replay."
        )
    spent = guard.spent()
    if spent >= guard.cap:
        return (
            f"The API cap is spent: {dollars(spent)} of {CAP_VARIABLE}={dollars(guard.cap)} is "
            f"in {guard.ledger}. Raise {CAP_VARIABLE} to spend more."
        )
    return None


def plan_lines(guard: spend.Guard | None, recorder: spend.Recorder | None) -> list[str]:
    """What a run would spend under: the cap and what is spent, and where answers are
    recorded."""
    if guard is None:
        return []
    if recorder is not None and recorder.mode == "replay":
        return [
            f"replay: every answer comes from the recordings in {recorder.root}; nothing is "
            "spent, and a call with no recording stops the run."
        ]
    if guard.cap <= 0:
        cap = f"API cap: none; export {CAP_VARIABLE} (in dollars) before --yes, or nothing is sent."
    else:
        cap = (
            f"API cap: {dollars(guard.cap)} ({CAP_VARIABLE}), {dollars(guard.spent())} spent so "
            f"far by {guard.ledger}."
        )
    if recorder is None or recorder.mode == "off":
        return [cap, f"recordings: off ({REPLAY_VARIABLE}=off); every call is live."]
    return [cap, f"recordings: {recorder.root}; a call made before is replayed for free."]


def credit_line(summaries: list[dict[str, Any] | None]) -> str | None:
    """One line on the API credit the calls behind `summaries` (`flywheel.spend.Meter.summary`)
    used; None when none of them went through the spend ladder."""
    used = [summary for summary in summaries if summary]
    if not used:
        return None
    live = sum(summary["calls"] - summary["replayed"] for summary in used)
    replayed = sum(summary["replayed"] for summary in used)
    billed = sum(summary["billed"] for summary in used)
    line = f"API credit: {dollars(billed)} billed for {live} live call{'' if live == 1 else 's'}"
    if replayed:
        line += f"; {replayed} answered from recordings"
        if all("usd" in summary for summary in used):
            saved = sum(summary["usd"] for summary in used) - billed
            line += f" ({dollars(saved)} saved)"
    return line + "."


def dollars(amount: float) -> str:
    """Four decimals: a run on the dev model can cost less than a cent."""
    return f"${amount:.4f}"
