"""The Studio builder tools (STU-G1, ``ai_studio.md`` sections 6.2 and 14).

Hermetic: the catalog GET is mocked at ``httpx``. The fences:

* a refusal returns the validator's errors verbatim and no manifest;
* the 24,000-character cap;
* a catalog read that fails is reported, never skipped;
* no tool takes an org or a member, and no studio module runs code.

Mutation run (recorded in the PR): in ``studio_tools._checked`` replace the
``validate_manifest`` call by ``errors = []`` and the refusal tests go red.
"""
from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from acb_common.studio_manifest import validate_manifest
from acb_skills import studio_tools as st

REPO = Path(__file__).resolve().parents[2]
MEMBER = "jane@acme.test"

ROWS = [
    {"id": "tasks.create", "title": "Create a task", "class": "B", "source": "manifest",
     "connection": None, "input_schema": {"type": "object", "properties": {"x": {}}}},
    {"id": "tasks.list", "title": "List tasks", "class": "A", "source": "manifest",
     "connection": None, "input_schema": None},
    {"id": "integration:slack.post", "title": "Post to Slack", "class": "C",
     "source": "integration", "connection": "slack", "input_schema": None},
]
CLASSES = {r["id"]: r["class"] for r in ROWS}


class _Fake:
    """A stand-in for ``httpx.AsyncClient`` that serves one canned answer."""

    calls: ClassVar[list[tuple[str, dict]]] = []

    def __init__(self, status=200, body=None, error=None, **_kw):
        self.status, self.body, self.error = status, body, error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        _Fake.calls.append((url, dict(headers or {})))
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body, request=httpx.Request("GET", url))


def _serve(monkeypatch, status=200, body=None, error=None):
    _Fake.calls = []
    monkeypatch.setattr(st, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(
        st.httpx, "AsyncClient",
        lambda **kw: _Fake(status, {"actions": ROWS} if body is None else body, error, **kw))


@pytest.fixture
def catalog(monkeypatch):
    _serve(monkeypatch)


async def _valid() -> dict:
    out = await st.studio_draft("Track customer orders")
    assert out["ok"], out
    return out["manifest"]


def _with_action(uses: str) -> list[dict]:
    return [{"op": "add", "path": "/actions/-", "value": {"id": "go", "uses": uses}}]


# -- draft -------------------------------------------------------------------


async def test_draft_is_a_valid_skeleton_with_the_four_keys() -> None:
    out = await st.studio_draft("Track customer orders")
    assert set(out) == {"ok", "errors", "diff", "manifest"}
    assert out["ok"] and out["errors"] == []
    m = out["manifest"]
    assert m["studio"] == 1 and m["id"] == "track-customer-orders"
    assert m["purpose"] == "Track customer orders"
    assert m["objects"] == m["actions"] == m["views"] == m["workflows"] == []
    assert validate_manifest(m) == []


@pytest.mark.parametrize("brief", ["", "   ", None, 5])
async def test_draft_refuses_an_empty_brief(brief) -> None:
    out = await st.studio_draft(brief)
    assert out["ok"] is False and "manifest" not in out and out["errors"]


@pytest.mark.parametrize("brief", ["123 numbers first", "Unicode cafe!", "x" * 400, "---", "A/B test"])
async def test_draft_is_valid_for_any_brief(brief) -> None:
    out = await st.studio_draft(brief)
    assert out["ok"], out
    assert validate_manifest(out["manifest"]) == []


async def test_draft_is_deterministic_and_makes_no_http_call(monkeypatch) -> None:
    _serve(monkeypatch)
    a, b = await st.studio_draft("Same brief"), await st.studio_draft("Same brief")
    assert a == b and _Fake.calls == []


# -- validate ----------------------------------------------------------------


async def test_validate_a_good_manifest_returns_it(catalog) -> None:
    m = await _valid()
    out = await st.studio_validate(m)
    assert out["ok"] and out["manifest"] == m and out["errors"] == []
    url, headers = _Fake.calls[0]
    assert url.endswith("/studio/actions") and headers["X-User-Email"] == MEMBER


async def test_validate_returns_the_validators_errors_verbatim(catalog) -> None:
    m = await _valid()
    m["id"] = "Bad Id"
    expected = [{"path": e.path, "message": e.message}
                for e in validate_manifest(m, catalog=CLASSES)]
    assert expected
    out = await st.studio_validate(m)
    assert out["ok"] is False and out["errors"] == expected and "manifest" not in out


async def test_validate_accepts_a_json_string_and_refuses_a_bad_one(catalog) -> None:
    m = await _valid()
    assert (await st.studio_validate(json.dumps(m)))["ok"]
    out = await st.studio_validate("{not json")
    assert out["ok"] is False and "JSON" in out["errors"][0]["message"]


async def test_validate_a_non_object_is_refused(catalog) -> None:
    out = await st.studio_validate([1, 2])
    assert out["ok"] is False and out["errors"][0]["path"] == "$"


# -- patch -------------------------------------------------------------------


async def test_patch_applies_and_returns_the_new_manifest_with_a_diff(catalog) -> None:
    m = await _valid()
    before = copy.deepcopy(m)
    ops = [{"op": "add", "path": "/objects/-", "value": {"name": "order", "fields": [
        {"key": "title", "type": "text"}]}}]
    out = await st.studio_patch(m, ops)
    assert out["ok"], out
    assert out["manifest"]["objects"][0]["name"] == "order"
    assert out["diff"] == ["add /objects/-"]
    assert m == before, "the input manifest is never changed"


async def test_patch_that_breaks_the_manifest_returns_errors_and_no_manifest(catalog) -> None:
    m = await _valid()
    before = copy.deepcopy(m)
    out = await st.studio_patch(m, [{"op": "replace", "path": "/id", "value": "Bad Id"}])
    assert out["ok"] is False and "manifest" not in out and out["errors"]
    broken = copy.deepcopy(m)
    broken["id"] = "Bad Id"
    expected = [{"path": e.path, "message": e.message}
                for e in validate_manifest(broken, catalog=CLASSES)]
    assert out["errors"] == expected
    assert m == before


@pytest.mark.parametrize("op,word", [
    ({"op": "move", "from": "/id", "path": "/name"}, "not supported"),
    ({"op": "copy", "from": "/id", "path": "/name"}, "not supported"),
    ({"op": "test", "path": "/id", "value": "x"}, "not supported"),
    ({"op": "zap", "path": "/id"}, "`op` must be"),
    ({"op": "replace", "path": "id", "value": "x"}, "must start with"),
    ({"op": "replace", "path": "/nope", "value": "x"}, "does not exist"),
    ({"op": "add", "path": "/objects/9", "value": {}}, "past the end"),
])
async def test_patch_refuses_a_bad_op_in_one_sentence(catalog, op, word) -> None:
    m = await _valid()
    before = copy.deepcopy(m)
    out = await st.studio_patch(m, [op])
    assert out["ok"] is False and "manifest" not in out and out["diff"] == []
    assert len(out["errors"]) == 1 and word in out["errors"][0]["message"]
    assert m == before
    assert _Fake.calls == [], "a refused patch makes no catalog call"


async def test_patch_ops_must_be_a_list(catalog) -> None:
    out = await st.studio_patch(await _valid(), {"op": "add"})
    assert out["ok"] is False and "list" in out["errors"][0]["message"]


# -- the catalog: unknown and class X ----------------------------------------


async def test_an_unknown_action_id_is_refused_when_the_catalog_is_passed(catalog) -> None:
    m = await _valid()
    out = await st.studio_patch(m, _with_action("tasks.nope"))
    assert out["ok"] is False
    assert any("actions[0].uses" in e["path"] for e in out["errors"]), out["errors"]
    ok = await st.studio_patch(m, _with_action("tasks.create"))
    assert ok["ok"], ok


async def test_a_class_x_action_is_refused_when_the_catalog_says_x(monkeypatch) -> None:
    rows = [*ROWS, {"id": "tasks.purge", "title": "Purge", "class": "X", "source": "manifest",
                    "connection": None}]
    _serve(monkeypatch, body={"actions": rows})
    m = await _valid()
    out = await st.studio_patch(m, _with_action("tasks.purge"))
    assert out["ok"] is False and out["errors"], out


# -- a catalog failure is reported, not skipped ------------------------------


@pytest.mark.parametrize("kw,word", [
    ({"status": 404, "body": {"detail": "Not found"}}, "not open"),
    ({"status": 403, "body": {}}, "not open to you"),
    ({"status": 500, "body": {}}, "status 500"),
    ({"status": 200, "body": {"actions": "x"}}, "shape"),
    ({"error": httpx.ConnectError("boom")}, "ConnectError"),
])
@pytest.mark.parametrize("tool", ["validate", "patch"])
async def test_a_failed_catalog_read_is_reported_and_the_manifest_is_not_valid(
    monkeypatch, kw, word, tool,
) -> None:
    _serve(monkeypatch, **kw)
    m = await _valid()
    if tool == "validate":
        out = await st.studio_validate(m)
    else:
        out = await st.studio_patch(m, [{"op": "replace", "path": "/name", "value": "N"}])
    assert out["ok"] is False and "manifest" not in out
    assert out["catalog_checked"] is False
    first = out["errors"][0]["message"]
    assert word in first and "did not run" in first


async def test_a_failed_catalog_read_still_returns_structural_errors(monkeypatch) -> None:
    _serve(monkeypatch, status=500, body={})
    m = await _valid()
    m["id"] = "Bad Id"
    out = await st.studio_validate(m)
    structural = [{"path": e.path, "message": e.message} for e in validate_manifest(m)]
    assert structural and out["errors"][1:] == structural


async def test_with_no_member_the_tools_refuse_and_call_nothing(monkeypatch) -> None:
    _serve(monkeypatch)
    monkeypatch.setattr(st, "_current_user_email", lambda: "")
    out = await st.studio_validate(await _valid())
    assert out["ok"] is False and "no member" in out["errors"][0]["message"]
    assert (await st.studio_list_actions())["ok"] is False
    monkeypatch.setattr(st, "_current_user_email", lambda: "room:abc")
    assert "shared room" in (await st.studio_list_actions())["errors"][0]["message"]
    assert _Fake.calls == []


# -- the cap -----------------------------------------------------------------


def _big() -> dict:
    return {"studio": 1, "id": "big", "name": "Big", "purpose": "p", "workflows": [],
            "objects": [{"name": "o", "label": "x" * 5000, "fields": []} for _ in range(6)]}


def test_the_cap_is_24000_characters() -> None:
    assert st.MAX_MANIFEST_CHARS == 24_000


@pytest.mark.parametrize("tool", ["validate", "patch", "outline"])
async def test_a_manifest_over_the_cap_is_refused_with_the_outline_and_size(catalog, tool) -> None:
    big = _big()
    assert st._size(big) > st.MAX_MANIFEST_CHARS
    if tool == "validate":
        out = await st.studio_validate(big)
    elif tool == "outline":
        out = await st.studio_outline(big)
    else:
        out = await st.studio_patch(big, [{"op": "add", "path": "/x", "value": 1}])
    assert out["ok"] is False and "manifest" not in out
    assert len(out["errors"]) == 1 and "24000" in out["errors"][0]["message"]
    assert out["size"] == st._size(big) and out["limit"] == 24_000
    assert "Objects (6)" in out["outline"]
    assert _Fake.calls == []


async def test_a_patch_that_grows_a_manifest_over_the_cap_is_refused(catalog) -> None:
    m = await _valid()
    out = await st.studio_patch(m, [{"op": "add", "path": "/purpose", "value": "y" * 19_000},
                                    {"op": "add", "path": "/name", "value": "z" * 19_000}])
    assert out["ok"] is False and "24000" in out["errors"][0]["message"]


# -- outline -----------------------------------------------------------------


async def test_outline_is_deterministic_with_counts_and_names(catalog) -> None:
    m = await _valid()
    m = (await st.studio_patch(m, [
        {"op": "add", "path": "/objects/-", "value": {"name": "order", "fields": [
            {"key": "a", "type": "text"}, {"key": "b", "type": "text"}]}},
        {"op": "add", "path": "/connections/-", "value": "slack"},
    ]))["manifest"]
    text = st.outline_text(m)
    assert text == st.outline_text(copy.deepcopy(m))
    assert "Objects (1): order (2 fields)" in text
    assert "Connections (1): slack" in text and "Skill: none" in text
    assert "Views (0): none" in text and "Workflows (0): none" in text


def test_outline_text_never_raises() -> None:
    junk = {"objects": 3, "views": [1, None], "skill": 4, "connections": "x"}
    for value in (None, 5, [], junk):
        assert isinstance(st.outline_text(value), str)


async def test_outline_tool_shape_and_validity() -> None:
    out = await st.studio_outline(await _valid())
    assert out["ok"] and out["manifest"] and out["outline"].startswith("App ")
    bad = await st.studio_outline({"studio": 2})
    assert bad["ok"] is False and "manifest" not in bad and bad["errors"]


async def test_outline_of_an_oversized_manifest_gives_the_size() -> None:
    """Mutation: drop the cap check from ``studio_outline`` -> the manifest
    comes back -> red."""
    big = _big()
    out = await st.studio_outline(big)
    assert out["outline"].startswith("App big") and out["size"] == st._size(big)
    assert out["ok"] is False and "manifest" not in out


def _valid_huge() -> dict:
    """A VALID manifest of more than 200,000 characters.

    STU-C1 caps a label at 120 characters, so the size comes from the most
    objects (50) with the most fields (60) each. It stays under A2's 256 KiB.
    """
    return {"studio": 1, "id": "huge", "name": "Huge", "purpose": "p", "connections": [],
            "actions": [], "views": [], "workflows": [],
            "objects": [{"name": f"o{i}", "label": f"Object {i}",
                         "fields": [{"key": f"f{j}", "type": "text", "label": "x" * 32}
                                    for j in range(60)]}
                        for i in range(50)]}


async def test_a_valid_manifest_over_the_cap_never_comes_back_whole(catalog) -> None:
    huge = _valid_huge()
    assert validate_manifest(huge) == [], "non-vacuity: the manifest is valid"
    assert st._size(huge) > 200_000
    for out in (await st.studio_outline(huge), await st.studio_validate(huge)):
        assert out["ok"] is False and "manifest" not in out
        assert st._size(out) <= st.MAX_MANIFEST_CHARS


async def test_the_errors_are_cut_with_a_count_of_the_rest(catalog) -> None:
    """400 bad action ids gave 49,000 characters. Mutation: drop the cut in
    ``_result`` -> 400 errors -> red."""
    m = await _valid()
    m["actions"] = [{"id": f"Bad Id {i}", "uses": "tasks.create"} for i in range(400)]
    assert st._size(m) < st.MAX_MANIFEST_CHARS, "non-vacuity: under the cap"
    every = validate_manifest(m, catalog=CLASSES)
    assert len(every) > st.MAX_ERRORS
    out = await st.studio_validate(m)
    assert len(out["errors"]) == st.MAX_ERRORS + 1
    assert out["errors"][: st.MAX_ERRORS] == [
        {"path": e.path, "message": e.message} for e in every[: st.MAX_ERRORS]]
    rest = len(every) - st.MAX_ERRORS
    assert out["errors"][-1]["message"].startswith(f"{rest} more errors are not shown.")
    assert st._size(out) <= st.MAX_MANIFEST_CHARS


async def test_one_long_connection_is_cut_in_the_outline() -> None:
    """One connection of 60,000 characters gave a refusal of 60,000.
    Mutation: drop the name cut in ``outline_text`` -> red."""
    m = {"studio": 1, "id": "c", "name": "C", "purpose": "p", "connections": ["z" * 60_000]}
    text = st.outline_text(m)
    assert "Connections (1): " in text
    assert all(len(line) <= st.MAX_OUTLINE_LINE_CHARS for line in text.split("\n"))
    out = await st.studio_outline(m)
    assert out["ok"] is False and "manifest" not in out
    assert st._size(out) <= st.MAX_MANIFEST_CHARS


def test_a_long_outline_line_counts_the_names_it_cannot_hold() -> None:
    m = {"id": "a", "name": "A", "connections": [f"conn{i}" for i in range(5_000)]}
    line = next(x for x in st.outline_text(m).split("\n") if x.startswith("Connections"))
    assert line.startswith("Connections (5000): conn0, conn1")
    assert len(line) <= st.MAX_OUTLINE_LINE_CHARS
    shown, rest = line.split(": ", 1)[1].rsplit(", and ", 1)
    assert rest.endswith(" more")
    assert len(shown.split(", ")) + int(rest.split()[0]) == 5_000


async def test_a_long_diff_is_cut_with_a_count_of_the_rest(catalog) -> None:
    m = await _valid()
    ops = [{"op": "replace", "path": "/name", "value": f"N{i}"} for i in range(150)]
    out = await st.studio_patch(m, ops)
    assert out["ok"], out
    assert out["diff"][: st.MAX_DIFF_LINES] == ["replace /name"] * st.MAX_DIFF_LINES
    assert out["diff"][-1] == f"and {150 - st.MAX_DIFF_LINES} more ops"
    assert len(out["diff"]) == st.MAX_DIFF_LINES + 1


def test_a_shown_error_is_cut_and_a_short_one_stays_verbatim() -> None:
    """No validator message is near the limits today, so a direct case holds
    the cut. Mutation: drop the path or the message cut -> red."""
    short = {"path": "$.actions[0].id", "message": "The id `Bad Id` is not valid."}
    long_ = {"path": "$." + "k" * 5_000, "message": "m" * 5_000 + "\x00\n"}
    shown = st._shown_errors([short, long_])
    assert shown[0] == short
    assert len(shown[1]["path"]) == st.MAX_ERROR_PATH_CHARS
    assert len(shown[1]["message"]) == st.MAX_ERROR_MESSAGE_CHARS
    assert shown[1]["message"].endswith("...")
    assert all(ch >= " " for ch in st._clip("a\x00b\x1fc\nd", 50)), "no control character"


def _notes(out: dict) -> dict:
    return {k: v for k, v in out.items() if k != "manifest"}


#: The text that costs the most JSON: each quote takes 2 characters.
_Q = '"'


def _worst_manifest() -> dict:
    """Under the cap, with long quoted names, a long key and many bad ids."""
    return {"studio": 1, "id": _Q * 300, "name": _Q * 300, "purpose": "p",
            _Q * 2000: 1,
            "connections": [_Q * 1000] * 2,
            "objects": [{"name": _Q * 300, "fields": []}] * 3,
            "actions": [{"id": _Q * 40, "uses": _Q * 40} for _ in range(50)],
            "views": [], "workflows": []}


async def _every_return(monkeypatch) -> list[tuple[str, dict]]:
    """A result of each return path of each tool, with the inputs that once
    grew a result past the cap, and with the text that costs the most JSON."""
    _serve(monkeypatch)
    m = await _valid()
    worst = _worst_manifest()
    assert st._size(worst) <= st.MAX_MANIFEST_CHARS, "non-vacuity: the worst is under the cap"
    long_path = "/" + _Q * 30_000
    outs: list[tuple[str, dict]] = [
        ("draft ok", await st.studio_draft("Track " + "x" * 50_000 + " orders " + "w " * 10_000)),
        ("draft quotes", await st.studio_draft(_Q * 30_000 + " " + "w " * 10_000)),
        ("draft long word", await st.studio_draft("x" * 100_000)),
        ("draft empty", await st.studio_draft("")),
        ("validate worst", await st.studio_validate(worst)),
        ("validate huge", await st.studio_validate(_valid_huge())),
        ("validate bad json", await st.studio_validate("{" + _Q * 50_000)),
        ("outline worst", await st.studio_outline(worst)),
        ("outline huge", await st.studio_outline({"connections": [_Q * 60_000] * 50,
                                                  "id": _Q * 9_000, "name": _Q * 9_000})),
        ("outline ok", await st.studio_outline(m)),
        ("outline many names", await st.studio_outline(
            {"id": "n", "name": "N", "connections": [f"{_Q * 70}{i}" for i in range(2_000)]})),
        ("validate many errors", await st.studio_validate(
            {**m, "actions": [{"id": f"Bad Id {i}", "uses": "tasks.create"} for i in range(400)]})),
        ("patch bad path", await st.studio_patch(m, [{"op": "replace", "path": long_path,
                                                      "value": 1}])),
        ("patch many ops", await st.studio_patch(m, [
            {"op": "add", "path": "/" + _Q * 90, "value": 1}] * 200)),
        ("patch worst", await st.studio_patch(worst, [{"op": "replace", "path": "/purpose",
                                                        "value": "q"}])),
        ("patch big index", await st.studio_patch(
            m, [{"op": "add", "path": "/objects/" + "9" * 5000, "value": {}}])),
    ]
    _serve(monkeypatch, status=500, body={})
    outs.append(("validate worst, no catalog", await st.studio_validate(worst)))
    rows = [{"id": f"a.op{i}", "title": _Q * 20_000, "class": _Q * 5_000,
             "source": _Q * 5_000, "connection": _Q * 5_000} for i in range(80)]
    rows.append({"id": "x" * 5_000, "title": "a long id", "class": "A"})
    _serve(monkeypatch, body={"actions": rows})
    outs.append(("list actions", await st.studio_list_actions()))
    _serve(monkeypatch, status=404, body={})
    outs.append(("list actions refused", await st.studio_list_actions()))
    return outs


async def test_every_return_path_of_every_tool_stays_under_the_cap(monkeypatch) -> None:
    """The notes of a result (each key but the manifest) stay under the cap,
    and a manifest comes back only under the cap. Mutation: drop any one cut
    (errors, diff, outline names, outline lines, row texts) -> red."""
    outs = await _every_return(monkeypatch)
    for label, out in outs:
        assert {"ok", "errors", "diff"} <= set(out), label
        assert st._size(_notes(out)) <= st.MAX_MANIFEST_CHARS, (label, st._size(_notes(out)))
        if "manifest" in out:
            assert out["ok"] is True, label
            assert st._size(out["manifest"]) <= st.MAX_MANIFEST_CHARS, label
    by = dict(outs)
    assert len(by["validate worst"]["errors"]) == st.MAX_ERRORS + 1, "non-vacuity: the cut ran"
    assert by["outline worst"]["errors"], "non-vacuity: the outline holds errors"
    assert by["list actions"]["total"] == 80, "the row with a long id is left out"
    assert all(len(r["title"]) <= st.MAX_ACTION_TITLE_CHARS for r in by["list actions"]["actions"])
    assert "past the end" in by["patch big index"]["errors"][0]["message"]
    assert by["draft ok"]["ok"] and len(by["draft ok"]["manifest"]["name"]) <= st.MAX_NAME_CHARS


# -- list actions ------------------------------------------------------------


async def test_list_actions_filters_by_id_and_title_and_hides_the_schema(catalog) -> None:
    out = await st.studio_list_actions("TASK")
    assert out["ok"] and [r["id"] for r in out["actions"]] == ["tasks.create", "tasks.list"]
    assert all("input_schema" not in r for r in out["actions"])
    assert [r["id"] for r in (await st.studio_list_actions("slack"))["actions"]] == [
        "integration:slack.post"]
    assert [r["id"] for r in (await st.studio_list_actions("post to"))["actions"]] == [
        "integration:slack.post"]
    assert (await st.studio_list_actions("zzz"))["actions"] == []
    assert out["total"] == 2 and out["shown"] == 2


async def test_list_actions_is_capped(monkeypatch) -> None:
    rows = [{"id": f"a.op{i}", "title": "t", "class": "A"} for i in range(80)]
    _serve(monkeypatch, body={"actions": rows})
    out = await st.studio_list_actions()
    assert out["shown"] == st.MAX_ACTION_ROWS and out["total"] == 80
    assert "offset=30" in out["page"] and "80" in out["page"]


async def test_list_actions_reaches_every_row_by_offset(monkeypatch) -> None:
    rows = [{"id": f"a.op{i}", "title": "t", "class": "A"} for i in range(80)]
    _serve(monkeypatch, body={"actions": rows})
    ids = []
    for offset in (0, 30, 60):
        out = await st.studio_list_actions(offset=offset)
        ids += [r["id"] for r in out["actions"]]
    assert ids == [r["id"] for r in rows]
    assert out["shown"] == 20 and out["total"] == 80
    past = await st.studio_list_actions(offset=200)
    assert past["actions"] == [] and past["page"]


async def test_list_actions_reports_a_failed_read(monkeypatch) -> None:
    _serve(monkeypatch, status=404, body={"detail": "Not found"})
    out = await st.studio_list_actions()
    assert out["ok"] is False and "not open" in out["errors"][0]["message"]


# -- R5 and the pack surface -------------------------------------------------


def test_no_tool_takes_an_org_or_a_member_argument() -> None:
    identity = {"organization_id", "org_id", "organization", "org", "tenant", "tenant_id",
                "member", "member_email", "user", "user_email", "email", "actor", "as_user"}
    assert len(st.STUDIO_TOOLS) == 8
    for fn in st.STUDIO_TOOLS:
        params = set(inspect.signature(fn).parameters)
        assert not params & identity, (fn.__name__, params)
        assert inspect.iscoroutinefunction(fn)


def test_every_tool_is_annotated_read_only_and_closed_world() -> None:
    for fn in st.STUDIO_TOOLS:
        hints = fn.__tool_risk__
        assert hints["read_only"] is True and hints["open_world"] is False, fn.__name__


# -- the fence: no studio module runs code -----------------------------------

_STUDIO_MODULES = (
    "packages/acb_skills/acb_skills/studio_tools.py",
    "packages/acb_common/acb_common/studio_patch.py",
    "apps/agents/agent-studio-builder/agents.py",
)
_BANNED_CALLS = {"exec", "eval", "compile", "__import__", "system", "popen", "run_command",
                 "run_skill_script", "Popen", "check_output", "check_call"}
_BANNED_MODULES = {"subprocess", "multiprocessing", "ctypes", "pty"}


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in _BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _BANNED_MODULES:
                found.append(node.module or "")
            found += [a.name for a in node.names if a.name in _BANNED_CALLS]
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name in _BANNED_CALLS:
                found.append(name)
    return found


@pytest.mark.parametrize("rel", _STUDIO_MODULES)
def test_no_studio_module_imports_or_calls_a_code_runner(rel) -> None:
    assert _violations((REPO / rel).read_text(encoding="utf-8")) == []


def test_the_fence_catches_a_code_runner() -> None:
    """Non-vacuity: the same walk flags ``eval``, ``subprocess`` and ``run_command``."""
    bad = "import subprocess\neval('1')\nfrom x import run_command\n"
    assert sorted(_violations(bad)) == ["eval", "run_command", "subprocess"]
