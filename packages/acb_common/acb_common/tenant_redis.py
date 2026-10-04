"""MT-1e — the tenant-prefixed Redis client. A key that carries no tenant cannot be built.

``saas_multitenancy.md`` §1.9 opens with the sentence this module exists for:

    "Postgres RLS protects Postgres. These do not run on Postgres."

Redis is the first row of that table. Every ``cc:*`` key in the tree today is
**deployment-global**: ``cc:activity`` is one stream for everyone,
``cc:room:{thread_id}`` is keyed by a thread id that is unique but not tenanted,
``cc:cost:{date}`` folds every customer's spend into one hash. Under one tenant
that is fine. Under two it is a cross-customer read with no server-side check in
front of it — Redis has no row-level policy to fall back on, so whatever the key
string says *is* the isolation.

§0.9.4 states the required shape, and states **why it is a client and not a
convention**:

    "Redis stays, but tenant prefixing is enforced by a wrapper client, not by
     convention. A convention is a thing people forget; a client that cannot
     construct an unprefixed key is not."

So the design goal here is not "make prefixing easy". It is: **make the wrong
key inexpressible.** Three mechanics get that:

1.  :class:`TenantKey` is the only thing the client's commands accept. A ``str``
    is rejected with ``TypeError`` — there is no method on this client that
    takes a key you typed yourself.
2.  :class:`TenantKey` cannot be built without a bound tenant. Its validation
    runs in ``__post_init__``, so constructing one directly is not a way around
    :func:`key` — it is the same check.
3.  The tenant comes from a ``ContextVar`` and **fails closed**: unbound raises
    :class:`TenantNotBound`. It never falls back to a global key, because a
    silent global-key fallback is exactly the bug this ticket exists to make
    impossible (compare §1.9's background-jobs row: "a job that forgets doesn't
    leak one row; it leaks unbounded").

The ContextVar mirrors ``acb_skills/integrations.py``'s ``bind_run_credentials``
/ ``_RUN_CREDENTIALS`` (MT-0a), deliberately and for the same reason it was
chosen there: a ContextVar is per-asyncio-task and is copied into tasks created
from the binding context, so two overlapping runs each see only their own value
with no shared window at all. Anything process-global — a module attribute, an
env var — is a leak the moment two tenants run concurrently in one process, and
this process runs model-generated tool calls over ingested email.

Key shape (§1.9: *"Prefix every key ``cc:<org_id>:…``"*)::

    cc:<organization_id>:<namespace>[:<part>...]

Consumer groups (§1.9: *"separate consumer groups per tenant on the Streams
bus"*) get the same treatment: :class:`ConsumerGroup` is built by :func:`group`
and carries the tenant, and every group command on this client demands one.
A per-tenant stream key already separates the data; a per-tenant group name is
what stops one tenant's consumer from ACKing entries out of another's PEL if a
stream key is ever shared or replayed.

Scope of this ticket — read before extending
--------------------------------------------
This module ships the client, its tests and its ratchet. It deliberately
**converts no existing call site**: ~20 of them across four services, each with
its own liveness/TTL semantics, is a separate and much riskier change and must
not ride along on the change that introduces the mechanism.

The ratchet that enforces adoption lives in ``tests/unit/test_tenant_redis.py``:
it fails the build on any *new* direct ``redis`` client, and carries today's
sites in an allow-list where each entry names the follow-up conversion. That
allow-list is the migration checklist — the follow-up ticket is done when it is
empty. Migration path, per call site:

1.  Bind the tenant at the entry point that already knows it (request handler,
    job record, run start) with :func:`organization_scope` or
    :func:`bind_organization`. **Do not** derive it inside the Redis call — §1.5
    binds the tenant from the authenticated session, never from a header or a
    body field.
2.  Replace the module's ``_get_client()`` with :func:`get_tenant_redis`.
3.  Replace each ``_foo_key(x)`` helper's body with ``key("foo", x)``. The
    prefix constant (``FOO_PREFIX = "cc:foo"``) becomes the namespace ``"foo"``
    and the ``cc:`` root is no longer written by hand anywhere.
4.  Delete the allow-list entry in ``test_tenant_redis.py``. The stale-entry
    test proves the list shrinks rather than silently becoming permission.

Deployed keys written before a conversion are orphaned by it, by design: every
key here is a cache, a presence flag or a bounded live stream (the durable
record is Postgres), so the conversion is a cache-cold event, not a data
migration. Do not write a dual-read shim — a reader that falls back to the
unprefixed key is a reader that crosses the tenant boundary.

Spec: ``saas_multitenancy.md`` §0.9.4, §1.9 · MT-1e · D15.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis

from acb_common.settings import get_settings

__all__ = [
    "KEY_ROOT",
    "ConsumerGroup",
    "ScanPattern",
    "TenantKey",
    "TenantMismatch",
    "TenantNotBound",
    "TenantRedis",
    "bind_organization",
    "current_organization",
    "get_tenant_redis",
    "group",
    "key",
    "match",
    "organization_bound",
    "organization_scope",
    "release_organization",
]

#: The one place the ``cc:`` root is written. Nothing else in the codebase
#: should ever type it again — that is the point of the module.
KEY_ROOT = "cc"


# ── Errors ───────────────────────────────────────────────────────────────────

class TenantNotBound(RuntimeError):
    """No tenant is bound on this context, so no key can be built.

    Raised rather than defaulting to a global key. §1.9's fail-closed rule: the
    caller that forgot to bind must break loudly here, in its own test run,
    rather than quietly writing into a key every other tenant also reads.
    """


class TenantMismatch(RuntimeError):
    """A key built for one tenant was used while another was bound.

    The realistic path to this is a key captured in a closure or a dict that
    outlives its binding — e.g. a key built during a request and used later on a
    background task that rebound to another tenant. Caught rather than executed.
    """


# ── The bound tenant (ContextVar; mirrors acb_skills.integrations, MT-0a) ─────

#: ``None`` default rather than a sentinel org id — there is no such thing as a
#: default tenant, and a mutable/plausible default is how the fallback this
#: module forbids gets reintroduced. Readers go through
#: :func:`current_organization`, which turns the ``None`` into a raise.
_ORGANIZATION_ID: ContextVar[str | None] = ContextVar(
    "acb_organization_id", default=None,
)

#: D15: the tenant is ``organization_id``. Constrained to what is safe in a
#: Redis key *and* unambiguous inside one: no ``:`` (it would forge a second
#: prefix segment) and no glob metacharacter (it would widen a SCAN pattern
#: across tenants). UUIDs and slugs both pass.
_ORG_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

#: Namespaces are the vocabulary the codebase already uses — ``activity``,
#: ``room``, ``presence``, ``stream``, ``active``, ``runactor``, ``ctrl-ack``, …
#: A namespace may not contain ``:``; nesting is expressed with parts, so that
#: ``cc:<org>:activity:live:<run>`` is ``key("activity", "live", run_id)`` and
#: the segment count is a property of the call, not of a string someone typed.
_NAMESPACE_RE = re.compile(r"\A[a-z0-9][a-z0-9_.-]{0,31}\Z")


def bind_organization(organization_id: str) -> Token[str | None]:
    """Bind *organization_id* as this context's tenant. Returns a reset token.

    The caller **must** hand the token to :func:`release_organization` at
    teardown, or use :func:`organization_scope`, which does it for you.

    Bind at the boundary that already established the tenant — the authenticated
    session (§1.5) or a job record's explicit ``organization_id`` (§1.9). Never
    from a header, query parameter or body field: MT-1f makes ignoring those a
    build-failing test, and a Redis call site is not the place to relitigate it.
    """
    org = _validated_org(organization_id)
    return _ORGANIZATION_ID.set(org)


def release_organization(token: Token[str | None] | None) -> None:
    """Undo :func:`bind_organization`. Never raises.

    ``ContextVar.reset`` rejects a token created in a *different* Context, which
    a teardown that hopped tasks would hit. Falling back to an explicit unbind
    keeps the failure closed — the tenant is gone either way — rather than
    leaving the previous tenant bound because the reset raised. Same reasoning,
    same shape as ``acb_skills.integrations.release_run_credentials``.
    """
    if token is None:
        return
    try:
        _ORGANIZATION_ID.reset(token)
    except (ValueError, RuntimeError):
        _ORGANIZATION_ID.set(None)


@contextmanager
def organization_scope(organization_id: str):
    """``with organization_scope(org): …`` — bind for a block, always release."""
    token = bind_organization(organization_id)
    try:
        yield organization_id
    finally:
        release_organization(token)


def current_organization() -> str:
    """The bound tenant, or raise :class:`TenantNotBound`.

    There is no ``default=`` parameter and there will not be one. A default is a
    global key with extra steps.
    """
    org = _ORGANIZATION_ID.get()
    if org is None:
        raise TenantNotBound(
            "No organization_id bound on this context. Redis keys are tenant-scoped "
            "(saas_multitenancy.md §1.9); bind the tenant at the request/job boundary "
            "with acb_common.tenant_redis.organization_scope(org_id) before touching "
            "Redis. There is deliberately no global-key fallback."
        )
    return org


def organization_bound() -> bool:
    """True when a tenant is bound. For guards that must not raise."""
    return _ORGANIZATION_ID.get() is not None


def _validated_org(organization_id: Any) -> str:
    if not isinstance(organization_id, str) or not _ORG_ID_RE.match(organization_id):
        raise ValueError(
            f"Invalid organization_id for a Redis key: {organization_id!r}. "
            "Expected 1-64 chars of [A-Za-z0-9_.-] starting alphanumeric — no ':' "
            "(it would forge a prefix segment) and no glob metacharacters (they "
            "would widen a SCAN across tenants)."
        )
    return organization_id


# ── The key types: the only things the client accepts ────────────────────────

def _validated_parts(parts: Sequence[Any]) -> tuple[str, ...]:
    """Validate key parts. Empty/whitespace/NUL rejected; ``:`` allowed.

    ``:`` is allowed in a *part* because real values already contain it — the
    run context's ``instance`` is ``u:<email>`` / ``t:<team>`` — and because a
    part cannot escape the tenant prefix: the prefix is prepended, never
    interpolated, and ``organization_id`` itself cannot contain ``:``. So the
    worst a colon in a part can do is collide with another key *inside the same
    tenant*, which is a correctness question, not a tenancy boundary.
    """
    out: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            raise TypeError(f"Redis key parts must be str, got {type(part).__name__}: {part!r}")
        if not part or part.strip() != part or "\x00" in part:
            raise ValueError(
                f"Invalid Redis key part {part!r}: must be non-empty, NUL-free and "
                "free of leading/trailing whitespace."
            )
        out.append(part)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class TenantKey:
    """A Redis key that provably carries a tenant. The client accepts nothing else.

    Direct construction is not a bypass: ``__post_init__`` runs the same checks
    :func:`key` would, including that the tenant matches the bound one. There is
    no constructor, classmethod or attribute on this type that yields a key
    without the ``cc:<org_id>:`` prefix — :attr:`value` builds it every time from
    validated fields rather than storing a string someone could have supplied.
    """

    organization_id: str
    namespace: str
    parts: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "organization_id", _validated_org(self.organization_id))
        if not isinstance(self.namespace, str) or not _NAMESPACE_RE.match(self.namespace):
            raise ValueError(
                f"Invalid Redis namespace {self.namespace!r}: expected 1-32 chars of "
                "[a-z0-9_.-] starting alphanumeric. Nest with parts, not with ':'."
            )
        object.__setattr__(self, "parts", _validated_parts(self.parts))
        # Fail closed even on direct construction: a key for a tenant nobody
        # bound is exactly the object this module exists to make unobtainable.
        bound = current_organization()
        if self.organization_id != bound:
            raise TenantMismatch(
                f"Cannot build a key for organization {self.organization_id!r} while "
                f"{bound!r} is bound. Rebind with organization_scope() instead."
            )

    @property
    def value(self) -> str:
        """The wire key: ``cc:<org_id>:<namespace>[:<part>...]``."""
        return ":".join((KEY_ROOT, self.organization_id, self.namespace, *self.parts))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ConsumerGroup:
    """A per-tenant Streams consumer group name (§1.9).

    Built by :func:`group`. Every group command on :class:`TenantRedis` demands
    one, so ``XGROUP CREATE``/``XREADGROUP``/``XACK`` cannot be issued with a
    shared group name — one tenant's consumer can never ACK entries out of
    another tenant's pending list.
    """

    organization_id: str
    base: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "organization_id", _validated_org(self.organization_id))
        if not isinstance(self.base, str) or not _NAMESPACE_RE.match(self.base):
            raise ValueError(
                f"Invalid consumer-group base {self.base!r}: expected 1-32 chars of "
                "[a-z0-9_.-] starting alphanumeric."
            )
        bound = current_organization()
        if self.organization_id != bound:
            raise TenantMismatch(
                f"Cannot build a consumer group for {self.organization_id!r} while "
                f"{bound!r} is bound."
            )

    @property
    def value(self) -> str:
        return f"{self.base}:{self.organization_id}"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ScanPattern:
    """A ``SCAN``/``KEYS`` match pattern that cannot outgrow its tenant.

    Built by :func:`match`. The fixed segments are glob-escaped and only the
    trailing wildcard is free, so ``cc:<org>:active:*`` is expressible and
    ``cc:*:active:*`` is not. This matters more than it looks: the one scan in
    the tree today (``chat.py`` listing active sessions via ``cc:active:*``)
    becomes a cross-tenant enumeration the moment a second tenant exists.
    """

    organization_id: str
    namespace: str
    parts: tuple[str, ...]
    suffix: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "organization_id", _validated_org(self.organization_id))
        if not isinstance(self.namespace, str) or not _NAMESPACE_RE.match(self.namespace):
            raise ValueError(f"Invalid Redis namespace {self.namespace!r}")
        object.__setattr__(self, "parts", _validated_parts(self.parts))
        bound = current_organization()
        if self.organization_id != bound:
            raise TenantMismatch(
                f"Cannot build a scan pattern for {self.organization_id!r} while "
                f"{bound!r} is bound."
            )

    @property
    def value(self) -> str:
        fixed = ":".join(
            (KEY_ROOT, self.organization_id, self.namespace, *(_glob_escape(p) for p in self.parts))
        )
        return f"{fixed}:{self.suffix}" if self.suffix else fixed

    def __str__(self) -> str:
        return self.value


def _glob_escape(part: str) -> str:
    """Escape Redis glob metacharacters so a fixed segment stays fixed."""
    out = []
    for ch in part:
        if ch in "*?[]\\":
            out.append("\\")
        out.append(ch)
    return "".join(out)


# ── Public builders ──────────────────────────────────────────────────────────

def key(namespace: str, *parts: str) -> TenantKey:
    """Build ``cc:<bound_org>:<namespace>[:<part>...]``.

    The only key builder there is. Raises :class:`TenantNotBound` when no tenant
    is bound — that raise is the feature.
    """
    return TenantKey(current_organization(), namespace, tuple(parts))


def group(base: str) -> ConsumerGroup:
    """Build the per-tenant consumer group ``<base>:<bound_org>`` (§1.9)."""
    return ConsumerGroup(current_organization(), base)


def match(namespace: str, *parts: str, suffix: str = "*") -> ScanPattern:
    """Build a SCAN pattern rooted at the bound tenant's prefix."""
    return ScanPattern(current_organization(), namespace, tuple(parts), suffix)


# ── The client ───────────────────────────────────────────────────────────────

class TenantRedis:
    """The wrapper client. Every command takes a :class:`TenantKey`, never a ``str``.

    The command surface is deliberately *enumerated*, not proxied through
    ``__getattr__``: a catch-all proxy would forward ``client.get("cc:activity")``
    straight to redis-py and hand back exactly the untenanted access this module
    removes. Adding a command means adding a method here, which is a two-line
    diff and a moment's thought about the key argument — the right price.

    The underlying redis-py client is private on purpose. Reaching for it is the
    move ``tests/unit/test_tenant_redis.py``'s ratchet exists to catch.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Any) -> None:
        self._client = client

    # -- key discipline -----------------------------------------------------

    @staticmethod
    def _raw(k: Any) -> str:
        """Unwrap a :class:`TenantKey`, refusing anything else. The choke point."""
        if not isinstance(k, TenantKey):
            raise TypeError(
                f"Redis commands take a TenantKey, not {type(k).__name__}. Build one with "
                "acb_common.tenant_redis.key(namespace, *parts) — a hand-written key "
                "string is the untenanted access this client exists to prevent "
                "(saas_multitenancy.md §0.9.4)."
            )
        bound = current_organization()
        if k.organization_id != bound:
            raise TenantMismatch(
                f"Key {k.value!r} belongs to organization {k.organization_id!r} but "
                f"{bound!r} is bound on this context."
            )
        return k.value

    @staticmethod
    def _raw_group(g: Any) -> str:
        if not isinstance(g, ConsumerGroup):
            raise TypeError(
                f"Consumer-group commands take a ConsumerGroup, not {type(g).__name__}. "
                "Build one with acb_common.tenant_redis.group(base) — §1.9 requires "
                "separate consumer groups per tenant."
            )
        bound = current_organization()
        if g.organization_id != bound:
            raise TenantMismatch(
                f"Consumer group {g.value!r} belongs to {g.organization_id!r} but "
                f"{bound!r} is bound on this context."
            )
        return g.value

    def _raw_streams(self, streams: Mapping[Any, Any]) -> dict[str, Any]:
        return {self._raw(k): v for k, v in streams.items()}

    # -- builders, so a caller needs only the client ------------------------

    def key(self, namespace: str, *parts: str) -> TenantKey:
        return key(namespace, *parts)

    def group(self, base: str) -> ConsumerGroup:
        return group(base)

    def match(self, namespace: str, *parts: str, suffix: str = "*") -> ScanPattern:
        return match(namespace, *parts, suffix=suffix)

    # -- strings / generic keys --------------------------------------------

    async def get(self, k: TenantKey) -> Any:
        return await self._client.get(self._raw(k))

    async def set(self, k: TenantKey, value: Any, **kwargs: Any) -> Any:
        return await self._client.set(self._raw(k), value, **kwargs)

    async def setex(self, k: TenantKey, seconds: int, value: Any) -> Any:
        return await self._client.setex(self._raw(k), seconds, value)

    async def delete(self, *keys: TenantKey) -> Any:
        return await self._client.delete(*(self._raw(k) for k in keys))

    async def exists(self, *keys: TenantKey) -> Any:
        return await self._client.exists(*(self._raw(k) for k in keys))

    async def expire(self, k: TenantKey, seconds: int) -> Any:
        return await self._client.expire(self._raw(k), seconds)

    async def ttl(self, k: TenantKey) -> Any:
        return await self._client.ttl(self._raw(k))

    async def incr(self, k: TenantKey, amount: int = 1) -> Any:
        return await self._client.incr(self._raw(k), amount)

    async def decrby(self, k: TenantKey, amount: int = 1) -> Any:
        return await self._client.decrby(self._raw(k), amount)

    # -- lists (steer signals are a durable list today) ---------------------

    async def rpush(self, k: TenantKey, *values: Any) -> Any:
        return await self._client.rpush(self._raw(k), *values)

    async def lrange(self, k: TenantKey, start: int, end: int) -> Any:
        return await self._client.lrange(self._raw(k), start, end)

    async def llen(self, k: TenantKey) -> Any:
        return await self._client.llen(self._raw(k))

    # -- hashes (cost rollups, room presence) -------------------------------

    async def hset(self, k: TenantKey, *args: Any, **kwargs: Any) -> Any:
        return await self._client.hset(self._raw(k), *args, **kwargs)

    async def hget(self, k: TenantKey, name: str) -> Any:
        return await self._client.hget(self._raw(k), name)

    async def hgetall(self, k: TenantKey) -> Any:
        return await self._client.hgetall(self._raw(k))

    async def hdel(self, k: TenantKey, *names: str) -> Any:
        return await self._client.hdel(self._raw(k), *names)

    async def hincrby(self, k: TenantKey, name: str, amount: int = 1) -> Any:
        return await self._client.hincrby(self._raw(k), name, amount)

    async def hincrbyfloat(self, k: TenantKey, name: str, amount: float = 1.0) -> Any:
        return await self._client.hincrbyfloat(self._raw(k), name, amount)

    # -- streams ------------------------------------------------------------

    async def xadd(self, k: TenantKey, fields: Mapping[str, Any], **kwargs: Any) -> Any:
        return await self._client.xadd(self._raw(k), fields, **kwargs)

    async def xlen(self, k: TenantKey) -> Any:
        return await self._client.xlen(self._raw(k))

    async def xrange(self, k: TenantKey, *args: Any, **kwargs: Any) -> Any:
        return await self._client.xrange(self._raw(k), *args, **kwargs)

    async def xrevrange(self, k: TenantKey, *args: Any, **kwargs: Any) -> Any:
        return await self._client.xrevrange(self._raw(k), *args, **kwargs)

    async def xread(self, streams: Mapping[TenantKey, Any], **kwargs: Any) -> Any:
        return await self._client.xread(self._raw_streams(streams), **kwargs)

    async def xdel(self, k: TenantKey, *ids: str) -> Any:
        return await self._client.xdel(self._raw(k), *ids)

    # -- streams, consumer groups (per tenant, §1.9) ------------------------

    async def xgroup_create(self, k: TenantKey, g: ConsumerGroup, **kwargs: Any) -> Any:
        return await self._client.xgroup_create(self._raw(k), self._raw_group(g), **kwargs)

    async def xreadgroup(
        self,
        g: ConsumerGroup,
        consumername: str,
        streams: Mapping[TenantKey, Any],
        **kwargs: Any,
    ) -> Any:
        return await self._client.xreadgroup(
            groupname=self._raw_group(g),
            consumername=consumername,
            streams=self._raw_streams(streams),
            **kwargs,
        )

    async def xack(self, k: TenantKey, g: ConsumerGroup, *ids: str) -> Any:
        return await self._client.xack(self._raw(k), self._raw_group(g), *ids)

    async def xpending(self, k: TenantKey, g: ConsumerGroup, **kwargs: Any) -> Any:
        return await self._client.xpending(self._raw(k), self._raw_group(g), **kwargs)

    async def xclaim(self, k: TenantKey, g: ConsumerGroup, *args: Any, **kwargs: Any) -> Any:
        return await self._client.xclaim(self._raw(k), self._raw_group(g), *args, **kwargs)

    # -- pub/sub (the cross-worker control bus) -----------------------------

    async def publish(self, channel: TenantKey, message: Any) -> Any:
        return await self._client.publish(self._raw(channel), message)

    async def subscribe(self, *channels: TenantKey) -> Any:
        """Return a redis-py pubsub already subscribed to tenant-scoped channels."""
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*(self._raw(c) for c in channels))
        return pubsub

    # -- scan ---------------------------------------------------------------

    async def scan_iter(self, pattern: ScanPattern, *, count: int = 200) -> AsyncIterator[str]:
        """Iterate keys under this tenant's prefix. Takes a pattern, never a string."""
        if not isinstance(pattern, ScanPattern):
            raise TypeError(
                f"scan_iter takes a ScanPattern, not {type(pattern).__name__}. Build one "
                "with match(namespace, *parts) — an untenanted match string turns a scan "
                "into a cross-tenant enumeration."
            )
        bound = current_organization()
        if pattern.organization_id != bound:
            raise TenantMismatch(
                f"Scan pattern belongs to {pattern.organization_id!r} but {bound!r} is bound."
            )
        async for raw in self._client.scan_iter(match=pattern.value, count=count):
            yield raw

    # -- pipeline -----------------------------------------------------------

    def pipeline(self, transaction: bool = False) -> TenantPipeline:
        return TenantPipeline(self._client.pipeline(transaction=transaction))


class TenantPipeline:
    """A pipeline with the same key discipline as :class:`TenantRedis`.

    Separate class rather than a shared mixin because redis-py's pipeline
    commands are *synchronous* (they buffer; only ``execute`` awaits), and a
    pipeline that quietly accepted a raw string would be the easiest hole to
    leave open — the cost rollup in ``activity.py`` is written entirely through
    one.
    """

    __slots__ = ("_pipe",)

    def __init__(self, pipe: Any) -> None:
        self._pipe = pipe

    def hincrbyfloat(self, k: TenantKey, name: str, amount: float) -> TenantPipeline:
        self._pipe.hincrbyfloat(TenantRedis._raw(k), name, amount)
        return self

    def hincrby(self, k: TenantKey, name: str, amount: int = 1) -> TenantPipeline:
        self._pipe.hincrby(TenantRedis._raw(k), name, amount)
        return self

    def hset(self, k: TenantKey, *args: Any, **kwargs: Any) -> TenantPipeline:
        self._pipe.hset(TenantRedis._raw(k), *args, **kwargs)
        return self

    def set(self, k: TenantKey, value: Any, **kwargs: Any) -> TenantPipeline:
        self._pipe.set(TenantRedis._raw(k), value, **kwargs)
        return self

    def delete(self, *keys: TenantKey) -> TenantPipeline:
        self._pipe.delete(*(TenantRedis._raw(k) for k in keys))
        return self

    def expire(self, k: TenantKey, seconds: int) -> TenantPipeline:
        self._pipe.expire(TenantRedis._raw(k), seconds)
        return self

    async def execute(self) -> Any:
        return await self._pipe.execute()


# ── Pooled client (one per process, like acb_common.db's engine) ─────────────

#: Process-wide and tenant-agnostic *by design*: the connection pool is shared,
#: the KEYS are what carry the tenant. A pool per tenant would multiply
#: connections by customer count for no isolation gain — Redis has no per-key
#: authorisation to enforce anyway, so isolation must come from the key, and
#: does. Mirrors ``activity.py``'s pooled client settings.
_POOL: Any = None

#: The second pool, for values that are bytes (WS-17 EM-T2b). The main pool
#: decodes every reply as UTF-8, so a binary value (an email attachment) either
#: raises on read or, caught, turns each cache read into a silent miss. This
#: pool returns ``bytes``. It is small because its one caller today is the
#: attachment cache, and it is the SAME wrapper over it — not a second client
#: class — so the key discipline is identical.
_BINARY_POOL: Any = None

#: Connection ceiling of the binary pool. One caller, short-lived commands.
BINARY_POOL_MAX_CONNECTIONS = 4


def get_tenant_redis(*, binary: bool = False) -> TenantRedis:
    """The shared tenant-aware client for this process.

    Does **not** require a tenant to be bound — the client is tenant-agnostic;
    every *key* it accepts is not. Binding is checked at key-build and at
    command time, which is where a missing binding is actually a bug.

    ``binary=True`` returns the same wrapper over a second, small pool with
    ``decode_responses=False``: replies come back as ``bytes``, unchanged.
    Use it only for values that are not text.
    """
    global _POOL, _BINARY_POOL
    if binary:
        if _BINARY_POOL is None:
            _BINARY_POOL = aioredis.from_url(
                get_settings().redis_url,
                decode_responses=False,
                max_connections=BINARY_POOL_MAX_CONNECTIONS,
                health_check_interval=30,
            )
        return TenantRedis(_BINARY_POOL)
    if _POOL is None:
        _POOL = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            max_connections=16,
            health_check_interval=30,
        )
    return TenantRedis(_POOL)


def reset_pool_for_tests() -> None:
    """Drop both cached pools. Tests only — there is no runtime reason to call it."""
    global _POOL, _BINARY_POOL
    _POOL = None
    _BINARY_POOL = None
