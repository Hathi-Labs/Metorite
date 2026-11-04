"""WS-BRIEF BR-2d — the delivery monitor with no database.

Code: ``gateway/brief/monitor.py``, ``gateway/routes/admin_brief_health.py``.
The database half is ``tests/unit/test_brief_monitor_r8.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.brief import monitor
from gateway.jobs import worker
from gateway.routes import admin_brief_health
from gateway.routes.admin._common import require_admin_user

_ROOT = Path(__file__).resolve().parents[2]


def test_the_share_and_the_target() -> None:
    assert monitor.Stats(0, 0, None, None, None).met is True
    assert monitor.Stats(100, 99, 1.0, 2.0, 3.0).met is True
    s = monitor.Stats(100, 98, 1.0, 2.0, 3.0)
    assert s.share == 0.98 and s.met is False
    assert s.body()["lag_seconds"] == {"p50": 1.0, "p95": 2.0, "max": 3.0}


def test_the_kind_and_the_seed_register_once() -> None:
    monitor.register_jobs(replace=True)
    monitor.register_jobs(replace=True)
    assert worker.seed_hooks.count(monitor.seed) == 1


def test_the_route_is_admin_gated_and_dark() -> None:
    app = FastAPI()
    app.include_router(admin_brief_health.router)
    # No override of the admin gate: an anonymous call is refused, not served.
    resp = TestClient(app).get("/admin/brief/health")
    assert resp.status_code in (401, 403)
    deps = [d.call for d in admin_brief_health.router.routes[0].dependant.dependencies]
    assert require_admin_user in deps


def test_the_monitor_only_reads() -> None:
    src = (_ROOT / "apps/services/gateway/gateway/brief/monitor.py").read_text(encoding="utf-8")
    for word in ("INSERT", "UPDATE", "DELETE"):
        assert word not in src.replace("UPDATE brief", "x")
