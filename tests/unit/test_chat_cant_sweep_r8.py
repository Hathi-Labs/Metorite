"""R8: the PAR-7 sweep reads ONE organization, through the tenant seam (task 3116).

Spec: ``project-docs/specs/chat_parity.md`` §6.5. Script:
``scripts/chat_cant_sweep.py``. The pure twin is ``test_chat_cant_sweep.py``.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS, FORCE RLS on
``chat_message`` and ``chat_session``), with two organizations:

1. a run for org A counts only org A's answers. Org B's marker text, its app
   and its count never reach the text or the JSON. The reverse holds too;
2. a run for an organization with no row reads nothing and prints the
   no-gap line;
2b. a run of org A for the board of org B prints the counts and NO member
   text of org A. With the same organization on both sides, the examples
   come back;
3. the bind is the fence. The SQL filter of org A, run in a session bound to
   org B, reads no row.

Mutations, each red here (R7):

* ``read_rows`` opens ``acb_graph.db.get_session()`` and not
  ``tenant_session(org)``: (1) gets the no-gap line for org A, because an
  unbound backend reads no row under FORCE RLS.
  ``test_a_read_with_no_tenant_bind_reads_nothing`` runs that mutation in
  the suite, so it stays proved;
* ``read_rows`` binds a fixed tenant and not the ``--org`` of the run: one of
  the two directions of (1) goes red;
* the ``organization_id`` filter is dropped from ``_READ_SQL``: still green,
  because RLS is the fence. The filter is the second belt.

The rows are SYNTHETIC. The seed runs as the superuser, which escapes RLS, so
it can stamp each organization directly.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_chat_cant_sweep_r8.py -v -rs
"""

from __future__ import annotations

import json
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import chat_cant_sweep as sweep

pytestmark = _DB_GATE

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
NOW_ARG = "2026-11-22T12:00:00Z"
# Plain words, so the mask keeps them. A marker that the mask removed could
# leak and the test would still pass.
MARK_A = "falconmarker"
MARK_B = "zebraquokkamarker"


def _ms(hours_ago: float) -> int:
    return int((NOW - timedelta(hours=hours_ago)).timestamp() * 1000)


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """Org A: two Email gaps. Org B: three WhatsApp gaps. Seeded as the superuser."""
    tag = uuid.uuid4().hex[:8]
    plan = {
        promoted.org_a: [
            (f"par7a-{tag}-{i}", f"attach the {MARK_A} file to the email {i}") for i in range(2)
        ],
        promoted.org_b: [
            (f"par7b-{tag}-{i}", f"post the {MARK_B} note on whatsapp {i}") for i in range(3)
        ],
    }
    with promoted.admin_engine.begin() as c:
        for org, sessions in plan.items():
            for n, (sid, ask) in enumerate(sessions):
                c.execute(
                    text(
                        "INSERT INTO chat_session (id, user_id, agent_name, organization_id) "
                        "VALUES (:id, 'member@par7.test', 'orchestrator', :o)"
                    ),
                    {"id": sid, "o": org},
                )
                marker = MARK_A if org == promoted.org_a else MARK_B
                for mid, role, content, hours in (
                    ("u", "user", ask, 5 + n),
                    ("a", "assistant", f"I can't do that from the chat. {marker}", 4.9 + n),
                    ("s", "system", f"invite everyone {marker}", 4.8 + n),
                ):
                    c.execute(
                        text(
                            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                            "member_typed, organization_id) VALUES (:id, :s, :r, :c, :t, TRUE, :o)"
                        ),
                        {
                            "id": f"{mid}-{sid}",
                            "s": sid,
                            "r": role,
                            "c": content,
                            "t": _ms(hours),
                            "o": org,
                        },
                    )
    return promoted


@pytest.fixture
def graph_as_app(seeded, app_engine, monkeypatch):  # noqa: F811
    """``acb_graph`` sessions open as the app role, one fresh backend each."""
    eng = create_engine(seeded.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield seeded
    finally:
        eng.dispose()


def _run(capsys, org: str, *extra: str, board: str | None = None) -> tuple[int, str]:
    """A run whose board belongs to ``org`` itself, unless ``board`` names another."""
    code = sweep.main(["--org", org, "--board-org", board or org, "--now", NOW_ARG, *extra])
    out = capsys.readouterr()
    assert out.err == ""
    return code, out.out


def test_org_a_reads_only_its_own_rows(graph_as_app, capsys):
    code, out = _run(capsys, graph_as_app.org_a)
    assert code == 0
    assert "| Email | 2 | 100% |" in out, out
    assert MARK_A in out
    assert MARK_B not in out and "WhatsApp" not in out and "| 3 |" not in out
    assert "I can't do that" not in out and "invite everyone" not in out
    assert "par7a-" not in out

    code, raw = _run(capsys, graph_as_app.org_a, "--json")
    data = json.loads(raw)
    assert code == 0 and data["total"] == 2
    assert [(r["app"], r["count"]) for r in data["apps"]] == [("email", 2)]
    assert MARK_B not in raw and "whatsapp" not in raw.lower()


def test_org_b_reads_only_its_own_rows(graph_as_app, capsys):
    code, out = _run(capsys, graph_as_app.org_b)
    assert code == 0
    assert "| WhatsApp | 3 | 100% |" in out, out
    assert MARK_B in out
    assert MARK_A not in out and "| Email |" not in out

    code, raw = _run(capsys, graph_as_app.org_b, "--json")
    data = json.loads(raw)
    assert data["total"] == 3 and [r["app"] for r in data["apps"]] == ["whatsapp"]
    assert MARK_A not in raw and '"email"' not in raw


def test_counts_only_shows_no_member_text(graph_as_app, capsys):
    code, out = _run(capsys, graph_as_app.org_a, "--counts-only")
    assert code == 0 and "| Email | 2 | 100% |" in out
    assert MARK_A not in out and sweep.EXAMPLES_HEADER not in out


def test_another_board_gets_the_counts_and_no_member_text(graph_as_app, capsys):
    """P0. Org A's chat, for a comment on org B's board: counts only."""
    a, b = graph_as_app.org_a, graph_as_app.org_b
    code, out = _run(capsys, a, board=b)
    assert code == 0
    assert f"Organization: {a}" in out and sweep.NOT_BOARD_OWNER in out
    assert "| Email | 2 | 100% |" in out
    assert MARK_A not in out and MARK_B not in out and sweep.EXAMPLES_HEADER not in out

    code, raw = _run(capsys, a, "--json", board=b)
    data = json.loads(raw)
    assert data["examples"] == [] and data["counts_only"] is True and data["total"] == 2
    assert MARK_A not in raw and MARK_B not in raw

    # The same org owns the board: the examples come back.
    code, out = _run(capsys, a)
    assert MARK_A in out and sweep.EXAMPLES_HEADER in out and sweep.NOT_BOARD_OWNER not in out


def test_an_org_with_no_rows_reads_nothing(graph_as_app, capsys):
    code, out = _run(capsys, str(uuid.uuid4()))
    assert (code, out) == (0, "No gaps found this week.\n")


def test_the_window_holds_the_read(graph_as_app, capsys):
    """A run whose window ends before the seed reads no row."""
    code = sweep.main(
        [
            "--org",
            graph_as_app.org_a,
            "--board-org",
            graph_as_app.org_a,
            "--now",
            "2026-11-01T00:00:00Z",
        ]
    )
    assert (code, capsys.readouterr().out) == (0, "No gaps found this week.\n")


def test_the_bind_is_the_fence(graph_as_app):
    """The SQL filter of org A, in a session bound to org B, reads no row."""
    params = {"org": graph_as_app.org_a, "since": _ms(48), "until": _ms(0), "limit": 100}
    with graph_db.tenant_session(graph_as_app.org_b) as s:
        assert s.execute(text(sweep._READ_SQL), params).all() == []
    with graph_db.tenant_session(graph_as_app.org_a) as s:
        assert len(s.execute(text(sweep._READ_SQL), params).all()) == 4  # 2 user + 2 assistant


def test_a_read_with_no_tenant_bind_reads_nothing(graph_as_app, monkeypatch, capsys):
    """The mutation, run here: ``read_rows`` with the unbound session.

    Under FORCE RLS it reads no row, so the org A case above would go red.
    """

    @contextmanager
    def unbound(_org=None):
        with graph_db.get_session() as s:
            yield s

    monkeypatch.setattr(graph_db, "tenant_session", unbound)
    code, out = _run(capsys, graph_as_app.org_a)
    assert (code, out) == (0, "No gaps found this week.\n")
