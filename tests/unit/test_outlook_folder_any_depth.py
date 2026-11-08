"""H-267: find the Outlook folder at any depth before creating it."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from email_ingestion.providers.outlook import OutlookProvider


def _resp(body: dict, *, ok: bool = True, status: int = 200, text: str = ""):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=body)
    r.is_success = ok
    r.status_code = status
    r.text = text
    return r


class FakeGraph:
    """Top-level folders plus a children map. Counts the calls."""

    def __init__(self, top, children, *, fail_children=(), post_status=201,
                 filter_hits=None):
        self.top, self.children = top, children
        self.fail_children = set(fail_children)
        self.posts: list[dict] = []
        self.moves: list[str] = []
        self.tree_reads = 0
        self.post_status = post_status
        self.filter_hits = filter_hits  # callable(n_filter_calls) -> list
        self.filter_calls = 0
        self.dead_ids: set[str] = set()

    async def get(self, url, params=None):
        if url == "/me/mailFolders" and params and "$filter" in params:
            self.filter_calls += 1
            hits = self.filter_hits(self.filter_calls) if self.filter_hits else []
            return _resp({"value": hits})
        if url == "/me/mailFolders":
            self.tree_reads += 1
            return _resp({"value": self.top})
        fid = url.split("/")[3]
        if fid in self.fail_children:
            raise RuntimeError("403")
        return _resp({"value": self.children.get(fid, [])})

    async def post(self, url, json=None):
        if url == "/me/mailFolders":
            self.posts.append(json)
            if self.post_status == 409:
                return _resp({}, ok=False, status=409, text="ErrorFolderExists")
            return _resp({"id": "NEW"}, status=201)
        self.moves.append(json["destinationId"])
        if json["destinationId"] in self.dead_ids:
            return _resp({}, ok=False, status=404)
        return _resp({"id": "moved"})


def _f(fid, name, kids=0):
    return {"id": fid, "displayName": name, "childFolderCount": kids}


def _p(graph: FakeGraph) -> OutlookProvider:
    p = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    p._get_client = AsyncMock(return_value=graph)  # type: ignore[method-assign]
    return p


INBOX_TREE = dict(
    top=[_f("inbox", "Inbox", 1), _f("other", "Other", 1)],
    children={"inbox": [_f("N-in", "Newsletter")],
              "other": [_f("N-ot", "newsletter")]},
)


async def test_a_nested_same_name_folder_makes_no_post() -> None:
    g = FakeGraph(**INBOX_TREE)
    assert await _p(g)._get_or_create_folder_id("NEWSLETTER ") == "N-in"
    assert g.posts == []


async def test_b_top_level_wins_over_nested() -> None:
    t = dict(INBOX_TREE)
    t["top"] = [*t["top"], _f("N-top", "Newsletter")]
    g = FakeGraph(**t)
    assert await _p(g)._get_or_create_folder_id("Newsletter") == "N-top"
    assert g.posts == []


async def test_c_inbox_subtree_wins_over_other_nested() -> None:
    t = dict(INBOX_TREE)
    t["top"] = [_f("other", "Other", 1), _f("inbox", "Inbox", 1)]
    g = FakeGraph(**t)
    assert await _p(g)._get_or_create_folder_id("Newsletter") == "N-in"


async def test_d_no_match_makes_one_post() -> None:
    g = FakeGraph(top=[_f("inbox", "Inbox")], children={})
    assert await _p(g)._get_or_create_folder_id("Newsletter") == "NEW"
    assert g.posts == [{"displayName": "Newsletter"}]


async def test_e_partial_tree_without_match_makes_no_post() -> None:
    g = FakeGraph(top=[_f("inbox", "Inbox", 1)], children={},
                  fail_children=["inbox"])
    with pytest.raises(ValueError, match="could not be read"):
        await _p(g)._get_or_create_folder_id("Newsletter")
    assert g.posts == []


async def test_f_two_moves_one_tree_read() -> None:
    g = FakeGraph(**INBOX_TREE)
    p = _p(g)
    await p.move_to_folder("m1", "Newsletter")
    await p.move_to_folder("m2", "Newsletter")
    assert g.tree_reads == 1
    assert g.moves == ["N-in", "N-in"]


async def test_g_cached_id_that_answers_404_is_resolved_again() -> None:
    g = FakeGraph(**INBOX_TREE)
    p = _p(g)
    await p.move_to_folder("m1", "Newsletter")
    g.dead_ids.add("N-in")
    g.children["inbox"] = []  # the folder was deleted; the other one stays
    await p.move_to_folder("m2", "Newsletter")
    assert g.moves == ["N-in", "N-in", "N-ot"]
    assert g.tree_reads == 2


async def test_h_409_race_reread_finds_the_nested_folder() -> None:
    # The first lookup sees nothing. The create races (409). The re-read
    # then finds the folder in the tree.
    g = FakeGraph(top=[_f("inbox", "Inbox", 1)], children={}, post_status=409)
    p = _p(g)
    calls = {"n": 0}
    real = p.list_folders

    async def tree():
        calls["n"] += 1
        if calls["n"] == 1:
            return await real()
        g.children["inbox"] = [_f("N-in", "Newsletter")]
        return await real()

    p.list_folders = tree  # type: ignore[method-assign]
    assert await p._get_or_create_folder_id("Newsletter") == "N-in"
    assert len(g.posts) == 1
