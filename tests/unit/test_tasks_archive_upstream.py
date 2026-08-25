"""Archive-instead-of-delete back-propagation to a connected workspace.

A delete in the app removes the task locally but ARCHIVES its upstream
counterpart (recoverable there) rather than hard-deleting it; an explicit
Archive/Restore mirrors the same archived flag upstream.

🔧 **RE-CUT 2026-08-25 (D52 repair round 1, board WS-39 S1).** The first four
cases in this file exercised ``ClickUpProvider.archive_task`` directly —
constructing it, stubbing its ``httpx`` client, asserting the
``clickup.archive_task`` broker action id. That class was DELETED by D52, so
this module raised ``ImportError`` at COLLECTION and took the whole
``tests/unit`` run down with it (``pytest -x`` on CI reported nothing else).
They are deleted, not skipped, per §12.6 criterion 7 — there is no connector to
assert against and no honest way to write one.

What SURVIVES is the half that never belonged to ClickUp: ``items``' own
contract with whatever provider it is handed.

  * ``items._delete_upstream`` archives upstream and does **not** call
    ``delete_task`` — the recoverability rule, which is the app's decision, not
    the vendor's;
  * ``items._archive_upstream`` mirrors the flag for SYNCED rows only,
    best-effort (a provider error never raises), reusing one provider per
    account.

Both reach a recording stub through a patched ``build_provider``, so they never
needed a real connector and do not need one now. ⚠️ This plumbing is
**unreachable in production** — the registry is empty, so nothing can build a
provider — and WS-39 **S3a** deletes it with the ``gtd_*`` store it serves.
These tests go with it then.
"""
from __future__ import annotations

import asyncio
import types

# ── items._delete_upstream / _archive_upstream ──────────────────────────────

def _row(**kw):
    base = dict(id="abc123def456", source="SYNCED",
               provider_task_id="T1", account_id="acc1")
    base.update(kw)
    return types.SimpleNamespace(**base)


class _RecordingProvider:
    def __init__(self):
        self.archived: list = []
        self.deleted: list = []

    async def archive_task(self, tid, archived=True):
        self.archived.append((tid, archived))

    async def delete_task(self, tid):
        self.deleted.append(tid)


def _patch_provider_build(monkeypatch, provider):
    """Stub out account lookup + credential decrypt + provider construction so
    _delete_upstream/_archive_upstream reach a recording provider with no DB.

    ``build_provider`` is patched rather than exercised: since D52 the real one
    refuses every name (the registry is empty), and what these cases are about
    is what ``items`` DOES with a provider — not which provider it gets.
    """
    import gateway.routes.tasks.items as items

    async def _fake_owner(db, acc_id, uid):
        return types.SimpleNamespace(
            id=acc_id, provider="stub", workspace_id="ws1",
            credentials_encrypted=b"x")

    monkeypatch.setattr(items, "_assert_account_owner", _fake_owner)
    monkeypatch.setattr(
        items, "_key_store",
        lambda: types.SimpleNamespace(decrypt=lambda _b: '{"token": "t"}'))
    monkeypatch.setattr(items, "build_provider",
                        lambda *a, **k: provider)


def test_delete_upstream_archives_not_deletes(monkeypatch):
    """A purge of a synced task ARCHIVES it upstream — never hard-deletes."""
    import gateway.routes.tasks.items as items

    prov = _RecordingProvider()
    _patch_provider_build(monkeypatch, prov)

    asyncio.run(items._delete_upstream(None, _row(), "u1"))

    assert prov.archived == [("T1", True)]
    assert prov.deleted == []  # the upstream DELETE is never used


def test_archive_upstream_skips_local_and_mirrors_synced(monkeypatch):
    import gateway.routes.tasks.items as items

    prov = _RecordingProvider()
    _patch_provider_build(monkeypatch, prov)

    rows = [
        _row(id="local1", source="LOCAL", provider_task_id=None,
             account_id=None),          # skipped (local)
        _row(id="s1", provider_task_id="T1"),
        _row(id="s2", provider_task_id="T2"),
    ]
    asyncio.run(items._archive_upstream(None, rows, "u1", True))

    assert prov.archived == [("T1", True), ("T2", True)]


def test_archive_upstream_is_best_effort(monkeypatch):
    """A provider error on one row is swallowed and doesn't abort the rest."""
    import gateway.routes.tasks.items as items

    class _Flaky(_RecordingProvider):
        async def archive_task(self, tid, archived=True):
            if tid == "T1":
                raise RuntimeError("provider down")
            await super().archive_task(tid, archived)

    prov = _Flaky()
    _patch_provider_build(monkeypatch, prov)
    rows = [_row(id="s1", provider_task_id="T1"),
            _row(id="s2", provider_task_id="T2")]

    # Must not raise; the healthy row still gets archived.
    asyncio.run(items._archive_upstream(None, rows, "u1", True))
    assert prov.archived == [("T2", True)]
