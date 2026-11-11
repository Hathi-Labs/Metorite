"""App roles and row rules of a Studio object (WS-STU C8a).

Spec: ``project-docs/specs/ai_studio.md`` §19.7 and the §14 C8a row.

**The grammar.** A manifest names its roles at the top (``roles``). An object
takes ``access``, a list of one grant for each role::

    "roles": ["hr", "manager", "employee"],
    "objects": [{"name": "payslip", "access": [
      {"role": "hr", "read": "all", "write": "all"},
      {"role": "manager", "read": {"where": "manager = $me"}},
      {"role": "employee", "read": {"where": "employee = $me"}, "hide": ["bank_account"]}
    ]}]

``read`` is ``"all"`` or ``{"where": "<person field> = $me"}``, and nothing
else. The field is a hot ``person`` field with no ``many``. ``write`` is
``"all"`` or absent: v1 has no row rule for a write. ``hide`` is a list of
field keys. An object with no ``access`` is open: each member reads and writes
it, as before C8a.

**The rules of a viewer (owner approved, 2026-11-11).**

* The package owner passes every rule.
* A viewer with many roles gets the UNION of their reads. A record is
  readable when one grant of the viewer reads it.
* A key is hidden on a record when EACH grant that reads that record hides it.
  So a grant that reads all and hides nothing shows each key on each record.
* A filter, a sort and a write refuse a key that a grant of the viewer hides
  (:meth:`Scope.maybe_hidden`), unless a grant that reads all shows it.
* A formula target that reads a hidden key is hidden too. A roll-up target is
  hidden unless the viewer reads every record of the source and sees each
  key that the roll-up reads (:func:`rollup_hidden`).
* A create or an update must leave a record that the viewer can read
  (:meth:`Scope.sees`), as ``WITH CHECK`` does for RLS.

**One module of the grammar.** A2 (``studio_manifest.py``) and the object
store (``gateway/studio/objects_store.py``) each call :func:`parse_access` and
:func:`check_access_fit`, and neither keeps a copy. The store applies the
scope in its one reader and its one writer. Pure: no gateway import, no SQL
and no I/O.

**Tenancy (D12, D15).** A role is a projection of the D12 grant grammar, never
a third mechanism. Tenancy stays the row (``organization_id`` and RLS). A
viewer's roles come from the install (C8b) and the email from the session,
never from request input. ⚠️ RLS stays org-only, so a row rule is an
app-level check. The one-reader fence and the R8 suite hold it.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from acb_common.studio_fields import MAX_FIELDS_PER_OBJECT, NAME_PATTERN, FieldSpec, storable_text
from acb_common.studio_rules import FORMULA, ROLLUP, Rule, rule_reads

#: The roles of one package, and the grants of one object.
MAX_ROLES = 16
#: The one value of a grant that reads or writes every record.
ALL = "all"
#: The marker of the viewer in a ``where``.
ME = "$me"

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
#: ``<field> = $me``, with optional spaces around ``=``. `fullmatch` only.
_WHERE_RE = re.compile(rf"\s*({NAME_PATTERN})\s*=\s*\$me\s*", re.ASCII)
_GRANT_KEYS = frozenset({"role", "read", "write", "hide"})


class AccessError(ValueError):
    """One refused declaration of roles or access: a path and one sentence.

    ``path`` is relative to the manifest key, as ``[1].read``. The sentence
    names no record value."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Grant:
    """The grant of one role on one object, after :func:`parse_access`.

    ``where`` is the key of the person field of a row rule, or None when the
    grant reads all. ``write`` is True for ``"write": "all"``."""

    role: str
    where: str | None = None
    write: bool = False
    hide: tuple[str, ...] = ()

    def stored(self) -> dict[str, Any]:
        """The JSON of this grant, as the store keeps it."""
        out: dict[str, Any] = {"role": self.role,
                               "read": ALL if self.where is None else {"where": f"{self.where} = {ME}"}}
        if self.write:
            out["write"] = ALL
        if self.hide:
            out["hide"] = list(self.hide)
        return out


@dataclass(frozen=True)
class Viewer:
    """The member that reads or writes, and the roles of the member in the
    package (C8b resolves them from the install).

    The email is the lower-case address, as ``studio_fields._person`` stores
    it. The caller takes it from the session (``user.email``), never from a
    body. A role is a name of the manifest ``roles``."""

    email: str
    roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        email = self.email if isinstance(self.email, str) else ""
        clean = email.strip().lower()
        if "@" not in clean or not storable_text(clean) or len(clean) > 254:
            raise ValueError("A viewer is the email address of a member.")
        roles = frozenset(self.roles)
        if not all(isinstance(r, str) and _NAME_RE.fullmatch(r) for r in roles):
            raise ValueError("A role is lower-case letters, digits and _, a letter first.")
        object.__setattr__(self, "email", clean)
        object.__setattr__(self, "roles", roles)


# --- the declaration --------------------------------------------------------


def parse_roles(raw: Any) -> tuple[str, ...]:
    """The ``roles`` of a manifest. None means no roles. Raises :class:`AccessError`."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_ROLES:
        raise AccessError("", f"`roles` is a list of at most {MAX_ROLES} role names.")
    seen: set[str] = set()
    for i, role in enumerate(raw):
        if not isinstance(role, str) or not _NAME_RE.fullmatch(role):
            raise AccessError(
                f"[{i}]", "A role is lower-case letters, digits and _, a letter first, at most 48.")
        if role in seen:
            raise AccessError(f"[{i}]", f"Role `{role}` is listed twice. Each must be unique.")
        seen.add(role)
    return tuple(raw)


def _read(path: str, value: Any) -> str | None:
    """The person field of a ``where``, or None for ``"all"``."""
    if value == ALL:
        return None
    if isinstance(value, Mapping) and set(value) == {"where"} and isinstance(value["where"], str):
        found = _WHERE_RE.fullmatch(value["where"])
        if found is not None:
            return found.group(1)
    raise AccessError(
        f"{path}.read", 'A read is "all" or {"where": "<person field> = $me"}, and nothing else.')


def _hide(path: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_FIELDS_PER_OBJECT:
        raise AccessError(f"{path}.hide", "`hide` is a list of field keys of this object.")
    for j, key in enumerate(value):
        if not isinstance(key, str) or not _NAME_RE.fullmatch(key):
            raise AccessError(f"{path}.hide[{j}]", "`hide` is a list of field keys of this object.")
    if len(set(value)) != len(value):
        raise AccessError(f"{path}.hide", "`hide` names each field once.")
    return tuple(value)


def _grant(path: str, spec: Any, roles: Collection[str] | None) -> Grant:
    if not isinstance(spec, Mapping):
        raise AccessError(path, "A grant is an object with `role` and `read`.")
    extra = sorted(str(k) for k in spec if k not in _GRANT_KEYS)
    if extra:
        safe = extra[0] if _NAME_RE.fullmatch(extra[0]) else "?"
        raise AccessError(f"{path}.{safe}", f"`{safe}` is not a key of a grant. Remove it.")
    role = spec.get("role")
    if not isinstance(role, str) or not _NAME_RE.fullmatch(role):
        raise AccessError(f"{path}.role", "A grant names one role of the manifest `roles`.")
    if roles is not None and role not in roles:
        raise AccessError(f"{path}.role", f"Role `{role}` is not declared in `roles`.")
    if "read" not in spec:
        raise AccessError(f"{path}.read", "A grant needs `read`.")
    where = _read(path, spec["read"])
    write = spec.get("write")
    if write is not None and write != ALL:
        raise AccessError(f"{path}.write", 'A write is "all" in v1, or no write at all.')
    return Grant(role, where, write == ALL, _hide(path, spec.get("hide")))


def parse_access(raw: Any, roles: Collection[str] | None = None) -> tuple[Grant, ...] | None:
    """The grants of one object, or None when the object has no ``access``.

    *roles* is the ``roles`` of the manifest. With None, a role passes on its
    shape alone: the store runs this check again at a publish, after A2 ran
    it with the roles. An empty list is refused, because it would read as
    "open" to one reader and "closed" to another. Raises :class:`AccessError`.
    """
    if raw is None:
        return None
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ROLES:
        raise AccessError("", f"`access` is a list of 1 to {MAX_ROLES} grants, one for each role.")
    grants = [_grant(f"[{i}]", spec, roles) for i, spec in enumerate(raw)]
    seen: set[str] = set()
    for i, grant in enumerate(grants):
        if grant.role in seen:
            raise AccessError(f"[{i}].role", f"Role `{grant.role}` has two grants. Give it one.")
        seen.add(grant.role)
    return tuple(grants)


def check_access_fit(grants: Sequence[Grant] | None, fields: Sequence[FieldSpec]) -> None:
    """Refuse a grant that does not fit the *fields* of the object. A ``where``
    names a hot ``person`` field with no ``many``, and ``hide`` names fields of
    the object. Raises :class:`AccessError`."""
    by_key = {f.key: f for f in fields}
    for i, grant in enumerate(grants or ()):
        if grant.where is not None:
            field = by_key.get(grant.where)
            if field is None or field.type != "person" or field.many:
                raise AccessError(
                    f"[{i}].read", f"A `where` names a person field of this object. `{grant.where}` is not one.")
            if not field.hot:
                raise AccessError(
                    f"[{i}].read", f"Field `{grant.where}` must be hot, because a row rule reads it. Turn it hot.")
        for key in grant.hide:
            if key not in by_key:
                raise AccessError(f"[{i}].hide", f"`{key}` is not a field of this object.")


def access_reads(grants: Sequence[Grant] | None) -> frozenset[str]:
    """The person fields that the row rules read. They stay hot while a grant
    reads them."""
    return frozenset(g.where for g in grants or () if g.where is not None)


def stored_access(raw: Any) -> tuple[Grant, ...] | None:
    """The grants of the ``access`` column, or None for an open object.

    ⚠️ It fails closed. A stored grant that this code cannot read grants
    nothing, so its role reads nothing. A column that is not a list grants
    nothing to any role. Only the owner then reads the object."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        return ()
    out: list[Grant] = []
    for spec in raw:
        try:
            out.append(_grant("", spec, None))
        except AccessError:
            continue
    return tuple(out)


# --- the scope of one viewer ------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """What one viewer may do on one object.

    ``open`` is True for the owner and for an object with no ``access``: no
    rule binds. Else ``grants`` holds the grants of the viewer's roles, and an
    empty tuple reads nothing."""

    me: str
    open: bool
    grants: tuple[Grant, ...] = ()

    @property
    def read_all(self) -> bool:
        """True when the viewer reads every record of the object."""
        return self.open or any(g.where is None for g in self.grants)

    @property
    def where_keys(self) -> tuple[str, ...]:
        """The person fields of the viewer's row rules, each once, sorted. The
        store reads a record when one of them holds the viewer."""
        return tuple(sorted({g.where for g in self.grants if g.where is not None}))

    @property
    def reads(self) -> bool:
        """True when the viewer reads at least one record."""
        return self.read_all or bool(self.where_keys)

    @property
    def row_bound(self) -> bool:
        """True when a row rule binds each read: the store adds the ``where``."""
        return not self.read_all

    @property
    def writes(self) -> bool:
        """True when the viewer may create, change and delete records. A
        change and a delete still need a record that the viewer reads."""
        return self.open or any(g.write for g in self.grants)

    def _matching(self, data: Mapping[str, Any]) -> list[Grant]:
        return [g for g in self.grants if g.where is None or data.get(g.where) == self.me]

    def sees(self, data: Mapping[str, Any]) -> bool:
        """True when the viewer reads the record *data*."""
        return self.open or bool(self._matching(data))

    def hidden_for(self, data: Mapping[str, Any]) -> frozenset[str]:
        """The keys hidden on the record *data*: the keys that EACH grant that
        reads it hides. A record that no grant reads hides every key."""
        if self.open:
            return frozenset()
        matching = self._matching(data)
        if not matching:
            return frozenset(data)
        hidden = frozenset(matching[0].hide)
        for grant in matching[1:]:
            hidden &= frozenset(grant.hide)
        return hidden

    def maybe_hidden(self) -> frozenset[str]:
        """The keys that some record may hide from the viewer: each key that a
        grant hides, less each key that a grant which reads all shows. A
        filter, a sort and a write refuse them."""
        if self.open:
            return frozenset()
        hidden = frozenset(k for g in self.grants for k in g.hide)
        for grant in self.grants:
            if grant.where is None:
                hidden &= frozenset(grant.hide)
        return hidden

    def shows_all(self, keys: Collection[str]) -> bool:
        """True when the viewer reads every record and sees each of *keys* on
        each one. A roll-up of a source needs it (:func:`rollup_hidden`)."""
        if self.open:
            return True
        return any(g.where is None and not set(keys) & set(g.hide) for g in self.grants)


def scope_for(grants: Sequence[Grant] | None, viewer: Viewer, *, owner: bool) -> Scope:
    """The scope of *viewer* on an object with the stored *grants*. *owner* is
    True when the viewer owns the package. The store decides it from the
    package row, never from the caller."""
    if owner or grants is None:
        return Scope(viewer.email, True)
    return Scope(viewer.email, False, tuple(g for g in grants if g.role in viewer.roles))


def rollup_hidden(rules: Sequence[Rule], sources: Mapping[str, Scope]) -> frozenset[str]:
    """The roll-up targets of an object that the viewer may not read. A
    roll-up adds the records of its source, so its target shows only to a
    viewer who reads every record of the source and sees each key that the
    roll-up reads. A source with no scope in *sources* hides the target (it
    fails closed)."""
    out: set[str] = set()
    for rule in rules:
        if rule.kind != ROLLUP or rule.target is None:
            continue
        scope = sources.get(str(rule.source))
        if scope is None or not scope.shows_all(rule.source_keys()):
            out.add(rule.target)
    return frozenset(out)


def with_formulas(hidden: frozenset[str], rules: Sequence[Rule]) -> frozenset[str]:
    """*hidden* and each formula target that reads a hidden key, directly or
    through another formula. A formula of a hidden key would show the key."""
    if not hidden:
        return hidden
    out = set(hidden)
    formulas = [r for r in rules if r.kind == FORMULA and r.target is not None]
    grown = True
    while grown:
        grown = False
        for rule in formulas:
            if rule.target not in out and rule_reads(rule) & out:
                out.add(str(rule.target))
                grown = True
    return frozenset(out)
