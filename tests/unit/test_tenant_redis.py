"""MT-1e — the Redis wrapper cannot express an unprefixed key, and the build says so.

``saas_multitenancy.md`` §0.9.4 is the whole test plan:

    "Redis stays, but tenant prefixing is enforced by a wrapper client, not by
     convention. A convention is a thing people forget; a client that cannot
     construct an unprefixed key is not."

So these tests do not check that keys are *usually* prefixed. They check the
stronger property the spec asked for: that there is **no public path** to an
unprefixed key — not through the builder, not through the dataclass constructor,
not through a client method, not through the pipeline, not through a SCAN
pattern. Plus the fail-closed rule (§1.9): unbound raises rather than defaulting
to a global key.

The last section is the ratchet the ticket's done-when names: a source-level
check that fails the build on a direct ``redis`` client outside the wrapper. Its
allow-list is today's un-migrated call sites, and each entry is an item on the
follow-up ticket's checklist — see the migration path in
``acb_common/tenant_redis.py``'s module docstring.

Spec: ``saas_multitenancy.md`` §0.9.4, §1.9 · MT-1e · D15.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
from acb_common import tenant_redis as tr
from acb_common.tenant_redis import (
    KEY_ROOT,
    ConsumerGroup,
    ScanPattern,
    TenantKey,
    TenantMismatch,
    TenantNotBound,
    TenantRedis,
    current_organization,
    group,
    key,
    match,
    organization_bound,
    organization_scope,
)

_REPO = Path(__file__).resolve().parents[2]

ORG_A = "org-aaaaaaaa"
ORG_B = "org-bbbbbbbb"


@pytest.fixture(autouse=True)
def _unbound():
    """Every test starts with NO tenant bound.

    A leaked binding from a previous test would make the fail-closed assertions
    pass for the wrong reason, which is the one failure mode a tenancy test
    cannot afford.
    """
    token = tr._ORGANIZATION_ID.set(None)
    try:
        yield
    finally:
        tr._ORGANIZATION_ID.reset(token)


class _FakeRedis:
    """Records the raw key strings the wrapper actually sends to redis-py."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.store: dict[str, Any] = {}

    async def get(self, name: str) -> Any:
        self.calls.append(("get", (name,)))
        return self.store.get(name)

    async def set(self, name: str, value: Any, **kwargs: Any) -> bool:
        self.calls.append(("set", (name, value)))
        self.store[name] = value
        return True

    async def xadd(self, name: str, fields: Any, **kwargs: Any) -> str:
        self.calls.append(("xadd", (name,)))
        return "1-0"

    async def xread(self, streams: dict[str, Any], **kwargs: Any) -> list[Any]:
        self.calls.append(("xread", tuple(streams)))
        return []

    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> bool:
        self.calls.append(("xgroup_create", (name, groupname)))
        return True

    async def xreadgroup(self, groupname: str, consumername: str, streams: dict, **kw: Any):
        self.calls.append(("xreadgroup", (groupname, *streams)))
        return []

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self.calls.append(("xack", (name, groupname)))
        return len(ids)

    async def publish(self, channel: str, message: Any) -> int:
        self.calls.append(("publish", (channel,)))
        return 1

    async def scan_iter(self, match: str | None = None, count: int = 10):
        self.calls.append(("scan_iter", (match or "",)))
        for name in list(self.store):
            yield name

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)

    @property
    def sent_keys(self) -> list[str]:
        return [arg for _, args in self.calls for arg in args if isinstance(arg, str)]


class _FakePipeline:
    def __init__(self, parent: _FakeRedis) -> None:
        self.parent = parent

    def hincrbyfloat(self, name: str, field: str, amount: float) -> None:
        self.parent.calls.append(("hincrbyfloat", (name,)))

    def hincrby(self, name: str, field: str, amount: int = 1) -> None:
        self.parent.calls.append(("hincrby", (name,)))

    def expire(self, name: str, seconds: int) -> None:
        self.parent.calls.append(("expire", (name,)))

    async def execute(self) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# 1. The key builder always prefixes.
# ---------------------------------------------------------------------------

def test_key_carries_the_tenant_prefix() -> None:
    with organization_scope(ORG_A):
        assert key("activity").value == f"{KEY_ROOT}:{ORG_A}:activity"
        assert key("room", "thread-1").value == f"{KEY_ROOT}:{ORG_A}:room:thread-1"
        # Nesting is parts, not a colon in the namespace — the shape
        # `cc:<org>:activity:live:<run>` still comes out right.
        assert key("activity", "live", "run-9").value == f"{KEY_ROOT}:{ORG_A}:activity:live:run-9"


@pytest.mark.parametrize(
    "namespace",
    # Every namespace in the tree today (activity.py, room_stream.py,
    # stream_relay.py, steer.py) — the ones this wrapper must be able to express.
    [
        "activity", "cost", "room", "presence", "stream", "active",
        "runactor", "runsource", "runfloor", "control", "ctrl-ack", "steer",
    ],
)
def test_every_existing_namespace_round_trips_prefixed(namespace: str) -> None:
    with organization_scope(ORG_A):
        assert key(namespace, "x").value.startswith(f"{KEY_ROOT}:{ORG_A}:{namespace}:")


def test_str_of_a_key_is_the_prefixed_value() -> None:
    """``f"{k}"`` must not be a way to get something shorter than ``.value``."""
    with organization_scope(ORG_A):
        k = key("cost", "2026-08-08")
        assert str(k) == k.value == f"{KEY_ROOT}:{ORG_A}:cost:2026-08-08"


# ---------------------------------------------------------------------------
# 2. No public path yields an unprefixed key.
# ---------------------------------------------------------------------------

def test_no_public_attribute_of_a_key_is_unprefixed() -> None:
    """Reflection over the whole public surface of a built key.

    Not a spot check: the point of the type is that *nothing* on it hands back a
    usable-but-untenanted key, so the test enumerates rather than asserting on
    the two accessors it happens to remember. "Key-shaped" is read as "contains
    a ``:``" — the validated component fields (``namespace``, ``parts``,
    ``organization_id``) are inputs and cannot contain one, so anything with a
    colon in it is something a caller could mistake for a key.
    """
    with organization_scope(ORG_A):
        k = key("room", "thread-1")
        prefix = f"{KEY_ROOT}:{ORG_A}:"
        checked = 0
        for name in dir(k):
            if name.startswith("_"):
                continue
            value = getattr(k, name)
            if isinstance(value, str) and ":" in value:
                checked += 1
                assert value.startswith(prefix), (
                    f"TenantKey.{name} exposes an unprefixed key: {value!r}"
                )
        assert checked, "no key-shaped attribute was inspected — the test proves nothing"


def test_client_rejects_a_raw_string_key() -> None:
    """The choke point: no command on the client takes a key you typed."""
    client = TenantRedis(_FakeRedis())
    with organization_scope(ORG_A), pytest.raises(TypeError, match="TenantKey"):
        # Deliberately the exact untenanted key that exists in production today.
        client._raw("cc:activity")


async def test_every_client_command_sends_only_prefixed_keys() -> None:
    """Exercise the client and assert on what reached redis-py, not on intent."""
    fake = _FakeRedis()
    client = TenantRedis(fake)
    with organization_scope(ORG_A):
        await client.set(client.key("active", "t1"), "1")
        await client.get(client.key("active", "t1"))
        await client.xadd(client.key("stream", "t1"), {"event": "{}"})
        await client.xread({client.key("stream", "t1"): "0-0"})
        await client.publish(client.key("control", "t1"), "ping")
        g = client.group("cc-ingest")
        await client.xgroup_create(client.key("stream", "t1"), g, id="$", mkstream=True)
        await client.xreadgroup(g, "worker-1", {client.key("stream", "t1"): ">"})
        await client.xack(client.key("stream", "t1"), g, "1-0")
        pipe = client.pipeline()
        pipe.hincrbyfloat(client.key("cost", "2026-08-08"), "total|cost", 1.0)
        pipe.expire(client.key("cost", "2026-08-08"), 60)
        await pipe.execute()

    prefix = f"{KEY_ROOT}:{ORG_A}:"
    key_like = [s for s in fake.sent_keys if s.startswith(KEY_ROOT) or ":" in s]
    assert key_like, "the fake recorded nothing — the test proves nothing"
    for sent in key_like:
        # Group names are `<base>:<org>`, not keys; everything else must be a key.
        assert sent.startswith(prefix) or sent.endswith(f":{ORG_A}"), (
            f"an unprefixed value reached redis-py: {sent!r}"
        )


async def test_scan_pattern_cannot_escape_the_tenant() -> None:
    fake = _FakeRedis()
    client = TenantRedis(fake)
    with organization_scope(ORG_A):
        pattern = client.match("active")
        assert pattern.value == f"{KEY_ROOT}:{ORG_A}:active:*"
        async for _ in client.scan_iter(pattern):
            pass
        with pytest.raises(TypeError, match="ScanPattern"):
            async for _ in client.scan_iter("cc:active:*"):  # type: ignore[arg-type]
                pass
    assert ("scan_iter", (f"{KEY_ROOT}:{ORG_A}:active:*",)) in fake.calls


def test_glob_metacharacters_in_a_part_stay_literal() -> None:
    """A ``*`` in a value must not widen the pattern past this tenant's data."""
    with organization_scope(ORG_A):
        pattern = match("room", "thread*1")
        assert pattern.value == f"{KEY_ROOT}:{ORG_A}:room:thread\\*1:*"


def test_namespace_cannot_smuggle_a_colon() -> None:
    """`namespace="activity:live"` would let a caller hand-shape segments."""
    with organization_scope(ORG_A):
        with pytest.raises(ValueError, match="namespace"):
            key("activity:live", "run-1")
        with pytest.raises(ValueError, match="namespace"):
            key("cc:activity")


def test_organization_id_cannot_smuggle_a_colon_or_glob() -> None:
    for bad in ("org:evil", "org*", "", "org id", "a" * 65):
        with pytest.raises(ValueError, match="organization_id"), organization_scope(ORG_A):
            TenantKey(bad, "room", ("t1",))


def test_empty_or_whitespace_parts_are_rejected() -> None:
    """An empty part would collapse to ``cc:<org>:room:`` — a shared key."""
    with organization_scope(ORG_A):
        with pytest.raises(ValueError):
            key("room", "")
        with pytest.raises(ValueError):
            key("room", " t1 ")
        with pytest.raises(TypeError):
            key("room", 7)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Unbound tenant raises — never a silent global key.
# ---------------------------------------------------------------------------

def test_unbound_key_build_raises() -> None:
    assert organization_bound() is False
    with pytest.raises(TenantNotBound):
        key("activity")


def test_unbound_current_organization_raises() -> None:
    with pytest.raises(TenantNotBound):
        current_organization()


def test_unbound_direct_construction_raises() -> None:
    """The dataclass constructor is not a way around the builder."""
    with pytest.raises(TenantNotBound):
        TenantKey(ORG_A, "activity", ())
    with pytest.raises(TenantNotBound):
        ConsumerGroup(ORG_A, "cc-ingest")
    with pytest.raises(TenantNotBound):
        ScanPattern(ORG_A, "active", (), "*")


def test_unbound_group_and_match_raise() -> None:
    with pytest.raises(TenantNotBound):
        group("cc-ingest")
    with pytest.raises(TenantNotBound):
        match("active")


async def test_unbound_command_raises_even_with_a_key_in_hand() -> None:
    """A key captured under a binding is not usable after it is released."""
    client = TenantRedis(_FakeRedis())
    with organization_scope(ORG_A):
        captured = key("room", "t1")
    with pytest.raises(TenantNotBound):
        await client.get(captured)


def test_binding_is_released_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError), organization_scope(ORG_A):
        raise RuntimeError("boom")
    assert organization_bound() is False


def test_release_survives_a_foreign_token() -> None:
    """Fail closed on a cross-context reset, exactly like MT-0a's release."""
    tr.bind_organization(ORG_A)
    tr.release_organization(tr._ORGANIZATION_ID.set(ORG_B))
    tr.release_organization(None)  # no-op, must not raise
    tr._ORGANIZATION_ID.set(None)


# ---------------------------------------------------------------------------
# 4. Two tenants are disjoint.
# ---------------------------------------------------------------------------

def test_two_tenants_produce_disjoint_keys() -> None:
    with organization_scope(ORG_A):
        a = {key(ns, "same-thread-id").value for ns in ("room", "stream", "active", "cost")}
    with organization_scope(ORG_B):
        b = {key(ns, "same-thread-id").value for ns in ("room", "stream", "active", "cost")}
    # Same thread id, same namespaces — and no key in common. Before this
    # module, every one of these pairs was the SAME key.
    assert a and b and not (a & b)


def test_two_tenants_get_disjoint_consumer_groups() -> None:
    """§1.9: 'separate consumer groups per tenant on the Streams bus'."""
    with organization_scope(ORG_A):
        a = group("cc-ingest").value
    with organization_scope(ORG_B):
        b = group("cc-ingest").value
    assert a != b
    assert a.endswith(ORG_A) and b.endswith(ORG_B)


def test_two_tenants_get_disjoint_scan_patterns() -> None:
    with organization_scope(ORG_A):
        a = match("active").value
    with organization_scope(ORG_B):
        b = match("active").value
    assert a != b
    # Neither pattern can match the other tenant's keys.
    assert ORG_B not in a and ORG_A not in b


async def test_a_key_from_another_tenant_is_refused_at_command_time() -> None:
    """The realistic leak: a key that outlives its binding onto another task."""
    client = TenantRedis(_FakeRedis())
    with organization_scope(ORG_A):
        stolen = key("room", "t1")
    with organization_scope(ORG_B), pytest.raises(TenantMismatch):
        await client.get(stolen)


async def test_two_tenants_do_not_see_each_other_through_the_client() -> None:
    fake = _FakeRedis()
    client = TenantRedis(fake)
    with organization_scope(ORG_A):
        await client.set(client.key("room", "t1"), "A")
    with organization_scope(ORG_B):
        await client.set(client.key("room", "t1"), "B")
        assert await client.get(client.key("room", "t1")) == "B"
    with organization_scope(ORG_A):
        assert await client.get(client.key("room", "t1")) == "A"


# ---------------------------------------------------------------------------
# 5. The client has no proxy hole.
# ---------------------------------------------------------------------------

def test_client_does_not_proxy_unknown_attributes() -> None:
    """``__getattr__`` forwarding would hand redis-py a raw key string.

    The command surface is enumerated on purpose (see the class docstring); this
    pins that decision, because adding a two-line ``__getattr__`` later would
    silently reopen every hole the rest of this file closes.
    """
    client = TenantRedis(_FakeRedis())
    assert not hasattr(TenantRedis, "__getattr__")
    assert not hasattr(client, "keys")
    assert not hasattr(client, "flushdb")
    # __slots__ also stops a caller from stashing the raw client back on.
    with pytest.raises(AttributeError):
        client.raw = object()  # type: ignore[attr-defined]


def test_every_client_command_takes_a_typed_key_first() -> None:
    """No public command may take a plain ``str`` in the key position."""
    offenders: list[str] = []
    for name, fn in inspect.getmembers(TenantRedis, callable):
        if name.startswith("_") or name in {"key", "group", "match", "pipeline"}:
            continue
        params = [p for p in inspect.signature(fn).parameters.values() if p.name != "self"]
        if not params:
            continue
        first = params[0]
        annotation = str(first.annotation)
        if "TenantKey" not in annotation and "ConsumerGroup" not in annotation \
                and "ScanPattern" not in annotation and "Mapping" not in annotation:
            offenders.append(f"{name}({first.name}: {annotation})")
    assert not offenders, (
        "Client command(s) whose key argument is not a typed tenant key:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 5b. The binary pool (WS-17 EM-T2b) — same wrapper, bytes in and out.
# ---------------------------------------------------------------------------

def test_binary_client_is_the_same_wrapper_over_a_second_small_pool(monkeypatch) -> None:
    """``get_tenant_redis(binary=True)`` must not decode replies.

    The main pool decodes as UTF-8, so an attachment read through it either
    raises or (caught) becomes a silent cache miss. The binary pool is a second
    pool, small, under the SAME wrapper class, so the key discipline holds.
    """
    made: list[dict[str, Any]] = []

    def _from_url(url: str, **kwargs: Any) -> object:
        made.append(kwargs)
        return object()

    monkeypatch.setattr(tr.aioredis, "from_url", _from_url)
    tr.reset_pool_for_tests()
    try:
        binary = tr.get_tenant_redis(binary=True)
        text_client = tr.get_tenant_redis()
        again = tr.get_tenant_redis(binary=True)

        assert type(binary) is TenantRedis and type(text_client) is TenantRedis
        assert len(made) == 2, "each pool is built once, then reused"
        binary_kwargs, text_kwargs = made
        assert binary_kwargs["decode_responses"] is False
        assert binary_kwargs["max_connections"] == tr.BINARY_POOL_MAX_CONNECTIONS
        assert tr.BINARY_POOL_MAX_CONNECTIONS <= 4
        assert text_kwargs["decode_responses"] is True
        assert binary._client is not text_client._client
        assert again._client is binary._client

        tr.reset_pool_for_tests()
        assert tr._POOL is None and tr._BINARY_POOL is None
    finally:
        tr.reset_pool_for_tests()


# ---------------------------------------------------------------------------
# 6. The ratchet — a direct redis client outside the wrapper fails the build.
# ---------------------------------------------------------------------------
#
# The ticket's done-when. Source-level on purpose, for the same reason
# ``test_db_engine_seam.py`` is: the failure mode is a NEW module reaching for
# redis-py directly, which no runtime assertion sees until that module is in
# production holding another tenant's key.
#
# MT-1e deliberately migrates nothing — converting ~20 call sites with their own
# TTL and liveness semantics is a separate, riskier change. So the allow-list
# below IS the follow-up ticket's checklist. Each entry names what must move.
# Delete the entry when the module is converted; `test_no_stale_allowlist_entries`
# fails if you leave a dead one behind, so the list can only shrink.

_ALLOWED_DIRECT_REDIS: dict[str, str] = {
    "packages/acb_common/acb_common/tenant_redis.py":
        "the wrapper itself — the one place redis-py is reached",

    # ── MT-1e follow-up: convert these to acb_common.tenant_redis ──
    "packages/acb_common/acb_common/activity.py":
        "FOLLOW-UP: cc:activity, cc:activity:live:{run_id}, cc:cost:{day}; pooled "
        "client at :66 plus two one-shot clients at :226/:317",
    "apps/services/orchestrator/orchestrator/stream_relay.py":
        "FOLLOW-UP: cc:stream, cc:active, cc:runactor, cc:runsource, cc:runfloor, "
        "cc:control, cc:ctrl-ack — 31 key call sites, the largest conversion",
    "apps/services/gateway/gateway/room_stream.py":
        "FOLLOW-UP: cc:room:{tid} stream + cc:presence:{tid} hash",
    "apps/services/gateway/gateway/routes/chat.py":
        "FOLLOW-UP: inline client at :700 and the cc:active:* SCAN at :707 — the "
        "scan is a cross-tenant enumeration the moment a second tenant exists",
    "apps/services/ingestion/ingestion/queue.py":
        "FOLLOW-UP: ingestion:{clickup,zoho,gmail,dlq} streams (sync client)",
    "apps/services/ingestion/ingestion/consumer.py":
        "FOLLOW-UP: same streams read side + the SHARED 'cc-ingest' consumer group "
        "at :95 — §1.9 requires a group per tenant",
}


def _python_files() -> list[Path]:
    roots = [_REPO / "apps", _REPO / "packages"]
    out: list[Path] = []
    for root in roots:
        out.extend(
            p for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and ".venv" not in p.parts
        )
    return out


def _imports_redis_package(path: Path) -> bool:
    """True if the file imports the ``redis`` package itself.

    Parsed rather than grepped, exactly as ``test_db_engine_seam.py`` explains:
    half these modules discuss Redis in prose, and a substring match would read
    the documentation as a violation. Import-based rather than call-based
    because every way to build a client (``from_url``, ``Redis()``,
    ``ConnectionPool``) starts with importing the package, so one check covers
    them all — and ``agent_framework.redis`` correctly does not match, since its
    root module is not ``redis``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "redis" for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] == "redis"
        ):
            return True
    return False


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO)).replace("\\", "/")


def test_no_direct_redis_client_outside_the_wrapper() -> None:
    offenders = sorted(_rel(p) for p in _python_files() if _imports_redis_package(p))
    unexpected = [p for p in offenders if p not in _ALLOWED_DIRECT_REDIS]
    assert not unexpected, (
        "New direct redis-py import(s) — Redis is reached only through the tenant "
        "wrapper (saas_multitenancy.md §0.9.4, MT-1e):\n  "
        + "\n  ".join(unexpected)
        + "\n\nUse acb_common.tenant_redis.get_tenant_redis() and build keys with "
          "key(namespace, *parts). A hand-written key string carries no tenant, and "
          "Redis has no row-level policy behind it to catch that."
    )


def test_no_stale_allowlist_entries() -> None:
    """A converted module must leave the list, so the list can only shrink.

    Without this the allow-list drifts into blanket permission, and the next
    reader cannot tell which entries still describe real un-migrated code.
    """
    offenders = {_rel(p) for p in _python_files() if _imports_redis_package(p)}
    stale = sorted(set(_ALLOWED_DIRECT_REDIS) - offenders)
    assert not stale, f"Allow-list entries that no longer import redis: {stale}"


def test_allowlist_entries_say_why() -> None:
    """Every exemption carries a reason, and every follow-up says FOLLOW-UP."""
    for path, reason in _ALLOWED_DIRECT_REDIS.items():
        assert len(reason) > 20, f"{path} has no real reason recorded"
    follow_ups = [p for p, r in _ALLOWED_DIRECT_REDIS.items() if r.startswith("FOLLOW-UP")]
    assert len(follow_ups) == len(_ALLOWED_DIRECT_REDIS) - 1, (
        "Exactly one entry (the wrapper) may be a permanent exemption; every other "
        "entry is an un-migrated call site and must be marked FOLLOW-UP."
    )


# ---------------------------------------------------------------------------
# 7. The second ratchet — no hand-written ``cc:`` key literal.
# ---------------------------------------------------------------------------
#
# The import ratchet above catches a module that opens its own connection. It
# does NOT catch a module that builds an untenanted key and hands it to someone
# else's client — which is exactly what ``steer.py`` does today (``cc:steer``
# keys, ``stream_relay``'s client) and what ``chat.py`` does with its
# ``cc:active:*`` SCAN. Those are the same leak with an extra hop, so they get
# their own ratchet and their own follow-up entries.

_ALLOWED_CC_LITERALS: dict[str, str] = {
    "packages/acb_common/acb_common/activity.py":
        "FOLLOW-UP: ACTIVITY_STREAM / LIVE_PREFIX / COST_PREFIX (:45-47)",
    "apps/services/orchestrator/orchestrator/stream_relay.py":
        "FOLLOW-UP: seven PREFIX constants (:53, :54, :65, :71, :76, :557, :558)",
    "apps/services/orchestrator/orchestrator/steer.py":
        "FOLLOW-UP: STEER_PREFIX (:55); borrows stream_relay's client, so the "
        "import ratchet cannot see it",
    "apps/services/gateway/gateway/room_stream.py":
        "FOLLOW-UP: ROOM_STREAM_PREFIX / PRESENCE_PREFIX (:39-40)",
    "apps/services/gateway/gateway/routes/chat.py":
        "FOLLOW-UP: the cc:active:* SCAN match and removeprefix (:707-711) — a "
        "cross-tenant session enumeration once a second tenant exists",
}


def _cc_key_literals(path: Path) -> list[str]:
    """String constants that ARE a ``cc:`` key, excluding docstrings.

    Docstrings are skipped by node identity rather than by heuristic: a module
    that explains its keys in prose is documenting, not constructing. A literal
    that *starts* with ``cc:`` in any other position is a key being built.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value.startswith(f"{KEY_ROOT}:")
        and id(n) not in docstrings
    ]


def test_no_hand_written_cc_key_literals() -> None:
    offenders = sorted(_rel(p) for p in _python_files() if _cc_key_literals(p))
    unexpected = [p for p in offenders if p not in _ALLOWED_CC_LITERALS]
    assert not unexpected, (
        "Hand-written 'cc:' key literal(s) — keys are built by "
        "acb_common.tenant_redis.key(namespace, *parts), which cannot omit the "
        "tenant (saas_multitenancy.md §0.9.4, MT-1e):\n  " + "\n  ".join(unexpected)
    )


def test_no_stale_cc_literal_allowlist_entries() -> None:
    offenders = {_rel(p) for p in _python_files() if _cc_key_literals(p)}
    stale = sorted(set(_ALLOWED_CC_LITERALS) - offenders)
    assert not stale, f"Allow-list entries that no longer build a 'cc:' key: {stale}"


def test_the_cc_root_is_written_in_exactly_one_place() -> None:
    """Nothing but the wrapper may define the ``cc:`` root for a NEW key.

    Narrower than it looks and deliberately so: this pins that ``KEY_ROOT`` is
    the only literal the wrapper itself uses to build a key, so a future edit
    cannot quietly add a second, unprefixed builder beside it.
    """
    source = (_REPO / "packages/acb_common/acb_common/tenant_redis.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("cc:")
    ]
    assert literals == [], (
        f"tenant_redis.py contains a hand-written 'cc:' key literal: {literals}. "
        "The root belongs in KEY_ROOT and nowhere else."
    )
