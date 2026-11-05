"""Fence for scripts/reaper.py (CIV-14j): fake containers, worktrees and clock."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import reaper  # noqa: E402

NOW = 1_000_000.0
H = 3600


def wt(slug, head="abc", dirty=False, stack=None, age_h=0.0, branch=None):
    return reaper.Worktree(Path(f"/home/dev/wt-{slug}"), branch or slug, head, dirty, stack, NOW - age_h * H)


def acts(**kw):
    return reaper.plan(reaper.Env(now=NOW, **kw))


def test_stack_with_no_worktree_stops():
    out = acts(containers=["metorite-dev-gone-pg", "metorite-dev-gone-redis"])
    assert [(a["act"], a["stack"]) for a in out] == [("stop-stack", "metorite-dev-gone")]


def test_idle_stack_stops_and_live_one_stays():
    w1 = wt("old", stack="metorite-dev-old", age_h=17)
    w2 = wt("live", stack="metorite-dev-live", age_h=17)
    w3 = wt("fresh", stack="metorite-dev-fresh", age_h=1)
    out = acts(
        containers=["metorite-dev-old-pg", "metorite-dev-live-pg", "metorite-dev-fresh-pg"],
        worktrees=[w1, w2, w3],
        busy_paths={"/home/dev/wt-live/apps"},
    )
    assert [a["stack"] for a in out] == ["metorite-dev-old"]


def test_merged_clean_idle_worktree_is_removed():
    w = wt("done", head="sha1")
    out = acts(worktrees=[w], merged_heads={"done": {"sha1"}})
    assert out == [{"act": "remove-worktree", "slug": "done", "path": "/home/dev/wt-done", "why": "PR merged at sha1"}]


def test_worktree_is_kept_when_dirty_busy_head_differs_or_unmerged():
    cases = [
        (wt("a", head="s1", dirty=True), {"a": {"s1"}}, set()),
        (wt("b", head="s1"), {"b": {"s1"}}, {"/home/dev/wt-b"}),
        (wt("c", head="s2"), {"c": {"s1"}}, set()),
        (wt("d", head="s1"), {}, set()),
    ]
    for w, merged, busy in cases:
        assert acts(worktrees=[w], merged_heads=merged, busy_paths=busy) == [], w.path


def test_stack_of_strips_service_suffix():
    assert reaper.stack_of("metorite-dev-x-redis") == "metorite-dev-x"
