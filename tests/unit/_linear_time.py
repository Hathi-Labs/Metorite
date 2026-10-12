"""Fence a linear-time claim by the RATIO of two timings, not by a wall clock.

**Why this exists.** Each ``*_is_linear`` fence once asserted that one large
input finished in under 0.5 s. On 2026-10-12 the deploy gate failed on
``test_parse_due_is_linear_on_a_long_text[many_dates]``: the parse takes about
80 ms on a dev box, and it took 1.7 s on a loaded CI runner. A rerun passed.
A wall clock measures the runner as much as the code.

**What this measures instead.** The same work at size ``n / 8`` and at size
``n``, on the same runner, a moment apart. Linear code takes about 8 times as
long on the large input. A quadratic regex takes about 64 times as long. The
bound is 16, so a log factor and a cache effect still pass, and a quadratic
search still fails. Load slows both sizes alike, so the ratio holds steady.

**Why the best of several runs.** Load only ever adds time to a run. The
fastest run is the one that load touched least, so the best time is the
truest one. The small size is timed first, fully. The large size then runs
until its best time meets the bound. A linear fence passes on the first or
second try. A quadratic one never meets the bound and fails when the
attempts or the budget run out.

The budget only stops the retries. It never decides the result.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable

#: Size of the large input over the small one.
FACTOR = 8
#: The largest ratio a linear fence may show at ``FACTOR``. Linear code gives
#: about 8 and quadratic code about 64.
MAX_RATIO = 16.0


def _timed(setup: Callable[[int], Callable[[], object]], size: int) -> float:
    """Seconds for one call of the thunk that ``setup(size)`` returns.

    The setup runs outside the clock. The garbage collector stays off inside
    it, as ``timeit`` does, so a collection of the setup's garbage is never
    billed to the code under test. A full ``gc.collect()`` here costs about
    0.2 s in a large pytest process, so the helper does not call it."""
    thunk = setup(size)
    gc.disable()
    try:
        began = time.perf_counter()
        thunk()
        return time.perf_counter() - began
    finally:
        gc.enable()


def assert_linear(
    setup: Callable[[int], Callable[[], object]],
    n: int,
    *,
    repeats: int = 5,
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
    # A floor of 1 us keeps a clock tick of zero from a division by zero.
    best_small = max(min(_timed(setup, small) for _ in range(repeats)), 1e-6)
    stop = time.perf_counter() + budget_s
    best_large = float("inf")
    for _ in range(attempts):
        best_large = min(best_large, _timed(setup, n))
        if best_large <= MAX_RATIO * best_small:
            return
        if time.perf_counter() > stop:
            break
    raise AssertionError(
        f"not linear: size {small} took {best_small * 1000:.2f} ms and size {n} "
        f"took {best_large * 1000:.2f} ms, a ratio of {best_large / best_small:.1f} "
        f"for a {FACTOR}x input (the bound is {MAX_RATIO:g})"
    )
