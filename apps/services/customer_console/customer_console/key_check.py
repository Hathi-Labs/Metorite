"""The live key check of the connect flow and of the H6 fix (MS-2).

Spec: ``project-docs/specs/customer_console.md`` §6A.18 "API shapes", the
connect steps, and rule H6. Fence: ``tests/unit/test_catalog_connect_adopt_r8.py``.

🔴 **The host comes from the registry only.** ``provider_registry.KeyCheck``
names the full address, and no request field changes it. Connect takes no
``api_base``. A redirect is never followed, because a custom key header
would travel to the new host with it.

🔴 **10 seconds, end to end.** A vendor that does not answer in time gives
a failed check, and the caller stores nothing.

⚠️ **The vendor's words come back raw.** The caller passes them through
``main._try_provider_text`` before any response holds them, so no part of
the key leaves. Nothing here logs the words or the key.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from customer_console.provider_registry import RegistryProvider
from customer_console.try_media import try_clip

__all__ = ["LIMIT_SECONDS", "KeyResult", "check", "set_transport"]

_log = logging.getLogger(__name__)

#: The whole check, end to end (§6A.18 connect step 2).
LIMIT_SECONDS = 10.0

#: The most characters of a vendor body kept for the caller to scrub.
_SAID_MAX = 2000

#: The transport of the next check. ``None`` in production. A test sets an
#: ``httpx.MockTransport`` through :func:`set_transport`, so no test reaches
#: a vendor.
_TRANSPORT: list[httpx.AsyncBaseTransport | None] = [None]


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport. Tests use this. Production never does."""
    _TRANSPORT[0] = transport


@dataclass(frozen=True)
class KeyResult:
    """What one key check found."""

    ok: bool
    #: The vendor's status, or None for a timeout or a dropped connection.
    status: int | None
    #: The vendor's words, RAW. The caller scrubs them before any reply.
    said: str
    #: The vendor ids of a list check (after the provider prefix), or None
    #: when the check lists nothing.
    live: frozenset[str] | None


def _headers(entry: RegistryProvider, secret: str) -> dict[str, str]:
    spec = entry.key_check
    headers = dict(spec.headers)
    if spec.auth == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif spec.auth == "token":
        headers["Authorization"] = f"Token {secret}"
    elif spec.auth.startswith("header:"):
        headers[spec.auth.removeprefix("header:")] = secret
    else:  # pragma: no cover - the registry fence pins the three forms
        raise ValueError(f"unknown key check auth {spec.auth!r}")
    return headers


def _ids(entry: RegistryProvider, body: Any) -> frozenset[str]:
    """The vendor model ids in a list answer. An odd shape gives none."""
    shape = entry.key_check.ids
    found: list[Any] = []
    if shape == "data.id" and isinstance(body, dict) and isinstance(body.get("data"), list):
        found = [m.get("id") for m in body["data"] if isinstance(m, dict)]
    elif shape == "models.name" and isinstance(body, dict) and isinstance(body.get("models"), list):
        found = [
            str(m.get("name", "")).removeprefix("models/")
            for m in body["models"] if isinstance(m, dict)
        ]
    elif shape == "elevenlabs" and isinstance(body, list):
        found = [m.get("model_id") for m in body if isinstance(m, dict)]
    elif shape == "deepgram" and isinstance(body, dict):
        for group in ("stt", "tts"):
            for m in body.get(group) or []:
                if isinstance(m, dict):
                    found += [m.get("name"), m.get("canonical_name"), m.get("architecture")]
    return frozenset(str(i) for i in found if isinstance(i, str) and i)


async def _check(entry: RegistryProvider, secret: str) -> KeyResult:
    spec = entry.key_check
    async with httpx.AsyncClient(
        transport=_TRANSPORT[0], timeout=LIMIT_SECONDS, follow_redirects=False
    ) as client:
        if spec.method == "POST":
            response = await client.post(
                spec.url,
                headers=_headers(entry, secret),
                data=dict(spec.form),
                files={"file": ("try.wav", try_clip(), "audio/wav")},
            )
        else:
            response = await client.get(spec.url, headers=_headers(entry, secret))
    if not 200 <= response.status_code < 300:
        _log.warning(
            "key_check.refused",
            extra={"key_provider": entry.id, "key_status": response.status_code},
        )
        return KeyResult(False, response.status_code, response.text[:_SAID_MAX], None)
    live: frozenset[str] | None = None
    if spec.kind == "list":
        try:
            # A list we cannot read proves the key and nothing more. It
            # gives None, so the curated models stand, and each Try proves
            # its own model. An empty set would mark every model gone.
            live = _ids(entry, response.json()) or None
        except ValueError:
            live = None
    return KeyResult(True, response.status_code, "", live)


def check(entry: RegistryProvider, secret: str) -> KeyResult:
    """Prove *secret* works at the vendor of *entry*. 10 seconds at most.

    It never raises for a vendor fault: a timeout, a dropped connection and
    a refusal each give ``ok=False``. The caller runs it outside any
    database transaction, because it waits on the network.
    """
    try:
        return asyncio.run(asyncio.wait_for(_check(entry, secret), LIMIT_SECONDS))
    except TimeoutError:
        _log.warning("key_check.timeout", extra={"key_provider": entry.id})
        return KeyResult(
            False, None, f"{entry.label} did not answer in {LIMIT_SECONDS:g} seconds", None
        )
    except httpx.HTTPError as exc:
        _log.warning(
            "key_check.unreachable",
            extra={"key_provider": entry.id, "key_error": type(exc).__name__},
        )
        return KeyResult(False, None, f"{entry.label} could not be reached", None)
