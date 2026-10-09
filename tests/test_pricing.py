"""E11 to E13: the price table and `cost_usd` (R7)."""

from datetime import date

import pytest

from agentops import pricing
from agentops.evals.outcome import TokenUsage
from agentops.pricing import PRICES, PRICES_AS_OF, PRICES_SOURCE, ModelPrice, Unpriced, cost_usd

TEST_PRICE = ModelPrice(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30)


def test_cost_charges_uncached_input_output_writes_and_reads_at_their_own_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E11: hand-computed to the cent."""
    monkeypatch.setitem(pricing.PRICES, "m", TEST_PRICE)
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=200_000,
        cache_write_tokens=100_000,
    )
    expected = (700_000 * 3 + 100_000 * 15 + 100_000 * 3.75 + 200_000 * 0.30) / 1e6

    cost = cost_usd({"m": usage})

    assert isinstance(cost, float)
    assert round(cost, 2) == round(expected, 2)


def test_an_unpriced_id_makes_the_cost_unpriced_and_empty_usage_costs_nothing() -> None:
    """E12."""
    usage = TokenUsage(input_tokens=10, output_tokens=1)

    assert cost_usd({"claude-sonnet-5-5": usage, "scripted": usage}) == Unpriced("scripted")
    assert isinstance(cost_usd({"claude-sonnet-5-5": usage}), float)
    assert cost_usd({}) == 0.0


def test_the_shipped_table_prices_both_default_ids_with_a_date_and_a_source() -> None:
    """E13."""
    for model_id in ("claude-sonnet-5-5", "claude-haiku-4-5-20251001"):
        price = PRICES[model_id]
        assert min(price.input, price.output, price.cache_write, price.cache_read) > 0
    assert date.fromisoformat(PRICES_AS_OF) <= date.today()
    assert PRICES_SOURCE.startswith("https://")


def test_the_estimate_is_priced_role_by_role_and_stops_at_an_unpriced_id() -> None:
    """R9, D10: a dearer rca id raises only the rca share; an unpriced id gives no figure."""
    haiku = dict.fromkeys(
        ("triage", "rca", "postmortem", "escalate", "baseline", "judge"),
        "claude-haiku-4-5-20251001",
    )
    runs = {"graph": 10, "baseline": 10}
    cheap = pricing.estimated_cost(haiku, runs, 10)  # type: ignore[arg-type]
    mixed = pricing.estimated_cost({**haiku, "rca": "claude-sonnet-5-5"}, runs, 10)  # type: ignore[arg-type]
    rca_calls = 10 * pricing.ESTIMATED_CALLS_PER_RUN["graph"] / 4

    assert isinstance(cheap, float) and isinstance(mixed, float)
    sonnet_rca = pricing.call_cost("claude-sonnet-5-5", rca_calls)
    haiku_rca = pricing.call_cost("claude-haiku-4-5-20251001", rca_calls)
    assert isinstance(sonnet_rca, float) and isinstance(haiku_rca, float)
    assert round(mixed - cheap, 6) == round(sonnet_rca - haiku_rca, 6)
    unpriced = pricing.estimated_cost({**haiku, "rca": "claude-x"}, runs, 10)  # type: ignore[arg-type]
    assert unpriced == Unpriced("claude-x")
