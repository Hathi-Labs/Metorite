"""WS-STU C10a — the org caps of Studio records, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 C10a and §3.1. Code:
``acb_common/studio_fields.py`` (the two caps), ``gateway/studio/objects_store.py``
(``_org_usage``, ``_check_org_caps``, ``OrgCapReached`` and ``_run_steps``) and
``gateway/routes/studio/invoke.py`` (``_set_write_error``). The migration
that creates ``studio_org_usage`` (435 at build) holds the gauge and its three
triggers. ``test_studio_org_caps_r8.py`` holds the R8 half.

**The mutations (R7).** Commit before each, and restore with git after.
Each was run on 2026-11-13, and each went red.

* H1. ``_check_org_caps``: drop ``and records > records_were``.
  ``test_only_a_batch_that_raised_a_count_is_refused`` goes red.
* H2. ``_set_write_error``: move the ``OrgCapReached`` branch after the
  ``ObjectStoreError`` branch. ``test_the_door_branch_comes_before_the_store_error``
  and ``test_the_door_answers_422_org_cap_reached`` go red.
* H3. ``_ORG_USAGE_SQL``: drop the filter on the bound org.
  ``test_every_statement_on_the_gauge_is_bound_to_the_org`` goes red.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit import _r8_lists as r8_lists

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_INVOKE = _ROOT / "apps/services/gateway/gateway/routes/studio/invoke.py"

RECORDS_SENTENCE = (
    "This organization holds 1,000,000 records, the most it can hold, "
    "so delete records that you do not use.")
BYTES_SENTENCE = (
    "This organization holds 2 GiB of records, the most it can hold, "
    "so delete records that you do not use.")


def _migration() -> str:
    """The migration that creates the gauge, found by CONTENT (R1)."""
    hits = [p for p in sorted((_ROOT / "infra/postgres").glob("[0-9]*.sql"))
            if "CREATE TABLE IF NOT EXISTS studio_org_usage" in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0].read_text(encoding="utf-8")


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


# -- the caps and the error --------------------------------------------------------


def test_the_caps_are_one_million_records_and_two_gib() -> None:
    assert studio_fields.MAX_RECORDS_PER_ORG == 1_000_000
    assert studio_fields.MAX_RECORD_BYTES_PER_ORG == 2 * 1024**3
    # The store reads the same names, so a test can lower a cap in one place.
    assert store.MAX_RECORDS_PER_ORG == studio_fields.MAX_RECORDS_PER_ORG
    assert store.MAX_RECORD_BYTES_PER_ORG == studio_fields.MAX_RECORD_BYTES_PER_ORG


def test_the_error_is_a_budget_error_with_its_own_code() -> None:
    assert issubclass(store.OrgCapReached, store.BudgetExceeded)
    assert issubclass(store.OrgCapReached, store.ObjectStoreError)
    assert store.OrgCapReached.code == "org_cap_reached"
    assert invoke.ORG_CAP_REACHED == "org_cap_reached"


def _refusal(before: tuple[int, int], after: tuple[int, int]) -> str | None:
    batch = SimpleNamespace(index=7)
    try:
        store._check_org_caps(batch, before, after)  # type: ignore[arg-type]
    except store.OrgCapReached as exc:
        assert batch.index == 0, "the refusal names the whole batch"
        return str(exc)
    assert batch.index == 7
    return None


def test_the_two_sentences_name_the_cap_and_the_act() -> None:
    cap, size = store.MAX_RECORDS_PER_ORG, store.MAX_RECORD_BYTES_PER_ORG
    assert _refusal((cap, 0), (cap + 1, 10)) == RECORDS_SENTENCE
    assert _refusal((5, size), (5, size + 1)) == BYTES_SENTENCE
    for sentence in (RECORDS_SENTENCE, BYTES_SENTENCE):
        # Simplified Technical English: one sentence, at most 20 words, no semicolon.
        assert ";" not in sentence
        assert sentence.count(".") == 1 and sentence.endswith(".")
        assert len(sentence.split()) <= 20, sentence


def test_a_byte_cap_that_is_not_whole_gib_is_said_in_bytes(monkeypatch) -> None:
    monkeypatch.setattr(store, "MAX_RECORD_BYTES_PER_ORG", 1_500)
    assert _refusal((0, 1_500), (0, 1_501)) == (
        "This organization holds 1,500 bytes of records, the most it can hold, "
        "so delete records that you do not use.")


@pytest.mark.parametrize(("before", "after", "refused"), [
    # Under or at a cap: each batch passes.
    ((0, 0), (1, 100), False),
    ((9, 900), (10, 1_000), False),
    # Past the record cap, and this batch raised the count.
    ((10, 1_000), (11, 1_100), True),
    ((9, 900), (11, 1_100), True),
    # Past the record cap, and this batch did not raise the count: a delete,
    # an update, and a create with a delete in one batch.
    ((12, 1_200), (11, 1_100), False),
    ((12, 1_200), (12, 1_150), False),
    ((12, 1_200), (12, 1_200), False),
    # Past the byte cap, and this batch raised the bytes.
    ((5, 1_000), (5, 1_001), True),
    ((5, 900), (6, 1_100), True),
    # Past the byte cap, and this batch made the data smaller or kept it.
    ((5, 1_200), (5, 1_100), False),
    ((5, 1_200), (4, 1_200), False),
    # Past the byte cap with fewer records but more bytes: the bytes rule.
    ((5, 1_000), (4, 1_050), True),
])
def test_only_a_batch_that_raised_a_count_is_refused(
        monkeypatch, before: tuple[int, int], after: tuple[int, int], refused: bool) -> None:
    """The D108.5 idiom: past a cap, a delete and an update that makes the data
    smaller still pass, so the org can always get back under the cap."""
    monkeypatch.setattr(store, "MAX_RECORDS_PER_ORG", 10)
    monkeypatch.setattr(store, "MAX_RECORD_BYTES_PER_ORG", 1_000)
    assert (_refusal(before, after) is not None) is refused


# -- the batch core ----------------------------------------------------------------


def test_the_batch_reads_the_gauge_under_the_lock_and_checks_it_before_the_rules() -> None:
    src = ast.unparse(_functions(_STORE)["_run_steps"])
    order = ["_lock_org(", "_org_usage(", "_apply_write(", "_recompute_parents(",
             "_check_org_caps(", "_check_records(", "_check_keys("]
    places = [src.index(s) for s in order]
    assert places == sorted(places), order
    assert src.count("_org_usage(") == 2
    assert "_check_org_caps(batch, usage, await _org_usage(db))" in src


def test_the_gauge_read_is_one_row_and_no_count() -> None:
    """The batch reads one row of the gauge, never a count or a sum of the
    records of the org."""
    sql = store._ORG_USAGE_SQL
    assert sql.startswith("SELECT records, bytes FROM studio_org_usage ")
    assert "count(" not in sql.lower() and "sum(" not in sql.lower()
    assert "_ORG_USAGE_SQL" in ast.unparse(_functions(_STORE)["_org_usage"])


def test_every_statement_on_the_gauge_is_bound_to_the_org() -> None:
    """Each SQL constant of the store that names the gauge filters on the bound
    org. Python never writes the gauge: only the triggers do."""
    src = _STORE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    named: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        text = ast.get_source_segment(src, node) or ""
        if "studio_org_usage" not in text:
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        named.append(getattr(target, "id", "?"))
        assert "organization_id = {_BOUND_ORG}" in text, text
    assert named == ["_ORG_USAGE_SQL"], named
    writes = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+studio_org_usage\b", re.I)
    assert not writes.search(src)


def test_each_trigger_statement_keys_the_gauge_by_the_org_of_its_rows() -> None:
    """Each statement of the migration on the gauge names the org of the rows
    that it counts: the triggers group the transition rows by
    `organization_id`, and the backfill binds each org in turn."""
    sql = _migration()
    assert sql.count("GROUP BY n.organization_id") == 1  # the insert
    assert sql.count("GROUP BY x.organization_id") == 1  # the update
    assert sql.count("GROUP BY o.organization_id") == 1  # the delete
    assert sql.count("WHERE u.organization_id = d.organization_id") == 2
    assert "WHERE r.organization_id = org" in sql
    assert "PERFORM set_config('app.tenant_id', org::text, true)" in sql


def test_the_migration_has_force_rls_and_three_statement_triggers() -> None:
    sql = _migration()
    assert "ALTER TABLE studio_org_usage FORCE  ROW LEVEL SECURITY" in sql
    assert "DEFAULT current_setting('app.tenant_id', true)::uuid" in sql
    assert "REFERENCES organization (id) ON DELETE CASCADE" in sql
    assert sql.count("FOR EACH STATEMENT EXECUTE FUNCTION studio_org_usage_") == 3
    assert "LOCK TABLE studio_records IN SHARE MODE" in sql
    assert "SECURITY DEFINER" not in sql


def test_the_delete_trigger_never_inserts_a_row() -> None:
    """An org delete cascades to the records and the gauge in either order. A
    delete trigger that upserts would put the row back, and the org delete
    would then fail on the foreign key."""
    sql = _migration()
    body = sql.split("CREATE OR REPLACE FUNCTION studio_org_usage_delete()")[1]
    body = body.split("$fn$;")[0]
    assert "INSERT" not in body.upper()
    assert "UPDATE studio_org_usage" in body


# -- the door ----------------------------------------------------------------------


def test_the_door_branch_comes_before_the_store_error() -> None:
    src = ast.unparse(_functions(_INVOKE)["_set_write_error"])
    assert src.index("objects_store.StoreBusy") < src.index("objects_store.OrgCapReached")
    assert src.index("objects_store.OrgCapReached") < src.index("objects_store.ObjectStoreError")


def test_the_door_answers_422_org_cap_reached() -> None:
    out = invoke._Outcome(action="add")
    exc = store.OrgCapReached(RECORDS_SENTENCE)
    assert invoke._set_write_error(out, exc) is True
    assert out.fail is not None
    assert (out.fail.outcome, out.fail.reason) == ("refused", "org_cap_reached")
    assert out.fail.error.status_code == 422
    assert out.fail.error.detail == {
        "code": "org_cap_reached", "message": RECORDS_SENTENCE, "rule": None,
        "fieldErrors": []}


def test_a_cap_refusal_never_names_a_line() -> None:
    """The cap is of the whole batch, so a refusal in a save with lines does
    not take the place of a line."""
    line = invoke._Line(key="lines", to="order_line", place=3, id=None, data={})
    out = invoke._Outcome(action="add")
    assert invoke._set_write_error(out, store.OrgCapReached(BYTES_SENTENCE), line) is True
    assert out.fail is not None
    assert out.fail.error.detail["message"] == BYTES_SENTENCE


# -- the fences --------------------------------------------------------------------


def test_the_r8_suite_is_on_both_must_run_lists() -> None:
    assert "tests/unit/test_studio_org_caps_r8.py" in r8_lists.must_run()
