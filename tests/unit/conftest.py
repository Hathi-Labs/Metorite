"""Shared unit-test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture
def no_copilot_cli_download(monkeypatch):
    """Keep a Copilot SDK unit test off the network (task 2472).

    ``CopilotClient()`` resolves the CLI in ``__init__`` and, with no
    ``COPILOT_CLI_PATH``, downloads it from GitHub releases. A 504 there failed
    the 2026-11-09 release. The resolve returns a stub path, and any direct
    call to the SDK's download functions raises.
    """
    import copilot._cli_download as dl
    import copilot.client as cc

    def _refuse(*_a, **_kw):
        raise AssertionError("unit test tried to download the Copilot CLI")

    monkeypatch.setattr(cc, "_get_or_download_cli",
                        lambda **_kw: "copilot-cli-not-downloaded")
    monkeypatch.setattr(dl, "get_or_download_cli", _refuse)
    monkeypatch.setattr(dl, "download_cli", _refuse)
