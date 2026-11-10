"""Chat speed: the loader fetches a repo once per TTL, not on every run.

Fence for ``acb_skills.loader._refresh_due`` and the ``_ensure_repo`` skip.
Setting ``AGENT_REPO_REFRESH_TTL_SECONDS=0`` restores fetch-on-every-run.
"""
from __future__ import annotations

from acb_skills import loader


def test_a_repo_is_due_before_its_first_refresh(monkeypatch):
    monkeypatch.delenv("AGENT_REPO_REFRESH_TTL_SECONDS", raising=False)
    assert loader._refresh_due("never-seen") is True


def test_a_repo_is_not_due_inside_the_ttl(monkeypatch):
    monkeypatch.setenv("AGENT_REPO_REFRESH_TTL_SECONDS", "60")
    loader._mark_refreshed("r1")
    assert loader._refresh_due("r1") is False


def test_a_repo_is_due_again_after_the_ttl(monkeypatch):
    monkeypatch.setenv("AGENT_REPO_REFRESH_TTL_SECONDS", "60")
    loader._REFRESHED_AT["r2"] = loader.time.monotonic() - 61
    assert loader._refresh_due("r2") is True


def test_a_zero_ttl_always_pulls(monkeypatch):
    monkeypatch.setenv("AGENT_REPO_REFRESH_TTL_SECONDS", "0")
    loader._mark_refreshed("r3")
    assert loader._refresh_due("r3") is True


def test_a_bad_ttl_falls_back_to_sixty(monkeypatch):
    monkeypatch.setenv("AGENT_REPO_REFRESH_TTL_SECONDS", "soon")
    assert loader._refresh_ttl() == 60.0
