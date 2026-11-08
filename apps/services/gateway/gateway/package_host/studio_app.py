"""A published Studio app joins the registry as a private nav entry (WS-PKG
PKG-8a).

Spec: ``project-docs/specs/package_host.md`` §4 (the ``nav`` point) and the
PKG-8a row of §10. The caller is the publish route,
``routes/studio/packages.py``. It calls :func:`keep_private_install` in the
session of the publish, after ``sync_objects``, and only while
``flags.package_host_allowed`` holds the org.

**One install for the owner.** The first publish writes one private install
for the owner, with ``version`` the published number and no end date. Each
later publish moves that install to the new version and replaces its claims
(``store.set_version``). So a publish never writes a second row and never
answers 409 ``already_installed``.

**Jobs (PKG-8c).** Each ``shell.jobs`` item with a verb and a view adds a
``jobs`` claim, keyed ``<app id>:<view>``. See :func:`_job_claims`.

**Fields (PKG-14a-2).** Each entry of ``contributes.fields`` adds a
``fields`` claim, keyed ``<record kind>:<field key>``, with the declaration
as the payload (``package_host.md`` §8.3). See :func:`_field_claims`. A bad
entry raises :class:`store.ContributionRefused`, and the publish or the
install writes nothing. The conflict rule of §4 is the rule of the store,
so a taken key raises :class:`store.ClaimTaken` (409, PKG-Q29).

**One nav claim.** The install claims one ``nav`` key: the href of the app,
:func:`app_href`. The payload holds the href, the name of the manifest as
the label, the icon of a package card in the Studio library, and the
feature ``studio``. The workbench serves the href with a redirect to the app
page (``src/app/apps/[id]/page.tsx``).

⚠️ **The href is outside ``/studio``.** The sidebar drops an install entry
whose href a system pane owns (``systemOwns`` in
``src/lib/shell/packageManifest.ts``), and ``/studio`` is a system pane. So
the prefix is :data:`APP_HREF_PREFIX`. The shell manifest ``studio.json``
gates the ``/apps`` route on the feature ``studio``. Fence:
``tests/unit/test_studio_publish_install.py``.

**An org or a group install (PKG-8b).** The route
``POST /studio/packages/{id}/install`` calls :func:`install_scope` on the
body, checks ``studio:publish`` (D180.3), locks the ``apps`` row, and then
calls :func:`keep_scope_install` with the live version. That function moves the
install at the scope when it exists, and writes it when it does not, with
the claims of :func:`claims_of`. A later publish moves only the private
install, so the holder approves a new version by a new call.

This module runs no SQL. The store is the one writer, and the route is the
one place that decides who may call this.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from acb_auth.permissions import normalize_grant_subject
from acb_common.studio_fields import NAME_PATTERN, FieldError, FieldSpec
from acb_common.studio_manifest import HOST_FIELD_KEYS, MAX_HOST_FIELDS

from gateway.package_host import field_values as fv
from gateway.package_host import store
from gateway.package_host.store import Contribution, ContributionRefused, Install

#: The path of an installed Studio app. The workbench route
#: ``src/app/apps/[id]/page.tsx`` serves it.
APP_HREF_PREFIX = "/apps/"
#: The icon of a package card in the Studio library (``AppsGallery.tsx``).
NAV_ICON = "Boxes"
#: The feature of the app page. Only a holder of it may open the app.
NAV_FEATURE = "studio"
#: The label of an app whose manifest name is empty, as in the Studio library.
UNTITLED = "Untitled app"
#: The most ``jobs`` claims of one app (PKG-8c).
MAX_JOBS = 20
#: The longest verb or app name in a job payload, in characters.
MAX_LABEL_CHARS = 120
#: The shape of a field key, as ``studio_fields`` checks it.
_FIELD_KEY_RE = re.compile(NAME_PATTERN, re.ASCII)
#: The bidi controls (U+202A to U+202E and U+2066 to U+2069).
_BIDI = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))


def _unsafe(text: str) -> bool:
    """True when *text* holds a control or bidi character."""
    return any(ch in _BIDI or unicodedata.category(ch) == "Cc" for ch in text)


def _app_name(manifest: Mapping[str, Any]) -> str | None:
    """The manifest name for a job payload: stripped, free of control and bidi
    characters, and cut to :data:`MAX_LABEL_CHARS`. None when nothing is left."""
    name = manifest.get("name")
    if not isinstance(name, str):
        return None
    clean = "".join(ch for ch in name if ch not in _BIDI and unicodedata.category(ch) != "Cc")
    return clean.strip()[:MAX_LABEL_CHARS].strip() or None


def app_href(app_id: str) -> str:
    """The sidebar href of the Studio app *app_id*."""
    return f"{APP_HREF_PREFIX}{app_id}"


def _job_claims(app_id: str, manifest: Mapping[str, Any]) -> list[Contribution]:
    """The ``jobs`` claims of the ``shell.jobs`` list (PKG-8c).

    A job claims the key ``<app id>:<view>`` and opens the app at
    :func:`app_href`, the same href as the ``nav`` claim. An item with no
    verb or no view is skipped. So is a verb that is longer than
    :data:`MAX_LABEL_CHARS` or holds a control or bidi character. A second job on one view is dropped, as the
    store refuses a key that the request claims twice. At most
    :data:`MAX_JOBS` jobs go in.
    """
    shell = manifest.get("shell")
    jobs = shell.get("jobs") if isinstance(shell, Mapping) else None
    if not isinstance(jobs, list):
        return []
    out: list[Contribution] = []
    seen: set[str] = set()
    app = _app_name(manifest)
    for item in jobs:
        if not isinstance(item, Mapping):
            continue
        verb, view = item.get("verb"), item.get("view")
        if not (isinstance(verb, str) and verb.strip() and isinstance(view, str) and view.strip()):
            continue
        verb = verb.strip()
        if len(verb) > MAX_LABEL_CHARS or _unsafe(verb):
            continue
        key = f"{app_id}:{view}"
        if key in seen:
            continue
        seen.add(key)
        payload: dict[str, Any] = {
            "label": verb, "href": app_href(app_id), "feature": NAV_FEATURE}
        if app is not None:
            payload["app"] = app
        out.append(Contribution(point="jobs", key=key, payload=payload, position=len(out)))
        if len(out) >= MAX_JOBS:
            break
    return out


def _declaration(field: FieldSpec) -> dict[str, Any]:
    """The payload of a ``fields`` claim: the field spec without its key
    (§8.3). It holds only the parts that ``field_values.declared_field``
    reads, so the store and the door parse it back to the same field."""
    out: dict[str, Any] = {"type": field.type, "many": field.many}
    if field.label is not None:
        out["label"] = field.label
    if field.options is not None:
        out["options"] = list(field.options)
    return out


def _field_claims(manifest: Mapping[str, Any]) -> list[Contribution]:
    """The ``fields`` claims of ``contributes.fields`` (PKG-14a-2, §8.3).

    Each entry gives one claim, in the order of the manifest. The key is
    ``<record>:<key>``, and the record is one of
    ``field_values.HOST_RECORD_KINDS``. The payload is :func:`_declaration`
    of ``field_values.declared_field``, the one parse of a declaration.
    ``validate_manifest`` refuses a bad entry at the save and at the
    publish. This function checks each entry again, because the install
    route reads a stored manifest. Unlike a bad job, a bad entry is not
    skipped: it raises :class:`ContributionRefused` (422), because a
    skipped field would hide a value without a word.
    """
    contributes = manifest.get("contributes")
    if not isinstance(contributes, Mapping):
        return []
    entries = contributes.get(fv.FIELDS_POINT)
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ContributionRefused("bad_fields", "The fields of `contributes` must be a list.")
    if len(entries) > MAX_HOST_FIELDS:
        raise ContributionRefused(
            "too_many_fields", f"A package declares at most {MAX_HOST_FIELDS} extension fields.")
    out: list[Contribution] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ContributionRefused("bad_field", "Each extension field must be a JSON object.")
        record, key = entry.get("record"), entry.get("key")
        if not isinstance(record, str) or record not in fv.HOST_RECORD_KINDS:
            raise ContributionRefused(
                "bad_record_kind",
                f"The record kind must be one of {', '.join(fv.HOST_RECORD_KINDS)}.")
        if not isinstance(key, str) or not _FIELD_KEY_RE.fullmatch(key):
            raise ContributionRefused(
                "bad_field_key",
                "A field key is lower-case letters, digits and _, a letter first, at most 48.")
        extra = sorted(str(k) for k in entry if k not in HOST_FIELD_KEYS)
        if extra:
            raise ContributionRefused(
                "bad_field", f"Field `{key}` holds `{extra[0][:48]}`, and an extension field "
                "cannot hold it.")
        try:
            field = fv.declared_field(
                key, {k: v for k, v in entry.items() if k not in ("record", "key")})
        except FieldError as exc:
            raise ContributionRefused("bad_field", str(exc)) from None
        claim = fv.claim_key(record, key)
        if claim in seen:
            raise ContributionRefused(
                "duplicate_field", f"The manifest declares the field `{claim}` twice.")
        seen.add(claim)
        out.append(Contribution(
            point=fv.FIELDS_POINT, key=claim, payload=_declaration(field), position=len(out)))
    return out


def claims_of(app_id: str, manifest: Mapping[str, Any]) -> list[Contribution]:
    """The claims of a published Studio app: one ``nav`` entry (PKG-8a), one
    ``jobs`` entry for each job of the manifest (PKG-8c), and one ``fields``
    entry for each extension field (PKG-14a-2).

    PKG-8d adds the cards, the search and the needs. A bad extension field
    raises :class:`ContributionRefused`.
    """
    name = manifest.get("name")
    label = name.strip() if isinstance(name, str) and name.strip() else UNTITLED
    href = app_href(app_id)
    nav = Contribution(point="nav", key=href, payload={
        "href": href, "label": label, "icon": NAV_ICON, "feature": NAV_FEATURE})
    return [nav, *_job_claims(app_id, manifest), *_field_claims(manifest)]


async def keep_private_install(
    db: Any,
    *,
    app_id: str,
    version: int,
    owner: str,
    manifest: Mapping[str, Any],
) -> Install:
    """Keep the private install of *owner* at *version*, with its claims.

    *db* is the session of the publish. *owner* is the session member, who
    owns the package. The function moves the install to *version* when it
    exists, and writes it when it does not. A refusal of the store raises an
    :class:`store.InstallError`, and the route answers with its
    ``detail_for`` of the member.
    """
    claims = claims_of(app_id, manifest)
    moved = await store.set_version(
        db, app_id=app_id, version=version, scope_subject=owner, scope_kind="private",
        contributions=claims)
    if moved is not None:
        return moved
    return await store.install(
        db, app_id=app_id, version=version, scope_subject=owner, scope_kind="private",
        installed_by=owner, ends_at=None, contributions=claims)


#: The scopes of the install route (PKG-8b). A private install comes with the
#: publish (PKG-8a), so the route takes no address.
ROUTE_KINDS = frozenset({"org", "group"})
#: The refusal of a scope that the route does not take.
BAD_SCOPE = ("The scope is org or group:<slug>. Your own private install comes with "
             "each publish, so send one of the two.")


def install_scope(raw: object) -> tuple[str, str]:
    """The kind and the normalised subject of the scope in a route body.

    *raw* is the value of ``scope`` in the body: ``org`` or
    ``group:<slug>``. The case of the text does not matter: ``Org``, ``ORG``
    and ``GROUP:Ops`` fold to ``org`` and ``group:ops``, as a slug is lower
    case. Each other value, an address too, raises
    :class:`store.ScopeRefused` (422), and the route reads nothing. The store
    checks the subject again before its first statement.
    """
    refused = store.ScopeRefused("bad_scope", BAD_SCOPE)
    if not isinstance(raw, str) or "\x00" in raw:
        raise refused
    try:
        # A lone surrogate raises UnicodeEncodeError, a ValueError.
        if len(raw.encode("utf-8")) > store.MAX_SUBJECT_BYTES:
            raise refused
        subject = normalize_grant_subject(raw.strip().lower())
    except ValueError:
        raise refused from None
    kind = "org" if subject == "org" else "group" if subject.startswith("group:") else "private"
    if kind not in ROUTE_KINDS:
        raise refused
    return kind, subject


async def keep_scope_install(
    db: Any,
    *,
    app_id: str,
    version: int,
    scope_kind: str,
    scope_subject: str,
    member: str,
    manifest: Mapping[str, Any],
) -> tuple[Install, bool]:
    """Keep the install of *app_id* at an org or a group scope, at *version*.

    *db* is the session of the route, which holds the ``apps`` row ``FOR KEY
    SHARE`` already. *member* is the session member, and the store writes
    it as ``installed_by`` of a new install. The function moves the install
    at the scope when it exists, and writes it when it does not, as
    :func:`keep_private_install` does. Answers the install and True when it
    wrote a new one. A refusal of the store raises an
    :class:`store.InstallError`, and the route answers with its
    ``detail_for`` of the member.
    """
    if scope_kind not in ROUTE_KINDS:
        raise store.ScopeRefused("bad_scope", BAD_SCOPE)
    claims = claims_of(app_id, manifest)
    moved = await store.set_version(
        db, app_id=app_id, version=version, scope_subject=scope_subject,
        scope_kind=scope_kind, contributions=claims)
    if moved is not None:
        return moved, False
    made = await store.install(
        db, app_id=app_id, version=version, scope_subject=scope_subject, scope_kind=scope_kind,
        installed_by=member, ends_at=None, contributions=claims)
    return made, True
