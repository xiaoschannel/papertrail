"""How fast a run may call a hosted model: the pace the reported limits allow, and backing off."""

import pytest

from rate_budget import RateBudget, duration


def _headers(requests_left=500, tokens_left=200_000, requests_limit=500, tokens_limit=200_000,
             requests_reset="30s", tokens_reset="1m0s"):
    return {"x-ratelimit-limit-requests": str(requests_limit),
            "x-ratelimit-remaining-requests": str(requests_left),
            "x-ratelimit-reset-requests": requests_reset,
            "x-ratelimit-limit-tokens": str(tokens_limit),
            "x-ratelimit-remaining-tokens": str(tokens_left),
            "x-ratelimit-reset-tokens": tokens_reset}


def _calls(budget, count, seconds=1.5, tokens=2_000):
    """``count`` calls that went through, each taking ``seconds`` and spending ``tokens``."""
    for _ in range(count):
        budget.acquire()
        budget.spent(tokens)
        budget.release(ok=True, seconds=seconds)


@pytest.mark.parametrize("text, seconds", [("120ms", 0.12), ("6s", 6), ("1m30s", 90), ("2h", 7200), ("", None)])
def test_reset_headers_are_read_as_seconds(text, seconds):
    assert duration(text) == seconds


def test_nothing_is_assumed_before_a_response_has_said_what_the_limits_are():
    budget = RateBudget(slots=4)
    assert budget.wait_for() == 0
    assert budget.target() == 4          # a first call has to happen for there to be anything to go on


def test_the_pace_is_what_the_request_limit_allows_at_the_speed_calls_are_going():
    # 5,000 requests a minute at 1.5s a call is 125 in flight; at 70% of that, 87.
    budget = RateBudget(ceiling=200)
    budget.observe(_headers(requests_limit=5_000, requests_left=5_000,
                            tokens_limit=100_000_000, tokens_left=100_000_000))

    _calls(budget, 1, seconds=1.5)

    assert budget.target() == 87


def test_the_token_limit_is_the_lower_ceiling_when_the_documents_are_long():
    # 2,000,000 tokens a minute at 1,500 a call is 1,333 calls a minute; at 1.5s each and 70%, 23.
    budget = RateBudget(ceiling=200)
    budget.observe(_headers(requests_limit=5_000, requests_left=5_000,
                            tokens_limit=2_000_000, tokens_left=2_000_000))

    _calls(budget, 1, seconds=1.5, tokens=1_500)

    assert budget.target() == 23


def test_a_run_climbs_towards_that_pace_one_call_at_a_time():
    budget = RateBudget(slots=4, ceiling=200)
    budget.observe(_headers(requests_limit=5_000, requests_left=5_000,
                            tokens_limit=100_000_000, tokens_left=100_000_000))

    _calls(budget, 1, seconds=1.5)
    assert budget.slots == 5             # one call is one step, however far off the target is

    _calls(budget, 20, seconds=1.5)
    assert budget.slots == 25


def test_the_ceiling_is_the_ceiling_however_much_the_limits_allow():
    budget = RateBudget(slots=4, ceiling=8)
    budget.observe(_headers(requests_limit=5_000, requests_left=5_000,
                            tokens_limit=100_000_000, tokens_left=100_000_000))

    _calls(budget, 50, seconds=1.5)

    assert budget.slots == 8


def test_being_refused_halves_the_pace_and_holds_every_thread_of_the_run():
    budget = RateBudget(slots=8, ceiling=200)
    budget.observe(_headers(requests_limit=5_000, requests_left=5_000,
                            tokens_limit=100_000_000, tokens_left=100_000_000))

    budget.rate_limited(retry_after=12.0)

    assert budget.slots == 4
    assert budget.wait_for() == pytest.approx(12, abs=1)


def test_a_run_stops_rather_than_squeezing_the_last_of_the_limit():
    budget = RateBudget()
    budget.observe(_headers(requests_left=400, tokens_left=150_000))
    assert budget.wait_for() == 0

    budget.observe(_headers(requests_left=400, tokens_left=31_000, tokens_reset="20s"))
    _calls(budget, 1, tokens=2_000)      # a call of this size would take it under the reserve

    assert budget.wait_for() == pytest.approx(20, abs=2)


def test_calls_are_held_back_once_the_pace_is_full():
    budget = RateBudget(slots=2)

    assert budget.acquire() and budget.acquire()
    assert not budget.acquire(timeout=0.01)        # the third waits for one of the two to come back

    budget.release()

    assert budget.acquire(timeout=0.01)


def test_what_the_provider_does_not_say_leaves_that_count_as_it_was():
    budget = RateBudget()
    budget.observe(_headers(requests_left=400, tokens_left=150_000))

    budget.observe({"x-ratelimit-remaining-requests": "399"})     # a response carrying only the one header

    assert (budget.requests_left, budget.requests_limit) == (399, 500)
    assert budget.tokens_left == 150_000
