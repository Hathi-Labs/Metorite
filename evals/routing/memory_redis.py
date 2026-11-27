"""The Redis of a scripted harness run, held in memory.

A scripted run is offline. Before this module, only the event relay and the egress mark
were stand-ins. ``stream_relay`` (the active mark and the live-run registry) and the
activity writes reached whatever ``redis_url`` named, so a run on a developer machine or
on a box wrote there.

:func:`install` replaces the command methods of the redis-py CLASSES, so it covers a
client that a module built before the run too. A command in :data:`COMMANDS` is answered
from a dict, as an empty Redis answers it. Each other command, a pubsub command and each
connect raise ``ConnectionError``, which is what a run with no Redis sees (CI). So no
command reaches a server.

Only a scripted run installs it. A live run is as before.
"""
from __future__ import annotations

from typing import Any

import redis
import redis.asyncio
from redis.asyncio.client import Pipeline as AsyncPipeline
from redis.asyncio.client import PubSub as AsyncPubSub
from redis.client import Pipeline, PubSub
from redis.exceptions import ConnectionError as RedisConnectionError


def _held(what: str) -> RedisConnectionError:
    return RedisConnectionError(f"the scripted run holds Redis in memory: {what}")


class MemoryRedis:
    """The values of one harness run, and each command that it got."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        #: Each command: ``(name, first key)``.
        self.commands: list[tuple[str, str]] = []
        #: Each command or connect that this store refused.
        self.refused: list[str] = []

    def run(self, args: tuple[Any, ...]) -> Any:
        """The answer to one raw command, as redis-py returns it after its callbacks."""
        name = str(args[0]).upper()
        rest = args[1:]
        self.commands.append((name, str(rest[0]) if rest else ""))
        handler = COMMANDS.get(name)
        if handler is None:
            self.refused.append(name)
            raise _held(f"no answer for {name}")
        return handler(self.values, rest)

    def refuse(self, what: str) -> RedisConnectionError:
        self.refused.append(what)
        return _held(what)


def _set(values: dict[str, Any], rest: tuple[Any, ...]) -> Any:
    name, value, *options = rest
    if "NX" in options and name in values:
        return None
    values[name] = str(value)
    return True


def _hset(values: dict[str, Any], rest: tuple[Any, ...]) -> int:
    name, *pairs = rest
    row = values.setdefault(name, {})
    added = sum(1 for k in pairs[::2] if str(k) not in row)
    row.update({str(k): str(v) for k, v in zip(pairs[::2], pairs[1::2], strict=True)})
    return added


def _xadd(values: dict[str, Any], rest: tuple[Any, ...]) -> str:
    rows = values.setdefault(rest[0], [])
    rows.append(tuple(rest[1:]))
    return f"{len(rows)}-0"


def _hdel(values: dict[str, Any], rest: tuple[Any, ...]) -> int:
    name, *keys = rest
    row = values.get(name, {})
    return sum(1 for k in keys if row.pop(str(k), None) is not None)


def _sadd(values: dict[str, Any], rest: tuple[Any, ...]) -> int:
    name, *members = rest
    row = values.setdefault(name, set())
    added = len({str(m) for m in members} - row)
    row.update(str(m) for m in members)
    return added


#: The commands that a scripted run of the measure sends today: the active mark and the
#: live-run registry of ``stream_relay``, and the activity writes. No key expires here.
COMMANDS: dict[str, Any] = {
    "GET": lambda values, rest: values.get(rest[0]),
    "SET": _set,
    "DEL": lambda values, rest: sum(1 for n in rest if values.pop(n, None) is not None),
    "HGET": lambda values, rest: values.get(rest[0], {}).get(str(rest[1])),
    "HSET": _hset,
    "HDEL": _hdel,
    "SADD": _sadd,
    "EXPIRE": lambda values, rest: rest[0] in values,
    "XADD": _xadd,
}


def install(mp: Any) -> MemoryRedis:
    """Hold every Redis command of this process in memory, until *mp* undoes it."""
    store = MemoryRedis()

    async def execute(_client: Any, *args: Any, **_kw: Any) -> Any:
        return store.run(args)

    async def execute_pipeline(pipe: Any, *_a: Any, **_kw: Any) -> list[Any]:
        stack, pipe.command_stack = list(pipe.command_stack), []
        return [store.run(args) for args, _options in stack]

    def execute_sync(_client: Any, *args: Any, **_kw: Any) -> Any:
        return store.run(args)

    def execute_pipeline_sync(pipe: Any, *_a: Any, **_kw: Any) -> list[Any]:
        stack, pipe.command_stack = list(pipe.command_stack), []
        return [store.run(args) for args, _options in stack]

    async def no_async(_self: Any, *_a: Any, **_kw: Any) -> Any:
        raise store.refuse("a pubsub command or a connect")

    def no_sync(_self: Any, *_a: Any, **_kw: Any) -> Any:
        raise store.refuse("a pubsub command or a connect")

    mp.setattr(redis.asyncio.Redis, "execute_command", execute)
    mp.setattr(AsyncPipeline, "execute", execute_pipeline)
    mp.setattr(AsyncPipeline, "immediate_execute_command", execute)
    mp.setattr(AsyncPubSub, "execute_command", no_async)
    mp.setattr(redis.Redis, "execute_command", execute_sync)
    mp.setattr(Pipeline, "execute", execute_pipeline_sync)
    mp.setattr(Pipeline, "immediate_execute_command", execute_sync)
    mp.setattr(PubSub, "execute_command", no_sync)
    # The backstop: a path that no line above covers cannot open a connection.
    for connection in (redis.asyncio.connection.AbstractConnection,
                       redis.connection.AbstractConnection):
        is_async = connection is redis.asyncio.connection.AbstractConnection
        mp.setattr(connection, "connect", no_async if is_async else no_sync)
    return store
