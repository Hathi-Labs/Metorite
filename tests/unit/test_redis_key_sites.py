"""Task 570 (H5, R5): every raw Redis key site is on a list, and the list only shrinks.

``CLAUDE.md`` §3 rule 3 (R5): "Redis keys go through the prefix wrapper."
The wrapper is ``acb_common.tenant_redis``: a key it builds carries the org,
``cc:<org>:<namespace>:...``. A key that a module types itself carries no
org, and Redis has no row policy behind it to catch that.

``tests/unit/test_tenant_redis.py`` holds two file-level ratchets: a direct
``redis`` import, and a hand-written ``cc:`` literal. Neither sees a module
that borrows a raw client from another module (``steer.py`` borrows
``stream_relay._get_client``), nor a key with another root (``session_mem:``).
This ratchet counts each SITE. The scanner is ``_redis_key_sites.py``, an
AST taint pass. A site is a raw client that a module builds, a command on a
raw client, or a raw client that goes to a third-party callee.

The allow-list is keyed by ``path::function``, with a count for each kind and
command, so an edit that moves a line does not fail it. Each entry has a
class and a reason:

* ``a``: the key holds tenant data, or a tenant id or email names it. It
  must move to the wrapper. ``slice`` names the slice that moves it.
* ``b``: the key is global on purpose (a process heartbeat, a deployment-level
  hash). The reason says why.
* ``c``: dead code, or a manual script that no service runs.

A NEW site fails with its file, its line and the wrapper to use. A site that
went away fails until its entry shrinks, so the list never turns into a
permission. Slice 1 moved ``gateway/room_stream.py`` and
``acb_memory/session_cache.py``. Their entries are gone.

The scanner does not see a function that takes a client as a plain parameter
when no caller in the tree passes it a raw client: it does not know the
parameter is a client. ``invalidate_session_memory`` had that shape. The
taint finds such a site when a caller first passes a raw client.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1e, appendix
"Raw Redis key sites (task 570)".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import NamedTuple

import pytest

from tests.unit import _redis_key_sites as scanner


class Allow(NamedTuple):
    """One allowed function: its class, its slice, its sites, and why."""

    cls: str
    slice: str
    sites: dict[str, int]
    reason: str


_SR = "apps/services/orchestrator/orchestrator/stream_relay.py"
_RL = "apps/services/orchestrator/orchestrator/run_liveness.py"
_ST = "apps/services/orchestrator/orchestrator/steer.py"
_IC = "apps/services/ingestion/ingestion/consumer.py"
_IQ = "apps/services/ingestion/ingestion/queue.py"
_AC = "packages/acb_common/acb_common/activity.py"

_RUN = (
    "cc:stream holds the run's events (the member's words and the answer). "
    "cc:active, cc:runactor (a member email), cc:runsource and cc:runfloor "
    "(member emails) are keyed by a thread id"
)
_OWNER = (
    "cc:run-owner and cc:recover are keyed by a thread id and sit beside "
    "cc:active, so they move with stream_relay's keys"
)
_STEER = "cc:steer holds a member's typed steer, keyed by a thread id"
_STOPPED = "cc:steer-stopped marks a member's Stop, keyed by a thread id"
_INGEST = (
    "the ingestion:{zoho,gmail,dlq} streams hold webhook payloads of a "
    "tenant, and the consumer group 'cc-ingest' is shared. No code maps a "
    "webhook to an org (apps/AGENTS.md, ingestion), so the move needs that "
    "mapping first"
)

#: Each raw site of today. Delete an entry, or lower a count, when its
#: sites move. ``test_no_stale_entries`` fails until you do.
ALLOWED: dict[str, Allow] = {
    # ── class a, slice 2: the run relay of the orchestrator ──────────────
    f"{_SR}::_get_client": Allow(
        "a", "2", {"connect:from_url": 1},
        "the raw pool of every stream_relay, steer and run_liveness key "
        "below. It goes when the last of them moves",
    ),
    f"{_SR}::push_event": Allow(
        "a", "2", {"key:xadd": 1, "key:expire": 1, "key:set": 1}, _RUN),
    f"{_SR}::replay_events": Allow("a", "2", {"key:xread": 1}, _RUN),
    f"{_SR}::subscribe_events": Allow(
        "a", "2", {"key:get": 1, "key:xread": 2, "key:exists": 1}, _RUN),
    f"{_SR}::mark_active": Allow("a", "2", {"key:delete": 4, "key:set": 4}, _RUN),
    f"{_SR}::get_run_actor": Allow("a", "2", {"key:get": 1}, _RUN),
    f"{_SR}::get_run_source": Allow("a", "2", {"key:get": 1}, _RUN),
    f"{_SR}::get_run_floor": Allow("a", "2", {"key:get": 1}, _RUN),
    f"{_SR}::mark_inactive": Allow("a", "2", {"key:delete": 4, "key:expire": 1}, _RUN),
    f"{_SR}::keep_parked_stream": Allow("a", "2", {"key:expire": 1}, _RUN),
    f"{_SR}::parked_end_of": Allow("a", "2", {"key:xrevrange": 1, "key:xrange": 1}, _RUN),
    f"{_SR}::stream_head_ms": Allow("a", "2", {"key:xrange": 1}, _RUN),
    f"{_SR}::is_active": Allow("a", "2", {"key:get": 1}, _RUN),
    f"{_SR}::touch_active": Allow("a", "2", {"key:set": 1, "key:expire": 1}, _RUN),
    f"{_SR}::list_live_runs": Allow(
        "a", "2", {"key:get": 1},
        "reads cc:active of each run of the org's liveruns hash. The hash is "
        "on the wrapper. The cc:active read moves with stream_relay's keys",
    ),
    f"{_SR}::latest_step": Allow("a", "2", {"key:xrevrange": 1}, _RUN),
    f"{_SR}::stream_exists": Allow("a", "2", {"key:exists": 1}, _RUN),
    f"{_SR}::start_detached": Allow("a", "2", {"key:xdel": 1}, _RUN),
    f"{_SR}::publish_control": Allow(
        "a", "2", {"key:publish": 1},
        "cc:control:<thread> carries a control command, and a steer command "
        "holds the member's text",
    ),
    f"{_SR}::_control_listener": Allow(
        "a", "2", {"key:subscribe": 1, "key:unsubscribe": 1},
        "the subscribe side of cc:control:<thread>, which carries a "
        "member's steer text",
    ),
    # ── class b: the control ack holds no tenant data ────────────────────
    f"{_SR}::_write_control_ack": Allow(
        "b", "", {"key:set": 1},
        "cc:ctrl-ack:<ack id> is a random id of one control command and "
        "holds '1' for 60 s. It names no org, no member and no thread",
    ),
    f"{_SR}::wait_control_ack": Allow(
        "b", "", {"key:get": 1, "key:delete": 1},
        "the read side of cc:ctrl-ack:<ack id>: a random id that holds '1'",
    ),
    # ── run_liveness: process keys are b, run keys are a ─────────────────
    f"{_RL}::beat": Allow(
        "b", "", {"key:set": 1, "key:sadd": 1},
        "cc:instance:<id> and the cc:instances set belong to a PROCESS, "
        "not to a tenant (incident 2026-10-09)",
    ),
    f"{_RL}::stop_instance_heartbeat": Allow(
        "b", "", {"key:delete": 1}, "deletes the process heartbeat cc:instance:<id>"),
    f"{_RL}::record_instance_run": Allow(
        "b", "", {"key:hset": 1, "key:expire": 1},
        "cc:instance-runs:<id> holds the runs of MANY tenants that one "
        "process holds, so no single tenant can prefix it. It holds org and "
        "row ids, no member email",
    ),
    f"{_RL}::forget_instance_run": Allow(
        "b", "", {"key:hdel": 1}, "drops one run from cc:instance-runs:<id> (process-scoped)"),
    f"{_RL}::claim_run": Allow("a", "2", {"key:set": 1, "key:delete": 1}, _OWNER),
    f"{_RL}::release_run": Allow("a", "2", {"key:delete": 1}, _OWNER),
    f"{_RL}::run_liveness": Allow(
        "a", "2", {"key:get": 2, "key:exists": 1},
        f"{_OWNER}. The exists of cc:instance:<id> is process-scoped",
    ),
    f"{_RL}::claim_recovery": Allow("a", "2", {"key:set": 1}, _OWNER),
    f"{_RL}::release_recovery": Allow("a", "2", {"key:get": 1, "key:delete": 1}, _OWNER),
    f"{_RL}::_keep_claim": Allow("a", "2", {"key:get": 1, "key:expire": 1}, _OWNER),
    f"{_RL}::_dead_run_snapshot": Allow(
        "a", "2", {"key:get": 1, "key:hget": 1},
        f"{_OWNER}. The hget reads cc:instance-runs:<id> (process-scoped)",
    ),
    f"{_RL}::recovery_in_progress": Allow("a", "2", {"key:exists": 1}, _OWNER),
    f"{_RL}::interrupt_run": Allow(
        "a", "2", {"key:get": 1, "key:hget": 1, "key:hdel": 1},
        f"{_OWNER}. The hash reads cc:instance-runs:<id> (process-scoped)",
    ),
    f"{_RL}::sweep_dead_instances": Allow(
        "a", "2",
        {"key:smembers": 1, "key:exists": 1, "key:set": 1, "key:hgetall": 1,
         "key:get": 2, "key:delete": 1, "key:srem": 1},
        "the sweep of dead processes. Its process keys are b, and its reads "
        "of cc:run-owner and cc:active (keyed by a thread id) move in slice 2",
    ),
    "apps/services/orchestrator/orchestrator/pending_ask.py::_run_facts": Allow(
        "b", "", {"key:hget": 1},
        "reads this process's cc:instance-runs:<id>, a process-scoped hash",
    ),
    # ── steer: class a, slice 2 (borrows stream_relay's raw client) ──────
    f"{_ST}::mark_stopped": Allow("a", "2", {"key:set": 1}, _STOPPED),
    f"{_ST}::was_stopped": Allow("a", "2", {"key:get": 1}, _STOPPED),
    f"{_ST}::clear_stopped": Allow("a", "2", {"key:delete": 1}, _STOPPED),
    f"{_ST}::persist_signal": Allow(
        "a", "2", {"key:rpush": 1, "key:ltrim": 1, "key:expire": 1}, _STEER),
    f"{_ST}::take_pending_steers": Allow(
        "a", "2", {"key:rename": 1, "key:lrange": 1, "key:delete": 1}, _STEER),
    f"{_ST}::has_pending_steers": Allow("a", "2", {"key:lrange": 1}, _STEER),
    f"{_ST}::discard_signal": Allow("a", "2", {"key:lrange": 1, "key:lrem": 1}, _STEER),
    # ── ingestion: class a, but the move needs an org mapping first ──────
    f"{_IC}::_get_client": Allow("a", "decision", {"connect:from_url": 1}, _INGEST),
    f"{_IC}::_ensure_groups": Allow("a", "decision", {"key:xgroup_create": 1}, _INGEST),
    f"{_IC}::_dispatch_entry": Allow("a", "decision", {"key:xack": 1}, _INGEST),
    f"{_IC}::_drain_once": Allow("a", "decision", {"key:xreadgroup": 1}, _INGEST),
    f"{_IQ}::_client": Allow("a", "decision", {"connect:from_url": 1}, _INGEST),
    f"{_IQ}::enqueue": Allow("a", "decision", {"key:xadd": 1}, _INGEST),
    f"{_IQ}::enqueue_dlq": Allow("a", "decision", {"key:xadd": 1}, _INGEST),
    # ── activity: class b (H-201e) ───────────────────────────────────────
    f"{_AC}::_get_client": Allow(
        "b", "", {"connect:from_url": 1},
        "the pooled client writes ONE deployment-level hash, "
        "cc:_unattributed:cost:<day>: priced calls with no tenant. No tenant "
        "route reads it (H-201e)",
    ),
    f"{_AC}::_record_cost": Allow(
        "b", "", {"key:hincrbyfloat": 5, "key:hincrby": 7, "key:expire": 1},
        "the pipeline of the cost rollup. A tenant call passes the wrapper "
        "and a TenantKey. Only the unattributed call passes the raw client, "
        "with cc:_unattributed:cost:<day> (H-201e)",
    ),
    f"{_AC}::publish_activity._one_shot": Allow(
        "b", "", {"connect:from_url": 1},
        "a one-shot client of a sync worker. It is wrapped in TenantRedis at "
        "once, so every key goes through the wrapper",
    ),
    f"{_AC}::refresh_run_presence._one_shot": Allow(
        "b", "", {"connect:from_url": 1},
        "a one-shot client of a sync worker. It is wrapped in TenantRedis at "
        "once, so every key goes through the wrapper",
    ),
    # ── class c: dead code and manual scripts ────────────────────────────
    "apps/services/orchestrator/orchestrator/agents.py::build_orchestrator_agent": Allow(
        "c", "", {"connect:redis_url": 1},
        "RedisHistoryProvider keys chat history outside the wrapper. Only "
        "with_history=True reaches it, and no caller passes that. A revival "
        "needs its own decision (saas_multitenancy.md MT-1e)",
    ),
    "scripts/_test_reconnect_e2e.py::main": Allow(
        "c", "", {"connect:from_url": 1, "key:get": 3, "key:xlen": 2},
        "a manual end-to-end script against a local gateway. No service, "
        "job or CI step runs it",
    ),
    "scripts/check_infra.py::check_redis.probe": Allow(
        "b", "", {"connect:from_url": 1},
        "a PING health probe of the local stack. It names no key",
    ),
    "scripts/check_infra.py::main": Allow(
        "b", "", {"connect:redis_url": 1},
        "hands redis_url to the PING probe above. It names no key",
    ),
}

_WRAPPER_HINT = (
    "Reach Redis through acb_common.tenant_redis: get_tenant_redis(), then "
    "key(namespace, *parts) under organization_scope(org), with the org of "
    "the session or the job record (saas_multitenancy.md §0.9.4, R5)."
)


@pytest.fixture(scope="module")
def tree_sites() -> list[scanner.Site]:
    """ONE scan of the tree for this module. It takes some seconds."""
    return scanner.scan()


def violations(sites: list[scanner.Site], allowed: dict[str, Allow]) -> list[str]:
    """Each site over its allowance, and each allowance over its sites."""
    seen: dict[str, Counter[str]] = defaultdict(Counter)
    where: dict[tuple[str, str], list[scanner.Site]] = defaultdict(list)
    for site in sites:
        tag = f"{site.kind}:{site.method}"
        seen[site.where][tag] += 1
        where[(site.where, tag)].append(site)
    out: list[str] = []
    for fn, counts in sorted(seen.items()):
        allow = allowed.get(fn)
        for tag, n in sorted(counts.items()):
            limit = allow.sites.get(tag, 0) if allow else 0
            if n > limit:
                for site in where[(fn, tag)]:
                    out.append(
                        f"NEW raw Redis site {site.path}:{site.line} in {site.function} "
                        f"({site.kind} {site.method}): {site.text}\n    {_WRAPPER_HINT}"
                    )
            elif n < limit:
                out.append(
                    f"STALE allowance {fn}: {tag} allows {limit}, the tree has {n}. "
                    "Lower the count in ALLOWED."
                )
    for fn, allow in sorted(allowed.items()):
        for tag, limit in sorted(allow.sites.items()):
            if seen.get(fn, Counter())[tag] == 0 and limit:
                out.append(f"STALE allowance {fn}: {tag} has no site. Delete it from ALLOWED.")
    return out


# ---------------------------------------------------------------------------
# 1. The ratchet over the tree.
# ---------------------------------------------------------------------------


def test_no_raw_redis_site_outside_the_allow_list(tree_sites) -> None:
    bad = [v for v in violations(tree_sites, ALLOWED) if v.startswith("NEW")]
    assert not bad, "\n".join(bad)


def test_no_stale_entries(tree_sites) -> None:
    """A moved site must leave the list, so the list can only shrink."""
    stale = [v for v in violations(tree_sites, ALLOWED) if v.startswith("STALE")]
    assert not stale, "\n".join(stale)


def test_each_entry_has_a_class_a_slice_and_a_reason() -> None:
    for fn, allow in ALLOWED.items():
        assert "::" in fn, fn
        assert allow.cls in ("a", "b", "c"), (fn, allow.cls)
        assert len(allow.reason) > 30, f"{fn} has no real reason"
        assert allow.sites and all(n > 0 for n in allow.sites.values()), fn
        if allow.cls == "a":
            assert allow.slice in ("2", "decision"), (
                f"{fn}: a class a site names the slice that moves it"
            )
        else:
            assert allow.slice == "", f"{fn}: only class a has a slice"


def test_the_moved_modules_have_no_raw_site(tree_sites) -> None:
    """Slice 1 moved these two modules. A raw site in either is a regression."""
    moved = (
        "apps/services/gateway/gateway/room_stream.py",
        "packages/acb_memory/acb_memory/session_cache.py",
        "apps/services/gateway/gateway/memory_preread.py",
    )
    back = [f"{s.path}:{s.line} {s.method}" for s in tree_sites if s.path in moved]
    assert not back, back
    assert not any(fn.split("::")[0] in moved for fn in ALLOWED)


def test_the_scan_sees_the_known_borrowers(tree_sites) -> None:
    """The scan must see a client that one module borrows from another.

    If ``steer.py`` falls out of the scan, the taint across modules broke,
    and the ratchet passes for the wrong reason.
    """
    paths = {s.path for s in tree_sites}
    assert _ST in paths and _RL in paths
    assert "apps/services/orchestrator/orchestrator/pending_ask.py" in paths


# ---------------------------------------------------------------------------
# 2. The scanner on small sources.
# ---------------------------------------------------------------------------


def _scan(files: dict[str, str]) -> list[tuple[str, str, str, str]]:
    return [(s.path, s.function, s.kind, s.method) for s in scanner.scan(files)]


def test_an_alias_of_redis_asyncio_is_seen() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "c = aioredis.from_url('redis://h')\n"
            "async def f():\n"
            "    await c.get('cc:room:t')\n"
        ),
    })
    assert ("apps/x/m.py", "<module>", "connect", "from_url") in sites
    assert ("apps/x/m.py", "f", "key", "get") in sites


def test_from_redis_import_redis_as_r_is_seen() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "from redis import Redis as R\n"
            "def f():\n"
            "    r = R()\n"
            "    r.set('k', 1)\n"
        ),
    })
    assert ("apps/x/m.py", "f", "connect", "Redis") in sites
    assert ("apps/x/m.py", "f", "key", "set") in sites


def test_a_dotted_redis_asyncio_client_is_seen() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio\n"
            "async def f():\n"
            "    r = redis.asyncio.Redis()\n"
            "    await r.hset('h', 'a', 1)\n"
        ),
    })
    assert ("apps/x/m.py", "f", "key", "hset") in sites


def test_an_annotated_parameter_is_seen() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "async def f(r: aioredis.Redis | None):\n"
            "    await r.xadd('s', {'a': 1})\n"
        ),
    })
    assert sites == [("apps/x/m.py", "f", "key", "xadd")]


def test_a_client_passed_as_a_parameter_is_followed() -> None:
    sites = _scan({
        "apps/svc/pkg/__init__.py": "",
        "apps/svc/pkg/use.py": (
            "async def put(client, value):\n"
            "    await client.delete('k')\n"
        ),
        "apps/svc/pkg/call.py": (
            "import redis.asyncio as aioredis\n"
            "from pkg.use import put\n"
            "async def go():\n"
            "    await put(aioredis.from_url('redis://h'), 1)\n"
        ),
    })
    assert ("apps/svc/pkg/use.py", "put", "key", "delete") in sites
    assert not any(kind == "handoff" for _p, _f, kind, _m in sites)


def test_a_client_through_a_package_re_export_is_followed() -> None:
    """The ``acb_memory.get_session_memory`` shape: an ``__init__`` re-export."""
    sites = _scan({
        "packages/mem/mem/__init__.py": "from mem.cache import put\n",
        "packages/mem/mem/cache.py": (
            "async def put(*, client, thread):\n"
            "    await client.setex('session_mem:' + thread, 600, 'v')\n"
        ),
        "apps/svc/app/__init__.py": "",
        "apps/svc/app/relay.py": (
            "import redis.asyncio as aioredis\n"
            "_client: aioredis.Redis | None = None\n"
            "async def _get_client():\n"
            "    global _client\n"
            "    if _client is None:\n"
            "        _client = aioredis.from_url('redis://h')\n"
            "    return _client\n"
        ),
        "apps/svc/app/turn.py": (
            "async def turn(thread):\n"
            "    from mem import put\n"
            "    from app.relay import _get_client\n"
            "    await put(client=await _get_client(), thread=thread)\n"
        ),
    })
    assert ("packages/mem/mem/cache.py", "put", "key", "setex") in sites


def test_a_factory_borrowed_by_a_local_import_is_followed() -> None:
    """The ``steer.py`` shape: a function-local import of another module's
    raw factory. The importing module never imports ``redis``."""
    sites = _scan({
        "apps/svc/app/__init__.py": "",
        "apps/svc/app/relay.py": (
            "import redis.asyncio as aioredis\n"
            "_client = None\n"
            "async def _get_client() -> aioredis.Redis:\n"
            "    global _client\n"
            "    if _client is None:\n"
            "        _client = aioredis.from_url('redis://h')\n"
            "    return _client\n"
        ),
        "apps/svc/app/steer.py": (
            "async def persist(thread):\n"
            "    try:\n"
            "        from app.relay import _get_client\n"
            "        r = await _get_client()\n"
            "        await r.rpush('cc:steer:' + thread, 'x')\n"
            "    except Exception:\n"
            "        pass\n"
            "async def via_module():\n"
            "    from app import relay\n"
            "    r = await relay._get_client()\n"
            "    await r.get('k')\n"
        ),
    })
    assert ("apps/svc/app/steer.py", "persist", "key", "rpush") in sites
    assert ("apps/svc/app/steer.py", "via_module", "key", "get") in sites


def test_pipelines_and_pubsub_are_seen() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "async def f():\n"
            "    r = aioredis.from_url('redis://h')\n"
            "    p = r.pipeline()\n"
            "    p.incr('a')\n"
            "    await p.execute()\n"
            "    async with r.pipeline(transaction=True) as q:\n"
            "        q.expire('b', 5)\n"
            "    r.pipeline().set('c', 1)\n"
            "    ps = r.pubsub()\n"
            "    await ps.subscribe('chan')\n"
            "    await ps.get_message()\n"
        ),
    })
    keys = sorted(m for _p, _f, kind, m in sites if kind == "key")
    assert keys == ["expire", "incr", "set", "subscribe"]


def test_a_nested_function_in_a_branch_is_seen() -> None:
    """The ``activity.publish_activity._one_shot`` shape."""
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "def f(flag):\n"
            "    if flag:\n"
            "        async def _one_shot():\n"
            "            raw = aioredis.from_url('redis://h')\n"
            "            await raw.get('k')\n"
        ),
    })
    assert ("apps/x/m.py", "f._one_shot", "key", "get") in sites


def test_the_wrapper_is_not_a_site() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "from acb_common.tenant_redis import TenantRedis, get_tenant_redis, key\n"
            "async def f():\n"
            "    r = get_tenant_redis()\n"
            "    await r.get(key('room', 't'))\n"
            "    w = TenantRedis(aioredis.from_url('redis://h'))\n"
            "    await w.get(key('room', 't'))\n"
            "    d = {}\n"
            "    d.get('k')\n"
        ),
    })
    assert sites == [("apps/x/m.py", "f", "connect", "from_url")]


def test_a_client_or_url_handed_to_a_third_party_is_a_site() -> None:
    sites = _scan({
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "from vendor import Provider\n"
            "def f(settings):\n"
            "    Provider(client=aioredis.from_url('redis://h'))\n"
            "    Provider(redis_url=settings.redis_url)\n"
        ),
    })
    assert ("apps/x/m.py", "f", "handoff", "Provider") in sites
    assert ("apps/x/m.py", "f", "connect", "redis_url") in sites


def test_a_new_site_fails_with_its_file_its_line_and_the_wrapper() -> None:
    files = {
        "apps/x/m.py": (
            "import redis.asyncio as aioredis\n"
            "async def leak(org):\n"
            "    r = aioredis.from_url('redis://h')\n"
            "    await r.set('cc:secret:' + org, 1)\n"
        ),
    }
    out = violations(scanner.scan(files), {})
    joined = "\n".join(out)
    assert "apps/x/m.py:4" in joined and "leak" in joined
    assert "acb_common.tenant_redis" in joined and "get_tenant_redis()" in joined
    allow = {"apps/x/m.py::leak": Allow("a", "2", {"connect:from_url": 1, "key:set": 1}, "x" * 40)}
    assert violations(scanner.scan(files), allow) == []
    more = {"apps/x/m.py::leak": Allow("a", "2", {"connect:from_url": 1, "key:set": 2}, "x" * 40)}
    assert any(v.startswith("STALE") for v in violations(scanner.scan(files), more))
