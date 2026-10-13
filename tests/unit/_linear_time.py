"""Fence a linear-time claim by comparing two equal amounts of work.

**Why this exists.** Each ``*_is_linear`` fence once asserted that one large
input finished in under 0.5 s. On 2026-10-12 the deploy gate failed on
``test_parse_due_is_linear_on_a_long_text[many_dates]``: the parse takes about
80 ms on a dev box, and it took 1.7 s on a loaded CI runner. A rerun passed.
A wall clock measures the runner as much as the code.

**What this measures instead.** Block A runs the work once at size ``n``.
Block B runs it 8 times at size ``n / 8``, one call after the other. For
linear code the two blocks do the same work, so they take about the same
time. A quadratic regex makes block A about 8 times slower than block B. The
fence fails when A takes more than twice as long as B. A log factor and a
cache effect stay well under 2.

**Why two blocks of equal length, and not one small call.** The first
version timed ONE small call against one large call, and then needed a ratio
of 8. Under CPU load that failed on linear code with ratios of 20 to 35. A
short call fits inside one time slice of the scheduler. A long call is
preempted, and each wait is billed to it. Two blocks of equal length meet the
same preemption, so load cancels out of the ratio.

**Why the best of several runs.** Load only ever adds time to a run. The
fastest run is the one that load touched least. Each attempt times both
blocks again, and the fence keeps the best time of each. It passes as soon as
the two best times meet the bound. A linear fence passes on the first or
second try. A quadratic one never meets the bound and fails when the attempts
or the budget run out. The budget only stops the retries. It never decides
the result.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable

#: Size of the large input over the small one, and so the number of small
#: calls in block B.
FACTOR = 8
#: The largest ratio of block A over block B that a linear fence may show.
#: Linear code gives about 1 and quadratic code about ``FACTOR``.
MAX_RATIO = 2.0


def _timed(setup: Callable[[int], Callable[[], object]], size: int, calls: int) -> float:
    """Seconds for ``calls`` thunks of ``setup(size)``, run back to back.

    Every setup runs before the clock starts, so a fence whose code changes
    its input gets a fresh input for each call. The garbage collector stays
    off inside the clock, as ``timeit`` does. A full ``gc.collect()`` costs
    about 0.2 s in a large pytest process, so the helper does not call it."""
    thunks = [setup(size) for _ in range(calls)]
    gc.disable()
    try:
        began = time.perf_counter()
        for thunk in thunks:
            thunk()
        return time.perf_counter() - began
    finally:
        gc.enable()


def assert_linear(
    setup: Callable[[int], Callable[[], object]],
    n: int,
    *,
    attempts: int = 15,
    budget_s: float = 30.0,
) -> None:
    """Fail when the work grows faster than linearly from ``n // 8`` to ``n``.

    ``setup(size)`` builds the input of that size and returns a thunk with no
    arguments. Only the thunk is timed. Build a fresh input in ``setup`` when
    the code under test changes its input."""
    small = n // FACTOR
    assert small > 0, f"n={n} is too small to divide by {FACTOR}"
    setup(small)()  # Warm up: a lazy import or a regex compile is not the work.
    stop = time.perf_counter() + budget_s
    best_a = best_b = float("inf")
    for _ in range(attempts):
        best_b = min(best_b, _timed(setup, small, FACTOR))
        best_a = min(best_a, _timed(setup, n, 1))
        if best_a <= MAX_RATIO * best_b:
            return
        if time.perf_counter() > stop:
            break
    raise AssertionError(
        f"not linear: 1 call at size {n} took {best_a * 1000:.2f} ms, and "
        f"{FACTOR} calls at size {small} took {best_b * 1000:.2f} ms. The ratio "
        f"is {best_a / best_b:.1f} and the bound is {MAX_RATIO:g}"
    )


def assert_linear_count(work: Callable[[int], int], n: int) -> None:
    """Fail when a COUNT of work grows faster than linearly from ``n // 8`` to ``n``.

    ``work(size)`` runs the code once at that size and returns how much work
    it did, in a unit that a spy counts: the characters handed to a regex,
    the rows read. The count reads no clock, so load cannot move it, and the
    same input gives the same count on every run. Use this in place of
    :func:`assert_linear` when a spy can see the work that the fence is about.

    Block A is one call at size ``n``. Block B is 8 calls at size ``n / 8``.
    Linear code counts about the same in both. A rescan of the whole input on
    each step counts about 8 times more in block A. The bound is the same
    :data:`MAX_RATIO` as the timed fence.

    ``test_router_dsml_tool_calls.py`` failed :func:`assert_linear` at 2.1 and
    2.2 against 2 on CI (2026-10-12 and 2026-10-13). The rescans it fences
    are bounded. What the clock also saw is a copy of the region on each
    character, which no rescan fence is about."""
    small = n // FACTOR
    assert small > 0, f"n={n} is too small to divide by {FACTOR}"
    block_a = work(n)
    block_b = sum(work(small) for _ in range(FACTOR))
    assert block_b > 0, "the spy counted no work, so the fence proves nothing"
    if block_a > MAX_RATIO * block_b:
        raise AssertionError(
            f"not linear: 1 call at size {n} did {block_a} units of work, and "
            f"{FACTOR} calls at size {small} did {block_b}. The ratio is "
            f"{block_a / block_b:.1f} and the bound is {MAX_RATIO:g}"
        )
