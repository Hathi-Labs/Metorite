"""CRM · verified queries (WS-SEM SEM-8, task 3738).

Spec: ``project-docs/specs/semantic_model.md`` §3.2, §3.3, §5 steps 5 to 10,
§6.2 and §6.3.

    POST /crm/verified-queries/match   the verified questions that match
                                       the member's words, and for an admin
                                       the stale ones
    GET  /crm/verified-queries/{key}   one verified question, lowered to the
                                       parameters of ``GET /crm/aggregate``
                                       with the values ``param.<name>``

**No new store.** A verified query is the ``query`` item of the plan
(SEM-7, ``crm_sync/catalogue.py``). A save is a new approved version of the
plan through :func:`mapping_plans.commit`, so the plan lock, the checks and
Undo (``POST /crm/mapping/plans/{version}/restore``) all hold. A repair is
a draft of the plan through :func:`mapping_plans.write_draft`. No admin
approves a draft in silence.

**Save** (:func:`save`). An admin gives the question in the member's words
and the query. Each condition names the words of the question that hold its
value (``literal``). The server puts a named parameter in place of those
words. It keeps the question words, the query with the parameter names and
the plan version. It keeps no total, no parameter value and no record text
(§3.3 points 3 and 5). ``catalogue.semantic_problem`` refuses a digit that
stays in the question words.

**Match** (:func:`match`). A later question matches by its words and by the
kinds of the parameters, in code, with 0 tokens. The word rule is the term
matcher of ``crm_sync/terms.py`` (§18.16.4): ``terms.words`` and its prefix
rule. The answer names the parameters that the member must give.

**Run.** The server lowers the stored query to the parameters of the
existing aggregate (:func:`lower`). The chat tool then reads
``GET /crm/aggregate`` as the member. :func:`run_query` calls
``aggregate.compute``, the one body of that route, with the member. So a
verified question never reads more than the same aggregate reads. No SQL
lives here.

**Stale** (:func:`stale_reason`). A query is stale when the catalogue of
today drops a module, a field or a measure that the query uses. The mark is
derived at read time from the catalogue. No column holds it. A stale query
is never matched and never lowered. Only an admin sees it, with its reason.

**One repair** (:func:`propose_repair`). For a stale query, the server
proposes ONE repaired version. It maps each dropped field to the closest
remaining field of the same module by the catalogue, and keeps the
parameters and the output. It runs the repair read-only, and keeps it as a
draft item only when it runs. The repair is a pure function of the query
and the catalogue, so one catalogue shape gives one repair at most. While a
draft holds a change of the query, no second repair is made.

**Tenancy (R5).** Each read and write opens ``core._tenant_session``, bound
to the org of the session. The org comes from ``UserContext`` only, never
from the request. A member who is not an admin never sees a stale query,
and the access rule of the CRM router applies to each route.

**Dark.** Each route and each function answers 404 ``semantic_off`` for an
org off ``SEMANTIC_MODEL_ORGS`` (``flags.semantic_model_allowed``).

Fences: ``tests/unit/test_crm_verified_queries.py`` and
``tests/unit/test_crm_verified_queries_r8.py``.
"""

from __future__ import annotations

import calendar
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Request
from gateway.crm_sync import catalogue as cat
from gateway.crm_sync import terms
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import _tenant_session, actor, router
from gateway.routes.crm.field_sql import DATE_TYPES as _RANGE_DATES
from gateway.routes.crm.field_sql import MEASURE_TYPES
from gateway.routes.crm.flags import require_crm_mirror, semantic_model_allowed
from gateway.routes.crm.mapping_plans import (
    SUGGEST_ACTOR,
    _approved,
    _rules_of,
    carry_draft,
    commit,
    kind_key,
    lock_plan,
    merge_kinds,
    open_draft,
    split_rules,
    write_draft,
)
from pydantic import BaseModel, ConfigDict, Field

_log = get_logger("crm.verified_queries")

#: The prefix of a parameter value in the query string of the lower route.
PARAM_PREFIX = "param."
#: The longest value of one parameter.
VALUE_MAX = 200
#: The most matches that one answer names.
MATCH_MAX = 5
#: The longest text that a match reads.
ASKED_MAX = 2000

#: The words that no question tells apart by: the glue of a sentence.
GLUE_WORDS = frozenset({
    "a", "an", "the", "of", "for", "in", "on", "to", "per", "by", "with", "my", "our",
    "all", "what", "whats", "is", "was", "are", "were", "how", "much", "many", "me",
    "show", "give", "tell", "and", "from", "at", "this", "that", "do", "does", "we", "i",
})
#: The kinds of a parameter that name a time.
DATE_KINDS = frozenset({"date", "month", "quarter", "year", "period"})
#: The field types that hold a day or a time (``field_sql.DATE_TYPES``).
DATE_TYPES = frozenset(_RANGE_DATES)
#: The field types that take a range (``.from`` and ``.to``): a date or a
#: number, as ``field_sql._filter_value`` allows.
RANGE_TYPES = frozenset({*_RANGE_DATES, *MEASURE_TYPES})
#: The field types of a choice.
OPTION_TYPES = frozenset({"select", "picklist", "multiselect", "boolean"})
#: The month names, for the kind check of a match.
_MONTHS = frozenset(
    w.lower() for w in (*calendar.month_name[1:], *calendar.month_abbr[1:]))
#: The words that name a time with no digit.
_TIME_WORDS = frozenset({"today", "yesterday", "week", "month", "quarter", "year", "last",
                         "this", "current", "previous", "next"})
_QUARTER = re.compile(r"^(\d{4})-?q([1-4])$", re.IGNORECASE)
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR = re.compile(r"^\d{4}$")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")


def _refuse(code: str, message: str, status: int = 422, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message,
                                                     **extra})


def _off() -> HTTPException:
    return _refuse("semantic_off", "Verified questions are off for this organization.", 404)


def _org(user: UserContext) -> str:
    """The org of the session (R5). 404 ``semantic_off`` off the list."""
    org = str(user.organization_id or "")
    if not org or not semantic_model_allowed(org):
        raise _off()
    return org


async def _require_admin(user: UserContext) -> str:
    """The checks of a plan write (``field_rules._GUARDS``): the mirror
    flag (else 404) and ``admin:access:manage`` (else 403). Then the flag of
    this slice."""
    await require_crm_mirror(user)
    if not user.has_permission(ADMIN_PERMISSION):
        raise HTTPException(status_code=403, detail="Forbidden: a CRM admin saves this.")
    return _org(user)


# ── The pure parts ──────────────────────────────────────────────────────────


def queries_of(others: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The legal ``query`` items of a version."""
    return [dict(o) for o in others
            if o.get("kind") == cat.KIND_QUERY and cat.semantic_problem(o) is None]


def measures_of(others: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """The legal ``measure`` items of a version, by key."""
    return {str(o["key"]): dict(o) for o in others
            if o.get("kind") == cat.KIND_MEASURE and cat.semantic_problem(o) is None}


def _shape_keys(shape: Mapping[str, Any]) -> list[str]:
    """Each field key of a query shape, in order, each once."""
    keys = [*(shape.get("fields") or []), *(w.get("field") for w in shape.get("where") or []),
            *(shape.get("group_by") or [])]
    return list(dict.fromkeys(str(k) for k in keys))


def stale_reason(item: Mapping[str, Any], by_module: Mapping[str, cat.Source],
                 measures: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Why a legal ``query`` item does not fit the catalogue of today, or
    ``None``. Derived at read time, never stored. It holds our own names
    only: ``module:<Module>``, ``measure:<key>``, ``field:<key>`` or
    ``type:<key>``. It follows ``catalogue.semantic_ref_problem``, and it
    names the part that left."""
    shape = item.get("query") or {}
    module = str(shape.get("module"))
    src = by_module.get(module)
    if src is None:
        return f"module:{module}"
    name = shape.get("measure")
    if name is not None:
        measure = measures.get(str(name))
        if measure is None or cat.semantic_ref_problem(measure, by_module) is not None:
            return f"measure:{name}"
    for key in _shape_keys(shape):
        if src.field(key) is None:
            return f"field:{key}"
    aggregate = shape.get("aggregate")
    if aggregate is not None and aggregate != "count":
        for key in shape.get("fields") or []:
            found = src.field(str(key))
            if found is None or found.type not in cat.NUMBER_TYPES:
                return f"type:{key}"
    # A condition whose field changed its type (a date that became text)
    # no longer takes the kind of its parameter.
    kinds = {str(p.get("name")): str(p.get("kind")) for p in item.get("params") or []}
    for cond in shape.get("where") or []:
        types = _types_for("where", kinds.get(str(cond.get("param"))), None)
        found = src.field(str(cond.get("field")))
        if types is not None and found is not None and found.type not in types:
            return f"type:{cond.get('field')}"
    return None


def _param_name(key: str, taken: set[str]) -> str:
    """A parameter name from a field key: its words with ``_``."""
    base = "_".join(terms.words(key))[:28] or "value"
    if not base[0].isalpha():
        base = f"p_{base}"[:28]
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def _kind_of(found: cat.Field | None) -> str:
    """The kind of a parameter from the type of its field."""
    ftype = found.type if found is not None else ""
    if ftype == "lookup":
        return "record"
    if ftype in OPTION_TYPES:
        return "option"
    if ftype in DATE_TYPES:
        return "period"
    if ftype in cat.NUMBER_TYPES:
        return "number"
    return "text"


def _swap(question: str, literal: str, name: str) -> str:
    """*question* with each whole-word span of *literal* (any case, the
    spaces folded) as ``{name}``, so no copy of the value stays. A part of a word never matches, so the
    literal "in" never hits "Invoices". 422 ``literal_not_in_question``
    when the words do not hold it."""
    words = " ".join(literal.split())
    found = re.compile(rf"(?<!\w){re.escape(words)}(?!\w)", re.IGNORECASE)
    if not words or not found.search(question):
        raise _refuse("literal_not_in_question",
                      "Each condition names words of the question that hold its value.")
    return found.sub("{" + name + "}", question)


def _key_of(question: str, names: Sequence[str]) -> str:
    """A key from the question words, then ``__`` and the parameter names.
    So "Net value for {customer}" and "Net value in {month}" get two keys:
    ``net_value__customer`` and ``net_value__month``."""
    plain = [w for w in terms.words(cat._PLACEHOLDER.sub(" ", question))
             if w not in GLUE_WORDS and not w[0].isdigit()]
    head = "_".join(plain)[:24].rstrip("_") or "query"
    head = head if head[0].isalpha() else f"q_{head}"[:24]
    tail = "_".join(names)
    return f"{head}__{tail}"[:40].rstrip("_") if tail else head


class ConditionIn(BaseModel):
    """One condition of a query to save. ``literal`` is the words of the
    question that hold the value. The server never stores them."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=cat.SUM_KEY_MAX)
    op: str
    literal: str | None = Field(default=None, min_length=1, max_length=VALUE_MAX)
    name: str | None = Field(default=None, max_length=32)
    kind: str | None = None


class SaveIn(BaseModel):
    """A question and its query, as an admin saves them."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=ASKED_MAX)
    module: str = Field(min_length=1, max_length=cat.SUM_KEY_MAX)
    measure: str | None = None
    aggregate: str | None = None
    fields: list[str] | None = None
    where: list[ConditionIn] = Field(default_factory=list, max_length=cat.WHERE_MAX)
    group_by: list[str] = Field(default_factory=list, max_length=cat.GROUP_MAX)
    key: str | None = None


def build_item(payload: SaveIn, by_module: Mapping[str, cat.Source], version: int,
               measures: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The ``query`` item of *payload*: each literal is a named parameter.
    It holds no value. 422 ``bad_query`` names the part that is not legal,
    and ``question_value`` means a figure that stays in the question.

    *version* is the plan version that the save commits (the version that
    holds the item first). With *measures* (the save), an item that fits
    the catalogue must also lower: 422 ``not_runnable`` names a shape that
    the aggregate cannot run (:func:`runnable_problem`)."""
    src = by_module.get(payload.module)
    question = " ".join(payload.question.split())
    taken: set[str] = set()
    params: list[dict[str, str]] = []
    where: list[dict[str, str]] = []
    for cond in payload.where:
        found = src.field(cond.field) if src is not None else None
        name = cond.name or _param_name(cond.field, taken)
        if not _NAME.fullmatch(name) or name in taken:
            raise _refuse("bad_query", "The query is not legal (params).")
        taken.add(name)
        if cond.literal is not None:
            question = _swap(question, cond.literal.strip(), name)
        params.append({"name": name, "kind": cond.kind or _kind_of(found)})
        where.append({"field": cond.field, "op": cond.op, "param": name})
    shape: dict[str, Any] = {"module": payload.module}
    if payload.measure is not None:
        shape["measure"] = payload.measure
    else:
        shape["aggregate"] = payload.aggregate or "count"
        if payload.fields:
            shape["fields"] = list(payload.fields)
    if where:
        shape["where"] = where
    if payload.group_by:
        shape["group_by"] = list(payload.group_by)
    key = payload.key or _key_of(question, [p["name"] for p in params])
    item = {"kind": cat.KIND_QUERY, "key": key, "question": question, "params": params,
            "query": shape, "model_version": int(version)}
    problem = cat.semantic_problem(item)
    if problem is not None:
        raise _refuse("bad_query", f"The query is not legal ({problem}).")
    if measures is not None and stale_reason(item, by_module, measures) is None:
        problem = runnable_problem(item, measures, by_module)
        if problem is not None:
            raise _not_runnable(problem)
    return item


def _hit(word: str, said: Sequence[str]) -> bool:
    """The prefix rule of the term matcher (``terms._hit``)."""
    return terms._hit(word, said)


def _kind_seen(kind: str, said: Sequence[str]) -> bool:
    """True when the words can hold a value of *kind*. A time needs a digit,
    a month name or a time word. A number needs a digit. Any other kind is
    a name, and words cannot show it."""
    digit = any(w[0].isdigit() for w in said)
    if kind in DATE_KINDS:
        return digit or any(w in _MONTHS or w in _TIME_WORDS for w in said)
    if kind == "number":
        return digit
    return True


def wanted_words(question: str) -> list[str]:
    """The words of a saved question that a match needs: each word that is
    no glue and no parameter."""
    bare = cat._PLACEHOLDER.sub(" ", question)
    return [w for w in dict.fromkeys(terms.words(bare)) if w not in GLUE_WORDS]


def match(asked: str, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The items whose question matches *asked*: each wanted word of the
    question is a word of *asked* (the prefix rule of the term matcher),
    and the words can hold a value of each time and number parameter. The
    most words first, at most :data:`MATCH_MAX`. Pure: nothing is kept."""
    said = terms.words(str(asked)[:ASKED_MAX])
    found: list[tuple[int, str, Mapping[str, Any]]] = []
    for item in items:
        wanted = wanted_words(str(item.get("question") or ""))
        if not wanted or not all(_hit(w, said) for w in wanted):
            continue
        if not all(_kind_seen(str(p.get("kind")), said) for p in item.get("params") or []):
            continue
        found.append((len(wanted), str(item.get("key")), item))
    found.sort(key=lambda f: (-f[0], f[1]))
    return [dict(f[2]) for f in found[:MATCH_MAX]]


@dataclass(frozen=True)
class Lowered:
    """A verified query in the parameters of ``GET /crm/aggregate``."""

    entity: str
    measure: str
    field: str | None = None
    fields: str | None = None
    group_field: str | None = None
    bucket: str | None = None
    pairs: tuple[tuple[str, str], ...] = ()

    def params(self) -> dict[str, str]:
        """The query of ``GET /crm/aggregate``. :func:`lower` refuses two
        pairs with one name, so this dict holds each pair of
        :attr:`pairs`, the list that ``aggregate.compute`` takes."""
        out = {"entity": self.entity, "measure": self.measure}
        for name in ("field", "fields", "group_field", "bucket"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        for name, value in self.pairs:
            out[f"field.{name}"] = value
        return out


def _not_runnable(why: str) -> HTTPException:
    return _refuse("not_runnable", f"The aggregate cannot run this query ({why}).", why=why)


def period(value: str) -> tuple[str, str] | None:
    """The first and last day of a date, a month, a quarter or a year, as
    ISO dates. ``None`` for any other text."""
    text_ = value.strip()
    try:
        if len(text_) == 10:
            day = date.fromisoformat(text_)
            return day.isoformat(), day.isoformat()
        found = _MONTH.fullmatch(text_)
        if found:
            year, month = int(found.group(1)), int(found.group(2))
            last = calendar.monthrange(year, month)[1]
            return date(year, month, 1).isoformat(), date(year, month, last).isoformat()
        found = _QUARTER.fullmatch(text_)
        if found:
            year, q = int(found.group(1)), int(found.group(2))
            first, end = 3 * q - 2, 3 * q
            last = calendar.monthrange(year, end)[1]
            return date(year, first, 1).isoformat(), date(year, end, last).isoformat()
        if _YEAR.fullmatch(text_):
            return f"{text_}-01-01", f"{text_}-12-31"
    except ValueError:
        return None
    return None


def _check_value(name: str, kind: str, value: str) -> None:
    """422 ``bad_value`` for a value that is not of *kind*."""
    good = True
    if kind == "number":
        # A range of numbers is "<from>..<to>": each side is a number.
        try:
            parts = value.split("..")
            good = (len(parts) <= 2 and all(p.strip() for p in parts)
                    and all(math.isfinite(float(p)) for p in parts))
        except ValueError:
            good = False
    elif kind in DATE_KINDS:
        good = all(period(p) is not None for p in value.split("..") if p.strip())
    if not good:
        raise _refuse("bad_value", f"The value of {name} is not a {kind}.", param=name)


def _values(item: Mapping[str, Any], values: Mapping[str, Any], *,
            partial: bool) -> dict[str, str]:
    """The checked values of the parameters of *item*. 422 names each
    parameter that is unknown or missing. With *partial*, a missing one is
    left out."""
    kinds = {str(p["name"]): str(p["kind"]) for p in item.get("params") or []}
    unknown = sorted(set(values) - set(kinds))
    if unknown:
        raise _refuse("param_unknown", "This verified question takes other parameters.",
                      params=sorted(kinds))
    out: dict[str, str] = {}
    for name, kind in kinds.items():
        raw = values.get(name)
        value = " ".join(str(raw).split()) if raw is not None else ""
        if not value:
            continue
        if len(value) > VALUE_MAX:
            raise _refuse("bad_value", f"The value of {name} is too long.", param=name)
        _check_value(name, kind, value)
        out[name] = value
    missing = sorted(set(kinds) - set(out))
    if missing and not partial:
        raise _refuse("param_missing", "Give a value for each parameter.", params=missing)
    return out


def _condition(src: cat.Source, cond: Mapping[str, Any], value: str) -> list[tuple[str, str]]:
    """The ``field.<key>`` pairs of one condition with its value."""
    key, op = str(cond["field"]), str(cond["op"])
    found = src.field(key)
    dated = found is not None and found.type in DATE_TYPES
    if op in ("gte", "lte", "between") and (found is None or found.type not in RANGE_TYPES):
        # The aggregate takes a range on a date or a number field only.
        raise _not_runnable(f"a range on a {found.type if found else 'missing'} field")
    if not dated and op in ("eq", "gte", "lte") and ".." in value:
        raise _refuse("bad_value", f"Give {cond['param']} as one value, not a range.",
                      param=cond["param"])
    if op in ("eq", "on") and dated:
        span = period(value)
        if span is None:
            raise _refuse("bad_value", f"The value of {cond['param']} is no date.",
                          param=cond["param"])
        return [(f"{key}.from", span[0]), (f"{key}.to", span[1])]
    if op == "eq":
        return [(key, value)]
    if op in ("gte", "lte"):
        span = period(value) if dated else (value, value)
        if span is None:
            raise _refuse("bad_value", f"The value of {cond['param']} is no date.",
                          param=cond["param"])
        return [(f"{key}.from", span[0])] if op == "gte" else [(f"{key}.to", span[1])]
    if op == "between":
        low, sep, high = value.partition("..")
        if not sep or not low.strip() or not high.strip():
            raise _refuse("bad_value", f"Give {cond['param']} as <from>..<to>.",
                          param=cond["param"])
        start = period(low) if dated else (low.strip(), low.strip())
        end = period(high) if dated else (high.strip(), high.strip())
        if start is None or end is None:
            raise _refuse("bad_value", f"The value of {cond['param']} is no date.",
                          param=cond["param"])
        return [(f"{key}.from", start[0]), (f"{key}.to", end[1])]
    raise _not_runnable(f"operator {op}")


def _measure_parts(src: cat.Source, aggregate: str, fields: Sequence[Any],
                   filters: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list]:
    """The measure words of the aggregate, and the pairs of fixed filters.

    A ``sum`` of 2 to 5 fields is ONE total of their sum. On a module it
    lowers to the key ``A+B`` that ``field_sql.FieldScope.ref`` reads as one
    sum. It never lowers to ``fields=A,B``: that is a split, whose total is
    the first field only. A core entity has no such key, so it cannot run."""
    keys = [str(k) for k in fields]
    if aggregate == "count":
        out: dict[str, Any] = {"measure": "count"}
    elif len(keys) == 1:
        out = {"measure": aggregate, "field": keys[0]}
    elif aggregate == "sum" and src.kind == "module":
        # ``FieldScope._sum_of`` adds the parts of ``catalogue.SUM_TYPES``
        # only. A part of another number type is a 422 at run time.
        types = [getattr(src.field(k), "type", None) for k in keys]
        if not all(t in cat.SUM_TYPES for t in types):
            raise _not_runnable(f"a sum of fields that are not all of {list(cat.SUM_TYPES)}")
        out = {"measure": "sum", "field": cat.SUM_KEY_JOIN.join(keys)}
    elif aggregate == "sum":
        raise _not_runnable("a sum of more than one field on a core entity")
    else:
        raise _not_runnable("a measure of more than one field")
    pairs: list[tuple[str, str]] = []
    for one in filters:
        options = [str(o) for o in one.get("options") or []]
        if len(options) != 1:
            raise _not_runnable("a fixed filter of more than one option")
        pairs.append((str(one["field"]), options[0]))
    return out, pairs


def _option(src: cat.Source, cond: Mapping[str, Any], value: str) -> str:
    """The option key that *value* names, in the case of the field. 422
    ``not_an_option`` when the field holds option keys and *value* is none
    of them. A field with no option keys takes any value."""
    found = src.field(str(cond["field"]))
    options = found.options if found is not None else ()
    if not options:
        return value
    for option in options:
        if option.lower() == value.lower():
            return option
    raise _refuse("not_an_option", f"The value of {cond['param']} is not an option.",
                  param=cond["param"], options=list(options))


#: A value of each kind that lowers, for the save check of a shape.
_PLACEHOLDERS = {"number": "1", "date": "2026-01-01", "month": "2026-01",
                 "quarter": "2026-Q1", "year": "2026", "period": "2026-01"}


def runnable_problem(item: Mapping[str, Any], measures: Mapping[str, Mapping[str, Any]],
                     by_module: Mapping[str, cat.Source]) -> str | None:
    """Why :func:`lower` refuses the shape of a fresh *item*, or ``None``.
    It lowers once with a placeholder value of each kind. The save refuses
    such an item, and the matcher never offers it."""
    shape = item.get("query") or {}
    src = by_module.get(str(shape.get("module")))
    ops = {str(w.get("param")): str(w.get("op")) for w in shape.get("where") or []}
    fields = {str(w.get("param")): str(w.get("field")) for w in shape.get("where") or []}
    values: dict[str, str] = {}
    for p in item.get("params") or []:
        name, kind = str(p["name"]), str(p["kind"])
        value = _PLACEHOLDERS.get(kind, "x")
        if kind == "option" and src is not None:
            found = src.field(fields.get(name))
            value = found.options[0] if found is not None and found.options else "x"
        if ops.get(name) == "between":
            value = f"{value}..{value}"
        values[name] = value
    try:
        lower(item, values, measures, by_module)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return str(detail.get("why") or detail.get("code") or exc.status_code)
    return None


def lower(item: Mapping[str, Any], values: Mapping[str, Any],
          measures: Mapping[str, Mapping[str, Any]], by_module: Mapping[str, cat.Source],
          *, partial: bool = False) -> Lowered:
    """The parameters of ``GET /crm/aggregate`` for *item* with *values*.

    A ``measure`` gives its aggregate, its fields and its fixed filters. A
    condition gives ``field.<key>``, or ``.from`` and ``.to`` for a range or
    a time. One group field at most, with the bucket ``month`` for a date.
    With *partial* (the test run of a repair), a condition with no value is
    left out. 409 ``stale_query`` for a stale item, and 422
    ``not_runnable`` for a shape that the aggregate does not take."""
    reason = stale_reason(item, by_module, measures)
    if reason is not None:
        raise _refuse("stale_query", "This verified question no longer fits the CRM.", 409,
                      stale_reason=reason)
    shape = item["query"]
    src = by_module[str(shape["module"])]
    given = _values(item, values, partial=partial)
    name = shape.get("measure")
    if name is not None:
        measure = measures[str(name)]
        parts, pairs = _measure_parts(src, str(measure["aggregate"]),
                                      measure.get("fields") or [],
                                      measure.get("filters") or [])
    else:
        parts, pairs = _measure_parts(src, str(shape.get("aggregate") or "count"),
                                      shape.get("fields") or [], [])
    kinds = {str(p["name"]): str(p["kind"]) for p in item.get("params") or []}
    for cond in shape.get("where") or []:
        if cond["param"] in given:
            value = given[cond["param"]]
            if kinds.get(cond["param"]) == "option":
                value = _option(src, cond, value)
            pairs.extend(_condition(src, cond, value))
    names = [n for n, _v in pairs]
    if len(set(names)) != len(names):
        # A condition never overwrites a fixed filter or another condition.
        raise _not_runnable("two conditions on one field")
    group = [str(g) for g in shape.get("group_by") or []]
    if len(group) > 1:
        raise _not_runnable("more than one group field")
    group_field = group[0] if group else None
    found = src.field(group_field) if group_field else None
    bucket = "month" if found is not None and found.type in DATE_TYPES else None
    return Lowered(entity=src.entity, group_field=group_field, bucket=bucket,
                   pairs=tuple(pairs), **parts)


# ── One repair (§6.3) ───────────────────────────────────────────────────────


def _types_for(role: str, kind: str | None, aggregate: str | None) -> frozenset[str] | None:
    """The field types that may stand for a dropped field. ``None`` means
    any type."""
    if role == "measure":
        return None if aggregate == "count" else frozenset(cat.NUMBER_TYPES)
    if role == "group":
        return frozenset({*OPTION_TYPES, *DATE_TYPES, "lookup"})
    if kind == "record":
        return frozenset({"lookup"})
    if kind == "option":
        return frozenset(OPTION_TYPES)
    if kind in DATE_KINDS:
        return frozenset(DATE_TYPES)
    if kind == "number":
        return frozenset(cat.NUMBER_TYPES)
    return None


def _closest(src: cat.Source, gone: str, types: frozenset[str] | None,
             used: set[str]) -> str | None:
    """The field of *src* that clearly stands for the dropped key *gone*,
    of a type in *types*, or ``None``. The words are the term matcher's.

    1. A field whose key words or label words are the words of *gone*
       wins. Two such fields are a tie with no winner.
    2. Else the ONE field with the most words in common, at least two. A
       tie at the top has no winner, also when the tied fields share the
       same words ("Net Value Excl Tax" and "Net Value Incl Tax"). The fill
       share never picks a field, because the repair must keep the output.
    3. A field that shares one word, or no word, never stands: "Net Value"
       could be "Net Amount" or "Billed Value", and the repair must keep
       the output."""
    want = set(terms.words(gone))
    pool = [f for f in src.fields if f.key not in used
            and (types is None or f.type in types)]
    exact = [f for f in pool
             if set(terms.words(f.key)) == want or set(terms.words(f.label)) == want]
    if exact:
        return exact[0].key if len(exact) == 1 else None
    scored = []
    for f in pool:
        common = frozenset(want & {*terms.words(f.key), *terms.words(f.label)})
        scored.append((common, f.key))
    if not scored:
        return None
    top = max(len(s[0]) for s in scored)
    if top < 2:
        return None
    best = [s for s in scored if len(s[0]) == top]
    return best[0][1] if len(best) == 1 else None


def repair(item: Mapping[str, Any], by_module: Mapping[str, cat.Source],
           measures: Mapping[str, Mapping[str, Any]], version: int) -> dict[str, Any] | None:
    """ONE repaired version of a stale ``query`` item, or ``None``.

    Only a dropped field of the query, or a retyped field of its sum, is
    repaired. A dropped module or measure has no repair here. A retyped
    condition field (``type:<field>`` from a ``where``) has no repair
    either: the field is still there, so :func:`fix` keeps it, and the
    repaired item stays stale. ``propose_repair`` then answers
    ``no_repair``. Each such field maps to
    the closest remaining field of the module (:func:`_closest`). The
    parameters, the aggregate, the measure and the question stay, so the
    output stays. The repair is a pure function of the item and the
    catalogue: one catalogue shape gives one repair at most."""
    reason = stale_reason(item, by_module, measures)
    if reason is None or not reason.startswith(("field:", "type:")):
        return None
    shape = dict(item["query"])
    src = by_module[str(shape["module"])]
    kinds = {str(p["name"]): str(p["kind"]) for p in item.get("params") or []}
    aggregate = shape.get("aggregate")
    used = {k for k in _shape_keys(shape) if src.field(k) is not None}
    mapping: dict[str, str] = {}

    def fix(key: str, role: str, kind: str | None) -> str | None:
        found = src.field(key)
        fits = found is not None and (role != "measure" or aggregate == "count"
                                      or found.type in cat.NUMBER_TYPES)
        if fits:
            return key
        if key not in mapping:
            new = _closest(src, key, _types_for(role, kind, aggregate), used - {key})
            if new is None:
                return None
            mapping[key] = new
            used.add(new)
        return mapping[key]

    fields = [fix(str(k), "measure", None) for k in shape.get("fields") or []]
    where = [{**w, "field": fix(str(w["field"]), "where", kinds.get(str(w["param"])))}
             for w in shape.get("where") or []]
    group = [fix(str(g), "group", None) for g in shape.get("group_by") or []]
    if None in fields or None in group or any(w["field"] is None for w in where):
        return None
    if "fields" in shape:
        shape["fields"] = fields
    if "where" in shape:
        shape["where"] = where
    if "group_by" in shape:
        shape["group_by"] = group
    fixed = {**dict(item), "query": shape, "model_version": int(version)}
    if cat.semantic_problem(fixed) is not None:
        return None
    if stale_reason(fixed, by_module, measures) is not None:
        return None
    return fixed


# ── The reads and the writes ────────────────────────────────────────────────


async def _state(db: Any, org: str) -> tuple[dict[str, cat.Source], list[dict[str, Any]]]:
    """The catalogue sources by module, and the items of the approved
    version, in the caller's tenant session. No record count is read."""
    from gateway.routes.crm.catalogue import _load

    _mode, sources, _roles, others = await _load(db, org, count=False, core_counts=False)
    return {src.module: src for src in sources}, others


def _find(others: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    for item in queries_of(others):
        if item["key"] == key:
            return item
    raise _refuse("no_query", "No verified question has this key.", 404)


async def save(user: UserContext, payload: SaveIn) -> Any:
    """An admin saves a verified question (§6.3 "Save"). It is a new
    approved version of the plan, through :func:`mapping_plans.commit`, so
    Undo restores the version before. An open draft moves onto the new
    version (:func:`mapping_plans.carry_draft`). Returns the plan row.

    ⚠️ The library half: no route calls this yet. Its HTTP route arrives in
    the follow-up slice SEM-8b, with the chat tool that the CRM manifest
    fence asks for.

    A save of the SAME question under its key is an update. A DIFFERENT
    question under a key that the plan holds is a 409 ``key_exists``. An
    item that does not fit the catalogue is a 422 ``stale_query``, and a
    shape that cannot run is a 422 ``not_runnable``."""
    from gateway.routes.crm.mapping_plans import _next_version

    org = await _require_admin(user)
    by = actor(user).lower()
    async with _tenant_session() as db:
        approved = await lock_plan(db, by)
        by_module, _others = await _state(db, org)
        fields, others = split_rules(_rules_of(approved))
        measures = measures_of(others)
        # The version that this save commits: the lock is held, so commit
        # takes the same next version.
        item = build_item(payload, by_module, await _next_version(db), measures)
        reason = stale_reason(item, by_module, measures)
        if reason is not None:
            raise _refuse("stale_query", "The question does not fit the CRM of today.",
                          stale_reason=reason)
        held = [o for o in queries_of(others) if o["key"] == item["key"]]
        if held and held[0]["question"] != item["question"]:
            raise _refuse("key_exists", "Another verified question holds this key.", 409)
        draft = await open_draft(db, approved)
        plan = await commit(
            db, org=org, fields=fields, others=merge_kinds(others, [item], semantic=True),
            by=by, based_on=int(approved.version),
        )
        if draft is not None:
            await carry_draft(db, draft, plan, org=org)
    _log.info("crm.verified_query_saved version=%s params=%s", plan.version,
              len(item["params"]))
    return plan


async def _compute(lowered: Lowered, user: UserContext) -> Any:
    """``aggregate.compute``, the one body of ``GET /crm/aggregate``, with
    the bound tenant and *user*. It reads only."""
    from gateway.routes.crm.aggregate import compute
    from gateway.routes.crm.records import ListParams

    return await compute(
        lowered.entity, measure=lowered.measure, field=lowered.field,
        group_field=lowered.group_field, bucket=lowered.bucket, pairs=list(lowered.pairs),
        params=ListParams(direction="desc", page=1, page_size=50), user=user,
        fields=lowered.fields,
    )


async def run_query(user: UserContext, key: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Run the verified question *key* as *user*, read-only, through the
    one aggregate body. The answer carries ``verified``. A stale question
    is a 409 ``stale_query`` and never runs.

    ⚠️ The library half: no route calls this yet. Its HTTP route arrives in
    the follow-up slice SEM-8b. The chat runs a verified question through
    the lower route and ``GET /crm/aggregate``, with the same pairs."""
    org = _org(user)
    async with _tenant_session() as db:
        by_module, others = await _state(db, org)
    item = _find(others, key)
    lowered = lower(item, values, measures_of(others), by_module)
    answer = await _compute(lowered, user)
    return {"key": key, "question": item["question"], "verified": True,
            "model_version": int(item["model_version"]), "answer": answer}


async def propose_repair(user: UserContext, key: str,
                         values: Mapping[str, Any] | None = None) -> Any:
    """ONE repair of the stale verified question *key* (§6.3 "Stale"), for
    an admin. It runs the repair read-only with *values* (a condition with
    no value is left out), and keeps it as a draft item only when it runs.
    Returns the draft row. 409 ``not_stale``, ``repair_pending`` (a draft
    holds a change of this question), ``no_repair`` or ``repair_failed``.

    ⚠️ The library half: no route calls this yet. Its HTTP route arrives in
    the follow-up slice SEM-8b."""
    org = await _require_admin(user)
    async with _tenant_session() as db:
        approved = await _approved(db)
        by_module, others = await _state(db, org)
        draft = await open_draft(db, approved) if approved is not None else None
    item = _find(others, key)
    measures = measures_of(others)
    if stale_reason(item, by_module, measures) is None:
        raise _refuse("not_stale", "This verified question fits the CRM.", 409)
    if draft is not None:
        ident = kind_key(item, semantic=True)
        pending = [o for o in split_rules(_rules_of(draft))[1]
                   if kind_key(o, semantic=True) == ident]
        if pending != [item]:
            raise _refuse("repair_pending", "A draft already changes this question.", 409)
    fixed = repair(item, by_module, measures, int(approved.version))
    if fixed is None:
        raise _refuse("no_repair", "No field of the CRM can stand for the one that left.", 409)
    try:
        await _compute(lower(fixed, values or {}, measures, by_module, partial=True), user)
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        _log.info("crm.verified_query_repair_failed status=%s", exc.status_code)
        raise _refuse("repair_failed", "The repaired question did not run.", 409,
                      cause=code or str(exc.status_code)) from None
    async with _tenant_session() as db:
        row = await write_draft(db, items=[fixed], by=SUGGEST_ACTOR, org=org)
    _log.info("crm.verified_query_repair_kept version=%s", getattr(row, "version", None))
    return row


# ── The routes ──────────────────────────────────────────────────────────────


class ParamModel(BaseModel):
    name: str
    kind: str


class MatchModel(BaseModel):
    key: str
    question: str
    params: list[ParamModel]
    model_version: int


class StaleModel(BaseModel):
    key: str
    question: str
    #: Our own name of the part that left: ``field:<key>`` and the like.
    stale_reason: str


class MatchesModel(BaseModel):
    matches: list[MatchModel]
    #: For an admin only: each stale verified question, with its reason.
    stale: list[StaleModel] = Field(default_factory=list)


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The member's words. Read once to match, never kept and never logged.
    q: str = Field(default="", max_length=ASKED_MAX)


class LoweredModel(BaseModel):
    key: str
    question: str
    #: The mark of an answer from a verified question.
    verified: bool = True
    model_version: int
    entity: str
    #: The parameters of ``GET /crm/aggregate``.
    aggregate: dict[str, str]


def _wire(item: Mapping[str, Any]) -> MatchModel:
    return MatchModel(key=str(item["key"]), question=str(item["question"]),
                      params=[ParamModel(**p) for p in item.get("params") or []],
                      model_version=int(item["model_version"]))


@router.post("/verified-queries/match", response_model=MatchesModel)
async def match_verified_queries(
    payload: MatchIn, user: UserContext = Depends(get_current_user),
) -> MatchesModel:
    """The verified questions that match the words *q*, with the
    parameters that the member must give. A stale question never matches.
    An admin also gets each stale question and its reason. A POST, so the
    member's words stay out of each URL and each access log."""
    org = _org(user)
    async with _tenant_session() as db:
        by_module, others = await _state(db, org)
    measures = measures_of(others)
    fresh, gone = [], []
    for item in queries_of(others):
        reason = stale_reason(item, by_module, measures)
        if reason is None and runnable_problem(item, measures, by_module) is not None:
            # A shape that cannot run is never offered.
            continue
        (fresh if reason is None else gone).append((item, reason))
    found = match(payload.q, [i for i, _r in fresh]) if payload.q.strip() else []
    stale = ([StaleModel(key=str(i["key"]), question=str(i["question"]), stale_reason=str(r))
              for i, r in gone] if user.has_permission(ADMIN_PERMISSION) else [])
    return MatchesModel(matches=[_wire(i) for i in found], stale=stale)


@router.get("/verified-queries/{key}", response_model=LoweredModel)
async def lower_verified_query(
    key: str, request: Request, user: UserContext = Depends(get_current_user),
) -> LoweredModel:
    """The verified question *key* in the parameters of
    ``GET /crm/aggregate``, with the values ``param.<name>=<value>``. It
    reads no record. The chat tool then reads the aggregate as the member,
    so the member's own visibility holds."""
    org = _org(user)
    if not _KEY.fullmatch(key):
        raise _refuse("no_query", "No verified question has this key.", 404)
    values = {k[len(PARAM_PREFIX):]: v for k, v in request.query_params.items()
              if k.startswith(PARAM_PREFIX)}
    async with _tenant_session() as db:
        by_module, others = await _state(db, org)
    item = _find(others, key)
    try:
        lowered = lower(item, values, measures_of(others), by_module)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "stale_query" and not user.has_permission(ADMIN_PERMISSION):
            # The reason names our parts, for an admin only.
            raise _refuse("stale_query", str(detail.get("message")), 409) from None
        raise
    return LoweredModel(key=key, question=str(item["question"]),
                        model_version=int(item["model_version"]), entity=lowered.entity,
                        aggregate=lowered.params())


__all__ = [
    "GLUE_WORDS",
    "MATCH_MAX",
    "PARAM_PREFIX",
    "VALUE_MAX",
    "ConditionIn",
    "Lowered",
    "SaveIn",
    "build_item",
    "lower",
    "match",
    "measures_of",
    "period",
    "propose_repair",
    "queries_of",
    "repair",
    "run_query",
    "runnable_problem",
    "save",
    "stale_reason",
    "wanted_words",
]
