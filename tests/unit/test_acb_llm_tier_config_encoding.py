"""The tier-models config loads on a machine whose default encoding is not UTF-8.

Task 1333. `_init_tier_models` opened `infra/litellm/config.yaml` with no
`encoding`. On Windows the default is cp1252, the file holds a UTF-8 byte that
cp1252 cannot read, the loader logged a warning and went on, and a local run
used the fallback models. Linux CI has UTF-8 by default, so only this test, which
makes `Path.open` default to cp1252, sees the fault there.
"""

from __future__ import annotations

import io
import pathlib

import pytest

pytest.importorskip("acb_llm")

from acb_llm import client  # noqa: E402

_CONFIG = (
    "# Ï is C3 8F in UTF-8, and 8F has no cp1252 character.\n"
    "model_list:\n"
    "  - model_name: tier-fast\n"
    "    litellm_params:\n"
    "      model: test/encoding-probe-model\n"
)


def test_the_tier_config_loads_when_the_default_encoding_is_cp1252(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "infra" / "litellm" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_bytes(_CONFIG.encode("utf-8"))

    real_open = pathlib.Path.open

    def windows_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding is None:
            encoding = "cp1252"  # what Windows picks with no `encoding`
        return real_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(pathlib.Path, "open", windows_open)
    monkeypatch.setattr(client, "_find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(client, "_tier_models_initialised", False)
    monkeypatch.setattr(client, "_TIER_MODEL", dict(client._TIER_MODEL))
    monkeypatch.setattr(client, "ensure_model_registered", lambda model: None)
    monkeypatch.setattr("acb_llm.model_config.load_blob", lambda key: None)

    client._init_tier_models()

    assert client._TIER_MODEL["tier1"] == "test/encoding-probe-model"


def test_the_probe_really_fails_under_cp1252() -> None:
    """Prove the probe bites: cp1252 cannot decode the config text."""
    with pytest.raises(UnicodeDecodeError):
        io.BytesIO(_CONFIG.encode("utf-8")).read().decode("cp1252")
