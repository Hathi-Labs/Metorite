"""RFC 6902 and RFC 6901 cases for ``acb_common.studio_patch`` (STU-G1).

Only ``add``, ``replace`` and ``remove`` exist. A refusal raises ``PatchError``
with one sentence, and the document that the caller passed in is not changed.
"""
from __future__ import annotations

import copy

import pytest
from acb_common.studio_patch import (
    MAX_INDEX_DIGITS,
    MAX_OPS,
    PatchError,
    apply_patch,
    parse_pointer,
)


def _doc() -> dict:
    return {"a": {"b": [1, 2, 3]}, "s": "x", "k/y": 1, "t~z": 2}


# -- RFC 6901 ----------------------------------------------------------------


def test_pointer_tokens_and_escapes() -> None:
    assert parse_pointer("") == []
    assert parse_pointer("/a/b/0") == ["a", "b", "0"]
    assert parse_pointer("/k~1y") == ["k/y"]
    assert parse_pointer("/t~0z") == ["t~z"]
    assert parse_pointer("/") == [""]


@pytest.mark.parametrize("bad", ["a/b", "~1", "/a~2b", "/a~", 5, None, ["a"]])
def test_a_bad_pointer_is_refused(bad) -> None:
    with pytest.raises(PatchError):
        parse_pointer(bad)


def test_escaped_keys_are_reached() -> None:
    out = apply_patch(_doc(), [{"op": "replace", "path": "/k~1y", "value": 9},
                               {"op": "replace", "path": "/t~0z", "value": 8}])
    assert out["k/y"] == 9 and out["t~z"] == 8


# -- add ---------------------------------------------------------------------


def test_add_a_member_and_replace_an_existing_one() -> None:
    out = apply_patch(_doc(), [{"op": "add", "path": "/n", "value": 1},
                               {"op": "add", "path": "/s", "value": "y"}])
    assert out["n"] == 1 and out["s"] == "y"


def test_add_inserts_in_a_list_and_appends_with_dash() -> None:
    out = apply_patch(_doc(), [{"op": "add", "path": "/a/b/1", "value": 9},
                               {"op": "add", "path": "/a/b/-", "value": 7},
                               {"op": "add", "path": "/a/b/5", "value": 6}])
    assert out["a"]["b"] == [1, 9, 2, 3, 7, 6]


def test_add_past_the_end_is_refused() -> None:
    with pytest.raises(PatchError, match="past the end"):
        apply_patch(_doc(), [{"op": "add", "path": "/a/b/4", "value": 1}])


def test_add_to_a_missing_parent_is_refused() -> None:
    with pytest.raises(PatchError, match="does not exist"):
        apply_patch(_doc(), [{"op": "add", "path": "/nope/x", "value": 1}])


def test_add_at_the_root_replaces_the_document() -> None:
    assert apply_patch(_doc(), [{"op": "add", "path": "", "value": {"z": 1}}]) == {"z": 1}


def test_add_needs_a_value() -> None:
    with pytest.raises(PatchError, match="needs a `value`"):
        apply_patch(_doc(), [{"op": "add", "path": "/n"}])


def test_a_null_value_is_a_value() -> None:
    assert apply_patch(_doc(), [{"op": "add", "path": "/n", "value": None}])["n"] is None


# -- replace and remove ------------------------------------------------------


def test_replace_needs_an_existing_target() -> None:
    out = apply_patch(_doc(), [{"op": "replace", "path": "/a/b/0", "value": 0}])
    assert out["a"]["b"][0] == 0
    with pytest.raises(PatchError, match="does not exist"):
        apply_patch(_doc(), [{"op": "replace", "path": "/n", "value": 1}])
    with pytest.raises(PatchError, match="past the end"):
        apply_patch(_doc(), [{"op": "replace", "path": "/a/b/3", "value": 1}])


def test_remove_a_member_and_a_list_item() -> None:
    out = apply_patch(_doc(), [{"op": "remove", "path": "/s"},
                               {"op": "remove", "path": "/a/b/0"}])
    assert "s" not in out and out["a"]["b"] == [2, 3]


def test_remove_a_missing_target_or_the_root_is_refused() -> None:
    with pytest.raises(PatchError, match="does not exist"):
        apply_patch(_doc(), [{"op": "remove", "path": "/n"}])
    with pytest.raises(PatchError, match="whole document"):
        apply_patch(_doc(), [{"op": "remove", "path": ""}])


@pytest.mark.parametrize("kind", ["replace", "remove"])
def test_dash_only_works_for_add(kind) -> None:
    with pytest.raises(PatchError, match="only `add`"):
        apply_patch(_doc(), [{"op": kind, "path": "/a/b/-", "value": 1}])


@pytest.mark.parametrize("token", ["01", "-1", "x", "1.0", "+1", ""])
def test_a_bad_array_index_is_refused(token) -> None:
    with pytest.raises(PatchError, match="array index"):
        apply_patch(_doc(), [{"op": "add", "path": f"/a/b/{token}", "value": 1}])


@pytest.mark.parametrize("digits", [MAX_INDEX_DIGITS + 1, 4_301, 50_000])
@pytest.mark.parametrize("kind", ["add", "replace", "remove"])
def test_an_index_with_too_many_digits_is_past_the_end(kind, digits) -> None:
    """``int`` refuses a text of more than 4,300 digits with a ``ValueError``
    that is not a ``PatchError``. Mutation: drop the digit cap -> the
    ValueError escapes -> red."""
    op = {"op": kind, "path": "/a/b/" + "9" * digits, "value": 1}
    with pytest.raises(PatchError, match="past the end") as err:
        apply_patch(_doc(), [op])
    assert len(str(err.value)) < 200, "the sentence echoes a cut path"


def test_the_largest_index_that_fits_the_cap_still_reads() -> None:
    with pytest.raises(PatchError, match="past the end"):
        apply_patch(_doc(), [{"op": "add", "path": "/a/b/" + "9" * MAX_INDEX_DIGITS, "value": 1}])
    assert apply_patch(_doc(), [{"op": "add", "path": "/a/b/3", "value": 4}])["a"]["b"] == [1, 2, 3, 4]


@pytest.mark.parametrize("path", ["/nope/" + "x" * 5_000, "/a/b/" + "-x" * 3_000,
                                  "/s/" + "y" * 5_000])
def test_a_long_path_is_cut_in_the_sentence(path) -> None:
    with pytest.raises(PatchError) as err:
        apply_patch(_doc(), [{"op": "replace", "path": path, "value": 1}])
    assert len(str(err.value)) < 300


# -- the ops that do not exist -----------------------------------------------


@pytest.mark.parametrize("kind", ["move", "copy", "test"])
def test_move_copy_and_test_are_refused_plainly(kind) -> None:
    op = {"op": kind, "path": "/s", "from": "/a", "value": 1}
    with pytest.raises(PatchError, match=f"`{kind}` is not supported"):
        apply_patch(_doc(), [op])


@pytest.mark.parametrize("op", [{"op": "zap", "path": "/s"}, {"path": "/s"}, "add", 5, None,
                                {"op": "add"}, {"op": "add", "path": 3, "value": 1}])
def test_a_malformed_op_is_refused(op) -> None:
    with pytest.raises(PatchError):
        apply_patch(_doc(), [op])


def test_ops_must_be_a_list_within_the_cap() -> None:
    with pytest.raises(PatchError):
        apply_patch(_doc(), {"op": "add"})
    with pytest.raises(PatchError, match=str(MAX_OPS)):
        apply_patch(_doc(), [{"op": "add", "path": "/n", "value": 1}] * (MAX_OPS + 1))


# -- the input is never changed ----------------------------------------------


def test_the_input_is_unchanged_on_success_and_on_refusal() -> None:
    doc = _doc()
    before = copy.deepcopy(doc)
    out = apply_patch(doc, [{"op": "add", "path": "/a/b/-", "value": {"q": 1}}])
    assert doc == before and out is not doc
    with pytest.raises(PatchError):
        apply_patch(doc, [{"op": "add", "path": "/n", "value": 1},
                          {"op": "remove", "path": "/missing"}])
    assert doc == before


def test_a_value_is_copied_in() -> None:
    value = {"q": [1]}
    out = apply_patch({}, [{"op": "add", "path": "/v", "value": value}])
    value["q"].append(2)
    assert out["v"] == {"q": [1]}


def test_the_error_names_the_op_number() -> None:
    with pytest.raises(PatchError, match="Op 1:"):
        apply_patch(_doc(), [{"op": "add", "path": "/n", "value": 1},
                             {"op": "remove", "path": "/zz"}])
