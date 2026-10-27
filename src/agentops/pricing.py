"""The price table and the cost of a run, from the token counts the model replies carried.

The prices were transcribed by hand from the Anthropic pricing page on `PRICES_AS_OF`, not
computed or remembered; check them against `PRICES_SOURCE` before trusting a figure. An id the
table does not name is `Unpriced`, never a guess, a partial sum or zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from agentops.models import GRAPH_ROLES, ModelRole

TOKENS_PER_PRICE_UNIT = 1_000_000
PRICES_AS_OF = "2026-10-09"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"

# Design §11 upper bound, believed; replace with the measured mean after the first paid run.
ESTIMATED_CALLS_PER_RUN = {"graph": 12, "baseline": 12, "hitl": 12}
# Design §11 upper bound, believed; replace with the measured mean after the first paid run.
ESTIMATED_INPUT_TOKENS_PER_CALL = 6_000
# Design §11 upper bound, believed; replace with the measured mean after the first paid run.
ESTIMATED_OUTPUT_TOKENS_PER_CALL = 1_000
# Design §11 upper bound, believed; replace with the measured mean after the first paid run.
ESTIMATED_JUDGE_TOKENS = (2_000, 100)


@dataclass(frozen=True)
class ModelPrice:
    """US dollars per million tokens. `cache_write` is the five-minute cache, the only one used."""

    input: float
    output: float
    cache_write: float
    cache_read: float


@dataclass(frozen=True)
class Unpriced:
    """The first model id the price table does not name."""

    model_id: str


class Usage(Protocol):
    """What `cost_usd` reads from a token count; `agentops.evals.outcome.TokenUsage` fits."""

    @property
    def input_tokens(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    @property
    def cache_read_tokens(self) -> int: ...
    @property
    def cache_write_tokens(self) -> int: ...


PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_write=2.50, cache_read=0.10),
    "claude-haiku-4-5-20251001": ModelPrice(
        input=1.00, output=5.00, cache_write=1.25, cache_read=0.10
    ),
}


def cost_usd(usage_by_model: Mapping[str, Usage]) -> float | Unpriced:
    """The dollar cost of `usage_by_model`. Input tokens are charged at the input price after
    the cache reads and writes they include are taken out and charged at their own prices."""
    total = 0.0
    for model_id, usage in usage_by_model.items():
        price = PRICES.get(model_id)
        if price is None:
            return Unpriced(model_id)
        uncached = max(usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens, 0)
        total += (
            uncached * price.input
            + usage.output_tokens * price.output
            + usage.cache_write_tokens * price.cache_write
            + usage.cache_read_tokens * price.cache_read
        ) / TOKENS_PER_PRICE_UNIT
    return total


def estimated_cost(
    model_ids: Mapping[ModelRole, str], runs_per_system: Mapping[str, int], judge_calls: int
) -> float | Unpriced:
    """An upper-bound cost for the planned runs, priced role by role with each role's own id.

    A graph run's estimated calls are split evenly over the four graph roles; a baseline run's,
    with or without the approval pause, go to the baseline id; each judge call is priced at
    `ESTIMATED_JUDGE_TOKENS`. The first id the table does not name makes the whole estimate
    `Unpriced`."""
    total = 0.0
    graph_runs = runs_per_system.get("graph", 0)
    if graph_runs:
        calls_per_role = ESTIMATED_CALLS_PER_RUN["graph"] / len(GRAPH_ROLES)
        for role in GRAPH_ROLES:
            cost = call_cost(model_ids[role], graph_runs * calls_per_role)
            if isinstance(cost, Unpriced):
                return cost
            total += cost
    for system in ("baseline", "hitl"):  # the approval variant runs the baseline's model
        runs = runs_per_system.get(system, 0)
        if runs:
            cost = call_cost(model_ids["baseline"], runs * ESTIMATED_CALLS_PER_RUN[system])
            if isinstance(cost, Unpriced):
                return cost
            total += cost
    if judge_calls:
        cost = call_cost(model_ids["judge"], judge_calls, ESTIMATED_JUDGE_TOKENS)
        if isinstance(cost, Unpriced):
            return cost
        total += cost
    return total


def call_cost(
    model_id: str,
    calls: float,
    tokens_per_call: tuple[int, int] = (
        ESTIMATED_INPUT_TOKENS_PER_CALL,
        ESTIMATED_OUTPUT_TOKENS_PER_CALL,
    ),
) -> float | Unpriced:
    """The cost of `calls` uncached calls of `tokens_per_call` (input, output) on `model_id`."""
    price = PRICES.get(model_id)
    if price is None:
        return Unpriced(model_id)
    input_tokens, output_tokens = tokens_per_call
    return (
        calls * (input_tokens * price.input + output_tokens * price.output) / TOKENS_PER_PRICE_UNIT
    )
