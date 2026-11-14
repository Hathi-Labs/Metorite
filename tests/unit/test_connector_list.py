"""The connector list (WS-54 IN-11).

R7 fence: ``connector_list.json`` is the one curated file. Each provider with
a connect flow has a row, each row names an owning app or the console, and the
TypeScript reader reads the same file by a generated twin.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from acb_common import connector_list as cl
from acb_common.studio_manifest import CONNECTION_NAME_RE
from gateway.calendar_sync import connections as cal
from gateway.kb_sources.registry import SOURCES as DRIVE_SOURCES
from gateway.routes import integrations
from gateway.routes import integrations_connections as ic

ROOT = Path(__file__).resolve().parents[2]
TS = ROOT / "workbench/control_plane/src/lib/connectors.ts"
GEN_TS = ROOT / "workbench/control_plane/src/lib/connectors.generated.ts"
GEN_PY = ROOT / "scripts/gen_connectors.py"

#: The guides that the console does not offer as a key. An app owns the
#: connect flow, or the operator owns the platform app.
_NOT_A_KEY_GUIDE = {"zoho-crm", "whatsapp", "github", "gmail-oauth", "microsoft-oauth"}

#: The apps that own a connect flow beside the console apps of ``_SETUP_HREF``.
_OTHER_OWNERS = {"calendar", "knowledge", "console"}
_KEYS_OF_ROW = {
    "id", "label", "signin", "owning_app", "unlocks",
    "built_in_app_shows_data", "key_hint", "scope",
}


def _ids() -> list[str]:
    return [r["id"] for r in cl.connector_rows()]


def _flows() -> set[str]:
    """The provider ids that have a connect flow, derived from the code."""
    out = {"mailbox", "whatsapp", "zoho", "github", "mcp"}
    out |= set(cal.SOURCES)
    out |= set(DRIVE_SOURCES)
    out |= {s.replace("-", "_") for s in integrations._SETUP_GUIDES if s not in _NOT_A_KEY_GUIDE}
    return out


def test_each_provider_with_a_connect_flow_has_a_row() -> None:
    assert _flows() - set(_ids()) == set()


def test_no_row_for_a_provider_without_a_flow() -> None:
    assert set(_ids()) - _flows() == set()


def test_ids_are_unique_and_match_the_connection_grammar() -> None:
    ids = _ids()
    assert len(ids) == len(set(ids))
    for i in ids:
        assert CONNECTION_NAME_RE.fullmatch(i), i


def test_each_row_names_an_owning_app_or_the_console() -> None:
    known = set(ic._KNOWN_APPS) | _OTHER_OWNERS
    for r in cl.connector_rows():
        assert r["owning_app"] in known, r["id"]


def test_row_shape_and_values() -> None:
    for r in cl.connector_rows():
        assert set(r) == _KEYS_OF_ROW, r["id"]
        assert r["signin"] in cl.SIGNIN_KINDS
        assert r["scope"] in cl.SCOPES
        assert isinstance(r["built_in_app_shows_data"], bool)
        assert r["label"].strip() and r["unlocks"].strip()
        if r["signin"] == "key":
            assert r["key_hint"].strip(), r["id"]
        if r["signin"] == "one_click":
            assert r["key_hint"] == "", r["id"]


def test_the_file_holds_no_credential() -> None:
    from acb_common.secret_shapes import is_secret_field_key

    for r in cl.connector_rows():
        for k, v in r.items():
            assert not is_secret_field_key(k), k
            if isinstance(v, str):
                assert not re.search(r"(sk-|ghp_|xox[bp]-|Bearer )\S{8,}", v), r["id"]


def test_copy_follows_the_style() -> None:
    for r in cl.connector_rows():
        for field in ("unlocks", "key_hint"):
            text = r[field]
            assert ";" not in text, r["id"]
            for sentence in filter(None, re.split(r"(?<=[.!?])\s+", text)):
                assert len(sentence.split()) <= 20, (r["id"], sentence)


def test_connector_lookup() -> None:
    assert cl.connector("zoho")["owning_app"] == "crm"
    assert cl.connector("nope") is None


def test_the_ts_reader_reads_the_one_file() -> None:
    spec = importlib.util.spec_from_file_location("gen_connectors", GEN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_connectors"] = mod
    spec.loader.exec_module(mod)
    assert GEN_TS.read_text(encoding="utf-8") == mod.render_ts(), (
        "Run: uv run python scripts/gen_connectors.py"
    )
    src = TS.read_text(encoding="utf-8")
    assert "@/lib/connectors.generated" in src
    assert "connector_list.json" in src
