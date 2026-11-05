"""``GUIDE_ORGS``, the switch of the guide's tour (WS-OB GD-5a).

Spec: ``project-docs/specs/assistant_guide.md`` §7. The one reader is
``acb_common.guide_flags``. ``test_auth_me_shell.py`` proves that
``GET /auth/me/shell`` returns it as ``guide`` for the session's org.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from acb_common import guide_flags as flags


def _settings(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(guide_orgs=raw))


def test_the_guide_is_dark_by_default() -> None:
    from acb_common.env_guard import PLATFORM_ENV_PREFIXES, is_platform_env
    from acb_common.settings import Settings

    assert Settings.model_fields["guide_orgs"].default == ""
    assert "GUIDE_" in PLATFORM_ENV_PREFIXES
    assert is_platform_env("GUIDE_ORGS")


def test_a_reload_may_apply_it() -> None:
    """A flip is a SIGHUP reload, as each ``*_orgs`` list is."""
    from gateway.flag_reload import reloadable

    assert reloadable("GUIDE_ORGS")


def test_a_star_opens_every_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, "*")
    assert flags.guide_allowed("org-a") is True
    assert flags.guide_allowed("anything") is True


def test_a_listed_org_is_open_in_any_case_and_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, " Org-A , org-b")
    assert flags.guide_orgs() == {"org-a", "org-b"}
    assert flags.guide_allowed("org-a") is True
    assert flags.guide_allowed("ORG-B") is True


def test_an_unlisted_org_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, "org-b")
    assert flags.guide_allowed("org-a") is False


def test_an_empty_list_closes_every_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, "")
    assert flags.guide_orgs() == frozenset()
    assert flags.guide_allowed("org-a") is False


def test_no_org_is_closed_even_with_a_star(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, "*")
    assert flags.guide_allowed(None) is False
    assert flags.guide_allowed("") is False


def test_the_reader_reads_the_settings_on_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, "org-a")
    assert flags.guide_allowed("org-a") is True
    _settings(monkeypatch, "")
    assert flags.guide_allowed("org-a") is False
