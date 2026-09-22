"""D-PM-36 — a chat write says it came through the assistant.

Spec: ``project-docs/specs/projects_ai_chat.md`` §6.

``X-Actor-Via`` on the request binds ``core.ACTOR_VIA`` for that request, and
``record_activity`` copies it into ``meta.via``. ``created_by`` stays the
member, so authorship rules keep working. Three things this file pins:

1. a bound value lands on the row, and only on ``meta``;
2. no header, or a malformed one, stamps nothing — the human path is unchanged;
3. a caller that set ``meta.via`` itself keeps its own word.

The insert is faked here because the SQL is unchanged: ``insert_row`` writes
the same JSONB column it always did. What changes is the dict, and that is
what these tests read. The dependency is exercised through FastAPI's own
header parsing, so the alias and the default are the real ones.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from gateway.routes.projects import core


@pytest.fixture
def inserted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def fake_insert(db: Any, table: str, values: dict[str, Any]) -> Any:
        rows.append({"table": table, **values})
        return values

    async def fake_touch(db: Any, *task_ids: Any) -> None:
        return None

    monkeypatch.setattr(core, "insert_row", fake_insert)
    monkeypatch.setattr(core, "touch_task", fake_touch)
    return rows


async def _record(inserted: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    await core.record_activity(
        None,
        activity_type="comment",
        created_by="pm@fracktal.in",
        task_id="0f8fad5b-d9cb-469f-a165-70867728950e",
        body="hi",
        **kwargs,
    )
    return inserted[-1]


async def test_a_bound_via_lands_in_meta_and_created_by_stays_the_member(inserted) -> None:
    core.ACTOR_VIA.set("chat:projects-assistant")
    row = await _record(inserted)
    assert row["meta"] == {"via": "chat:projects-assistant"}
    assert row["created_by"] == "pm@fracktal.in"


async def test_no_via_stamps_nothing(inserted) -> None:
    core.ACTOR_VIA.set("")
    row = await _record(inserted)
    assert row["meta"] is None


async def test_the_callers_own_via_wins(inserted) -> None:
    core.ACTOR_VIA.set("chat:projects-assistant")
    row = await _record(inserted, meta={"via": "workflow:nightly"})
    assert row["meta"]["via"] == "workflow:nightly"


async def test_automation_and_via_compose(inserted) -> None:
    core.ACTOR_VIA.set("chat:projects-assistant")
    row = await _record(inserted, automation=True)
    assert row["meta"] == {"automation": True, "via": "chat:projects-assistant"}


def _app() -> TestClient:
    app = FastAPI()

    # Async, like every Projects route: a ContextVar bound in a dependency
    # reaches an async handler only if the dependency is async too. The
    # sync form of `capture_actor_via` bound nothing — this fence found it.
    @app.get("/probe", dependencies=[Depends(core.capture_actor_via)])
    async def probe() -> dict[str, str]:
        return {"via": core.ACTOR_VIA.get("")}

    return TestClient(app)


@pytest.mark.parametrize(
    ("header", "expect"),
    [
        ("chat:projects-assistant", "chat:projects-assistant"),
        ("CHAT:Projects-Assistant", "chat:projects-assistant"),
        ("", ""),
        ("has spaces in it", ""),
        ("x" * 80, ""),
        ("<script>", ""),
    ],
)
def test_the_dependency_binds_only_a_well_formed_value(header: str, expect: str) -> None:
    headers = {"X-Actor-Via": header} if header else {}
    assert _app().get("/probe", headers=headers).json() == {"via": expect}


def test_the_dependency_is_on_the_projects_router() -> None:
    names = {getattr(d.dependency, "__name__", "") for d in core.router.dependencies}
    assert "capture_actor_via" in names
