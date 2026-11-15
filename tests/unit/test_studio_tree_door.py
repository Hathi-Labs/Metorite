"""WS-STU STU-D18-2a: the door op ``object:<name>.tree``, the hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` section 14, row D18-2a. Code:
``gateway/routes/studio/invoke.py`` (``_tree_input``, ``_run_object_tree``
and ``_tree_answer``) over ``gateway/studio/objects_store.read_tree``. The R8
half is ``test_studio_tree_door_r8.py``.

**The rules (R7).**

* The body check runs before any read. A key outside ``TREE_INPUT_KEYS``, a
  ``root_id`` that is not a UUID, and a ``depth`` or a ``limit`` that is not an
  ``int`` in its range are 422 ``query_value_invalid``. The door never clamps.
* ``read_tree`` takes a REQUIRED ``viewer``, and the door passes the viewer of
  the session. ``test_studio_row_rules.py`` holds both for each caller.
* A ``root_id`` that the viewer does not read is "not found", never an empty tree.
* A viewer of an install reads a tree only when the installed version declares
  the hierarchy and its parent field, and only the values of installed fields.
* The answer is ``{nodes, truncated}``, and each node is ``{record, level,
  parent_id}``. No count of children and no path leave.

**The mutations (R7).** Each was run on 2026-11-14, and each restored with git.

* T1. ``invoke._tree_input``: drop ``type(value) is not int``.
  ``test_a_hostile_depth_or_limit_is_refused_before_any_read`` goes red (bool,
  float).
* T2. ``invoke._run_object_tree``: drop the ``root_id`` branch that sets
  ``missing``. ``test_a_root_that_the_viewer_does_not_read_is_not_found`` goes red.
* T3. ``invoke._installed_tree``: return True. ``test_a_viewer_of_an_install_with_no_installed_hierarchy_reads_nothing``
  goes red.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_query
from acb_common.studio_access import Viewer
from acb_common.studio_fields import parse_field_spec
from acb_common.studio_manifest import OBJECT_OP_CLASSES, parse_uses
from acb_common.studio_rules import stored_rules
from gateway.package_host import viewer as _viewer
from gateway.routes.studio import invoke
from gateway.studio import objects_store

OID = "00000000-0000-0000-0000-0000000000a1"
PID = "00000000-0000-0000-0000-0000000000b1"
RID = "00000000-0000-0000-0000-0000000000c1"
NOW = datetime(2026, 11, 14, tzinfo=UTC)
TREE_RULE = {"name": "tree", "kind": "hierarchy", "field": "parent"}
FIELDS = [{"key": "name", "type": "text"},
          {"key": "parent", "type": "relation", "to": "site", "hot": True},
          {"key": "secret", "type": "text"}]
VIEWER = Viewer("ana@x.test")


def _obj(rules: list[dict[str, Any]] | None = None) -> objects_store.StudioObject:
    return objects_store.StudioObject(
        id=OID, package_id=PID, name="site", label=None, created_by="ana@x.test",
        created_at=NOW, updated_at=NOW, fields=tuple(parse_field_spec(f) for f in FIELDS),
        constraints=stored_rules([TREE_RULE] if rules is None else rules))


def _record(rid: str = RID, **data: Any) -> objects_store.Record:
    return objects_store.Record(id=rid, object_id=OID, data=data, created_by="ana@x.test",
                                updated_by="ana@x.test", created_at=NOW, updated_at=NOW)


def _page(*nodes: objects_store.TreeNode, truncated: bool = False) -> objects_store.TreePage:
    return objects_store.TreePage(nodes=tuple(nodes), truncated=truncated)


class _Store:
    """The two store calls of the executor, and what they were called with."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, page: Any = None,
                 obj: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.page = _page() if page is None else page
        self.obj = _obj() if obj is None else obj

        async def _get(_db: Any, name: str, *, package_id: str) -> Any:
            return self.obj if name == "site" else None

        async def _read(_db: Any, **kw: Any) -> Any:
            self.calls.append(kw)
            return self.page

        monkeypatch.setattr(objects_store, "get_object_by_name", _get)
        monkeypatch.setattr(objects_store, "read_tree", _read)


def _run(package: Any, raw: bytes, viewer: Viewer = VIEWER) -> invoke._Outcome:
    out = invoke._Outcome("tree_of_sites")
    ref = parse_uses("object:site.tree")
    asyncio.run(invoke._run_object_tree(SimpleNamespace(), package, ref, raw, out, viewer))
    return out


_OWNER = SimpleNamespace(id=PID)


# -- the tables ---------------------------------------------------------------


def test_the_tree_is_a_class_a_read_of_the_door() -> None:
    assert OBJECT_OP_CLASSES["tree"] == "A"
    assert ("object", "tree") in invoke.OPEN_EXECUTORS
    assert invoke._READS["tree"] is invoke._run_object_tree
    assert {"root_id", "depth", "limit"} == invoke.TREE_INPUT_KEYS
    assert ("object", "tree") not in invoke.DIRECT_EXECUTORS


def test_read_tree_takes_a_required_viewer() -> None:
    """A call with no viewer fails before any statement."""
    param = inspect.signature(objects_store.read_tree).parameters["viewer"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        objects_store.read_tree(SimpleNamespace(), package_id=PID, object_id=OID)  # type: ignore[call-arg]

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no statement runs for a bad viewer")

    with pytest.raises(objects_store.ObjectStoreError):
        asyncio.run(objects_store.read_tree(SimpleNamespace(execute=_boom), package_id=PID,
                                            object_id=OID, viewer="ana@x.test"))


# -- the body ------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (b"", {}),
    (b"{}", {}),
    (b'{"root_id": null}', {}),
    (b'{"depth": 1, "limit": 1}', {"depth": 1, "limit": 1}),
    (b'{"depth": 20, "limit": 2000}', {"depth": 20, "limit": 2000}),
    (f'{{"root_id": "{RID.upper()}"}}'.encode(), {"root_id": RID}),
])
def test_a_good_body_is_read_as_is(raw: bytes, expected: dict[str, Any]) -> None:
    assert invoke._tree_input(raw) == expected


_HOSTILE = [0, -1, 10**30, -(10**30), 1.0, 1.5, "3", True, False, None, [], {}]


@pytest.mark.parametrize("key,value", [
    *[("depth", v) for v in [*_HOSTILE, 21]],
    *[("limit", v) for v in [*_HOSTILE, 2001]],
])
def test_a_hostile_depth_or_limit_is_refused_before_any_read(
        monkeypatch: pytest.MonkeyPatch, key: str, value: Any) -> None:
    store = _Store(monkeypatch)
    out = _run(_OWNER, json.dumps({key: value}).encode())
    assert out.query_error is not None
    assert out.query_error.code == studio_query.VALUE_INVALID
    assert out.query_error.field == key
    assert store.calls == [] and out.tree is None


@pytest.mark.parametrize("raw", [
    b'{"root_id": 7}', b'{"root_id": "not-a-uuid"}', b'{"root_id": []}',
    b'{"root_id": true}', b'{"filter": []}', b'{"organization_id": "x"}',
    b"[]", b"not json", b'"text"', b'{"depth": Infinity}', b'{"limit": NaN}',
])
def test_a_bad_body_is_422_and_reads_nothing(monkeypatch: pytest.MonkeyPatch,
                                             raw: bytes) -> None:
    store = _Store(monkeypatch)
    out = _run(_OWNER, raw)
    assert out.query_error is not None and out.query_error.code == studio_query.VALUE_INVALID
    assert store.calls == [] and out.tree is None


# -- the read ------------------------------------------------------------------


def test_the_door_passes_the_viewer_and_the_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(monkeypatch)
    out = _run(_OWNER, b"")
    assert out.tree is not None and out.tree.nodes == ()
    assert store.calls == [{"package_id": PID, "object_id": OID, "viewer": VIEWER,
                            "root_id": None, "depth": 20, "limit": 2000}]


def test_a_root_that_the_viewer_does_not_read_is_not_found(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A root of another org, of another member's rows, or of no record: the
    store gives no node, and the door answers 404, never an empty tree."""
    _Store(monkeypatch, page=_page())
    out = _run(_OWNER, f'{{"root_id": "{RID}"}}'.encode())
    assert out.missing and out.tree is None
    assert invoke._plain_outcome(out) == ("not_found", None)
    with pytest.raises(invoke.HTTPException) as caught:
        invoke._answer(out)
    assert caught.value.status_code == 404 and caught.value.detail == invoke.NOT_FOUND


def test_an_object_that_the_package_lacks_is_object_missing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(monkeypatch)
    out = invoke._Outcome("x")
    asyncio.run(invoke._run_object_tree(SimpleNamespace(), _OWNER, parse_uses("object:lot.tree"),
                                        b"", out, VIEWER))
    assert out.object_missing and store.calls == []


def test_an_object_with_no_hierarchy_is_a_plain_422(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_tree(_db: Any, **_kw: Any) -> Any:
        raise objects_store.NoTree("Object `site` holds no hierarchy rule.")

    _Store(monkeypatch, obj=_obj(rules=[]))
    monkeypatch.setattr(objects_store, "read_tree", _no_tree)
    out = _run(_OWNER, b"")
    assert out.query_error is not None
    assert out.query_error.code == studio_query.OP_NOT_ALLOWED
    assert "no hierarchy" in out.query_error.message
    with pytest.raises(invoke.HTTPException) as caught:
        invoke._answer(out)
    assert caught.value.status_code == 422


def test_a_viewer_of_an_install_with_no_installed_hierarchy_reads_nothing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The stored object has a hierarchy, and the installed version does not
    declare it: the viewer gets the refusal of no tree, and no read runs."""
    store = _Store(monkeypatch)
    manifest = {"objects": [{"name": "site", "fields": FIELDS}]}
    app = _viewer.InstalledApp(id=PID, version=2, manifest=manifest)
    out = _run(app, b"")
    assert out.query_error is not None and out.query_error.code == studio_query.OP_NOT_ALLOWED
    assert store.calls == []


def test_a_viewer_of_an_install_reads_the_installed_fields_only(
        monkeypatch: pytest.MonkeyPatch) -> None:
    node = objects_store.TreeNode(record=_record(name="Plant", secret="x"), level=1,
                                  parent_id=None)
    _Store(monkeypatch, page=_page(node))
    installed = [f for f in FIELDS if f["key"] != "secret"]
    manifest = {"objects": [{"name": "site", "fields": installed, "rules": [TREE_RULE]}]}
    out = _run(_viewer.InstalledApp(id=PID, version=2, manifest=manifest), b"")
    assert out.tree is not None
    rec = out.tree.nodes[0].record
    assert rec.data == {"name": "Plant"} and rec.created_by == "" and rec.updated_by == ""


# -- the answer ----------------------------------------------------------------


def test_the_answer_holds_the_nodes_and_truncated_only() -> None:
    root = objects_store.TreeNode(record=_record(name="Plant"), level=1, parent_id=None)
    child = objects_store.TreeNode(record=_record("00000000-0000-0000-0000-0000000000c2"),
                                   level=2, parent_id=RID)
    out = invoke._Outcome("x")
    out.tree = _page(root, child, truncated=True)
    got = invoke._answer(out)
    assert set(got) == {"nodes", "truncated"} and got["truncated"] is True
    assert [set(n) for n in got["nodes"]] == [{"record", "level", "parent_id"}] * 2
    assert [(n["level"], n["parent_id"]) for n in got["nodes"]] == [(1, None), (2, RID)]
    assert got["nodes"][0]["record"]["data"] == {"name": "Plant"}
