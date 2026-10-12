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
