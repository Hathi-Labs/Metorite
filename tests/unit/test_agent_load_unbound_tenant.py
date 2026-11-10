"""An agent-list load with no tenant bound reads the file and never writes.

Before this, every such load ran two failing INSERTs into dynamic_agents (the
organization_id default casts '' to uuid). Production logged about 12,000
``invalid input syntax for type uuid`` errors a day from it. Fence for the
guard in ``_load_dynamic_agents``.
"""

from __future__ import annotations

import gateway.routes.agent as routes_agent


def test_unbound_load_reads_file_and_skips_every_db_write(monkeypatch) -> None:
    monkeypatch.setattr(routes_agent, "_tenant_is_bound", lambda: False)

    def _boom(*_a, **_k):
        raise AssertionError("an unbound load must not write to the database")

    monkeypatch.setattr(routes_agent, "_sync_file_into_db", _boom)
    monkeypatch.setattr(routes_agent, "_save_dynamic_agents", _boom)
    monkeypatch.setattr(
        routes_agent, "_read_agents_file", lambda: [{"name": "from-file"}]
    )

    assert routes_agent._load_dynamic_agents() == [{"name": "from-file"}]


def test_read_agents_file_strips_strings_and_survives_a_missing_file(
    monkeypatch, tmp_path
) -> None:
    f = tmp_path / "agents.json"
    f.write_text('[{"name": " a ", "tags": ["x"]}]', encoding="utf-8")
    monkeypatch.setattr(routes_agent, "_get_agents_file", lambda: f)
    assert routes_agent._read_agents_file() == [{"name": "a", "tags": ["x"]}]

    monkeypatch.setattr(routes_agent, "_get_agents_file", lambda: tmp_path / "none.json")
    assert routes_agent._read_agents_file() == []
