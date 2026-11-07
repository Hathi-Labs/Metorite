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


def _c(slug):
    return [(f"metorite-dev-{slug}-pg", f"/home/dev/wt-{slug}"), (f"metorite-dev-{slug}-redis", f"/home/dev/wt-{slug}")]


def test_stack_with_no_worktree_stops():
    out = acts(containers=_c("gone"), existing=set())
    assert [(a["act"], a["repo"]) for a in out] == [("stop-stack", "/home/dev/wt-gone")]


def test_idle_stack_stops_and_live_one_stays():
    out = acts(
        containers=_c("old") + _c("live") + _c("fresh"),
        existing={"/home/dev/wt-old", "/home/dev/wt-live", "/home/dev/wt-fresh"},
        worktrees=[wt("old", age_h=17), wt("live", age_h=17), wt("fresh", age_h=1)],
        busy_paths={"/home/dev/wt-live/apps"},
    )
    assert [a["repo"] for a in out] == ["/home/dev/wt-old"]


def test_stack_of_the_primary_checkout_or_an_untracked_path_stays():
    """The primary checkout is no wt-* dir. Its live stack must not read as 'worktree gone'."""
    cs = [("metorite-dev-metorite-pg", "/home/dev/Metorite"), ("metorite-dev-preview-pg", "/tmp/x")]
    assert acts(containers=cs, existing={"/home/dev/Metorite", "/tmp/x"}) == []


def test_container_without_label_is_ignored_by_collect_contract():
    assert acts(containers=[("metorite-dev-x-pg", "")]) == []


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
