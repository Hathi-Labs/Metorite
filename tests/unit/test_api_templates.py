"""API-1: the template grammar, its validator, the one builder and the flag.

Spec: ``project-docs/specs/api_connections.md`` §2.1, §2.7, §2.10 and §5
(the first row). Decision D228. Task 3787.

The fence holds these rules:

* Each template is valid: https hosts, a class on each op, no free path, a
  shape on each query key.
* A path shape refuses ``/ ? # %`` and ``..``, and the builder refuses and
  percent-encodes a path value.
* The Zoho Books template takes each host from ``ZOHO_DATA_CENTRES``, reads
  only, and marks the invoice amount for the gateway total.
* A template with each defect fails, and the error names the rule.
* ``API_CONNECTIONS_ORGS`` is declared, empty by default, and has one reader.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from acb_common.api_connections_flag import api_connections_allowed
from acb_common.app_manifests.base import (
    UUID_PATTERN,
    App,
    BadCall,
    Route,
    build_route_call,
    percent_encode,
)
from acb_common.settings import Settings, get_settings
from gateway import outbound_guard
from gateway.connections.api import CLIENT_HOSTS, TEMPLATES, template
from gateway.connections.api.template import (
    MAX_ANSWER_CAP,
    MAX_PAGE_SIZE,
    MAX_PER_DAY,
    MAX_PER_MINUTE,
    MAX_TOTAL_PAGES,
    PATH_PROBES,
    Limits,
    Operation,
    Paging,
    SignIn,
    TemplateError,
    Total,
    build,
    path_shape_is_closed,
    query_shape_is_closed,
    validate,
)
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.crm_sources.zoho.auth import ZOHO_DATA_CENTRES

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "apps" / "services" / "gateway" / "gateway" / "connections" / "api"


def _with(**changes) -> object:
    return dataclasses.replace(ZOHO_BOOKS, **changes)


def _op(**changes) -> Operation:
    base = Operation(name="read_thing", method="GET", path="/v1/things", cls="A",
                     title="Read the things.")
    return dataclasses.replace(base, **changes)


def _with_op(op: Operation) -> object:
    return _with(operations=(*ZOHO_BOOKS.operations, op))


def _rule(tpl: object) -> str:
    with pytest.raises(TemplateError) as caught:
        validate(tpl, CLIENT_HOSTS)
    return caught.value.rule


# ── Each template is valid ──────────────────────────────────────────────────


def test_each_template_passes_the_validator():
    assert TEMPLATES, "the registry holds the Zoho Books template"
    for one in TEMPLATES.values():
        validate(one, CLIENT_HOSTS)
    assert template("zoho_books") is ZOHO_BOOKS
    assert template("nope") is None
    assert template(None) is None
    assert ZOHO_BOOKS.provider == "api:zoho_books"


def test_each_host_is_a_bare_https_origin():
    for one in TEMPLATES.values():
        for _, origin in one.hosts:
            assert origin.startswith("https://")
            assert origin.count("/") == 2 and ":" not in origin[len("https://"):]


def test_each_op_has_a_class_and_each_query_key_has_a_shape():
    for one in TEMPLATES.values():
        for op in one.operations:
            assert op.cls in ("A", "B", "C"), op.name
            for key, shape in op.query:
                assert shape, f"{op.name}.{key}"


def test_no_op_has_a_free_path():
    for one in TEMPLATES.values():
        for op in one.operations:
            shapes = dict(op.path_shapes)
            for name in op.route.path.split("{")[1:]:
                key = name.split("}")[0]
                assert key in shapes, f"{op.name}.{key}"
                assert path_shape_is_closed(shapes[key]), f"{op.name}.{key}"


def test_the_answer_cap_is_the_cap_of_the_guard():
    assert MAX_ANSWER_CAP == outbound_guard.MAX_ANSWER_BYTES


# ── Zoho Books ──────────────────────────────────────────────────────────────


def test_zoho_books_takes_each_host_from_the_one_zoho_list():
    hosts = {origin.removeprefix("https://") for _, origin in ZOHO_BOOKS.hosts}
    assert hosts == {dc.api_host for dc in ZOHO_DATA_CENTRES.values()}
    assert dict(ZOHO_BOOKS.hosts) == {
        region: f"https://{dc.api_host}" for region, dc in ZOHO_DATA_CENTRES.items()}
    assert {o for _, o in ZOHO_BOOKS.hosts} <= CLIENT_HOSTS["zoho"]


def test_zoho_books_names_no_zoho_host_of_its_own():
    text = (API_DIR / "zoho_books.py").read_text(encoding="utf-8")
    assert "zohoapis" not in text
    assert "accounts.zoho" not in text


def test_zoho_books_reads_only():
    assert ZOHO_BOOKS.operations
    for op in ZOHO_BOOKS.operations:
        assert op.cls == "A", op.name
        assert op.method == "GET", op.name


def test_zoho_books_signs_in_with_the_crm_client_and_sets_the_org():
    assert ZOHO_BOOKS.sign_in == SignIn(kind="oauth_code", header="Authorization",
                                        scheme="Zoho-oauthtoken", client="zoho")
    assert ZOHO_BOOKS.context == ("organization_id",)


def test_zoho_books_marks_the_invoice_amount_for_the_total():
    op = ZOHO_BOOKS.op("list_invoices")
    assert op is not None and op.total == Total(items="invoices", field="total")
    assert op.paging is not None and op.paging.size <= MAX_PAGE_SIZE
    assert op.total.max_pages == MAX_TOTAL_PAGES


def test_an_argument_that_names_a_context_key_is_refused():
    with pytest.raises(BadCall) as caught:
        build(ZOHO_BOOKS, "list_invoices", {"organization_id": "123"})
    assert caught.value.why == "context"
    with pytest.raises(BadCall) as caught:
        build_route_call(ZOHO_BOOKS.op("list_invoices").route, {"organization_id": "1"})
    assert caught.value.why == "unknown"


def test_the_query_keys_of_an_op_are_a_closed_list():
    call = build(ZOHO_BOOKS, "list_invoices", {"status": "unpaid", "page": 2, "per_page": 40})
    assert call.path == "/books/v3/invoices"
    assert call.query == (("page", "2"), ("per_page", "40"), ("status", "unpaid"))
    with pytest.raises(BadCall) as caught:
        build(ZOHO_BOOKS, "list_invoices", {"filter_by": "Status.All"})
    assert (caught.value.name, caught.value.why) == ("filter_by", "unknown")
    with pytest.raises(BadCall) as caught:
        build(ZOHO_BOOKS, "list_invoices", {"status": "unpaid&x=1"})
    assert caught.value.why == "shape"
    with pytest.raises(BadCall):
        build(ZOHO_BOOKS, "delete_invoice", {})


@pytest.mark.parametrize("value", [
    "..", "%2e%2e", "1/2", "1?x", "1#x", "1%", "\uff0f", "\u2215", "1\x00", "", "12 3",
])
def test_the_template_path_shape_refuses_a_value_that_leaves_its_place(value):
    with pytest.raises(BadCall) as caught:
        build(ZOHO_BOOKS, "get_invoice", {"invoice_id": value})
    assert caught.value.why in ("shape", "missing")


# ── Path shapes ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", [
    UUID_PATTERN, r"[0-9]{1,20}", r"[A-Za-z0-9_\-]{1,64}", r"[a-z]{2}-[0-9]{4}",
])
def test_a_closed_path_shape_passes(pattern):
    assert path_shape_is_closed(pattern)


@pytest.mark.parametrize("pattern", [
    r".+", r".{1,9}", r"[^/]+", r"[^/]{1,9}", r"\w{1,9}", r"\S{1,9}", r"(?i)[a-z]{1,9}",
    r"[a-z/]{1,9}", r"[a-z?]{1,9}", r"[a-z#]{1,9}", r"[a-z%]{1,9}", r"[a-z.]{1,9}",
    r"[a-z]{0,9}", r"[a-z]+", r"(a|b)", r"[\x00-\x7f]{1,9}", r"[a-z\u2215]{1,9}", "",
])
def test_an_open_path_shape_fails(pattern):
    assert not path_shape_is_closed(pattern)


def test_the_probes_name_each_breaker():
    for text in ("/", "?", "#", "%", "..", "%2e%2e", "\x00", "\uff0f", "\u2215", ""):
        assert text in PATH_PROBES


# ── The one builder ─────────────────────────────────────────────────────────


def _loose_route(shape: str) -> Route:
    """An API row with a loose shape, made by hand. The validator would refuse
    it. It shows that the builder still holds the path."""
    return Route("GET", "/v1/files/{name}", tool="", cls="A", op="read_file",
                 title="Read a file.", path_shapes=(("name", shape),), api=True)


@pytest.mark.parametrize("value", [
    "..", "a/../b", "%2e%2e", "a%2fb", "/", "a/b", "?", "a?b", "#", "a#b", "%", "a\x00b", "a\\b",
])
def test_the_builder_refuses_a_breaker_in_a_path_value(value):
    with pytest.raises(BadCall) as caught:
        build_route_call(_loose_route(r".+"), {"name": value})
    assert (caught.value.name, caught.value.why) == ("name", "shape")


@pytest.mark.parametrize("value", [".", "...", "...."])
def test_the_builder_refuses_a_dot_segment(value):
    with pytest.raises(BadCall) as caught:
        build_route_call(_loose_route(r".+"), {"name": value})
    assert (caught.value.name, caught.value.why) == ("name", "shape")


def test_no_door_shape_takes_a_dot():
    from acb_common.app_manifests import APPS

    for app in APPS:
        for row in app.routes:
            for name in row.path.split("{")[1:]:
                key = name.split("}")[0]
                shape = dict(row.path_shapes).get(key, UUID_PATTERN)
                assert not re.fullmatch(shape, "."), (app.id, row.path, key)


@pytest.mark.parametrize("pattern", ["[a-z]{\u0661}", "[0-9]{1,\u0669}"])
def test_a_shape_with_a_unicode_digit_count_fails(pattern):
    assert not path_shape_is_closed(pattern)
    assert not query_shape_is_closed(pattern)


@pytest.mark.parametrize(("value", "encoded"), [
    ("a b", "a%20b"),
    ("\uff0f", "%EF%BC%8F"),
    ("\u2215x", "%E2%88%95x"),
    ("a+b", "a%2Bb"),
    ("a&b=c", "a%26b%3Dc"),
    ("abc-1_2", "abc-1_2"),
])
def test_the_builder_percent_encodes_a_path_value(value, encoded):
    call = build_route_call(_loose_route(r".+"), {"name": value})
    assert call.path == f"/v1/files/{encoded}"
    assert call.template == "/v1/files/{name}"


@pytest.mark.parametrize("value", [
    "abc", "a b", "\uff0f", "\u2215", "a+b&c=d", "~._-", "été", "\U0001f600", "'\"<>",
])
def test_the_encoder_agrees_with_urllib(value):
    assert percent_encode(value) == quote(value, safe="")


def test_the_builder_closes_the_query_of_an_api_row_and_the_door_stays_open():
    route = Route("GET", "/v1/files", tool="", cls="A", op="list_files", title="List.",
                  query_shapes=(("q", r"[a-z]{1,9}"),), api=True)
    assert build_route_call(route, {"q": "abc"}).query == (("q", "abc"),)
    with pytest.raises(BadCall):
        build_route_call(route, {"other": "x"})
    # The door passes open_query, so a key with no shape is a query key.
    assert build_route_call(route, {"other": "x"}, open_query=True).query == (("other", "x"),)


def test_an_app_refuses_an_api_row():
    row = Route("GET", "/v1/files", tool="", cls="A", op="list_files", title="List.", api=True)
    with pytest.raises(ValueError, match="API op"):
        App(id="files", name="Files", reach="x", pane="/files", feature="feature:files",
            router="x", routes=(row,))


def test_an_api_row_names_no_chat_tool():
    with pytest.raises(ValueError, match="no chat tool"):
        Route("GET", "/v1/files", tool="find", cls="A", op="list_files", title="List.", api=True)


# ── Mutation: each defect fails by name ────────────────────────────────────


@pytest.mark.parametrize(("hosts", "rule"), [
    ((("us", "http://www.zohoapis.com"),), "http_host"),
    ((("us", "www.zohoapis.com"),), "http_host"),
    ((("us", "https://www.zohoapis.com:8443"),), "http_host"),
    ((("us", "https://www.zohoapis.com/books"),), "http_host"),
    ((("us", "https://127.0.0.1"),), "http_host"),
    ((("us", "https://localhost"),), "http_host"),
    ((("us", "https://www.zohoapis.com"), ("us", "https://www.zohoapis.eu")), "http_host"),
    ((), "http_host"),
    ((("us", "https://books.example.com"),), "foreign_host"),
    ((("xx", "https://www.zohoapis.com.evil.example"),), "foreign_host"),
])
def test_a_bad_host_fails_by_name(hosts, rule):
    assert _rule(_with(hosts=hosts)) == rule


def test_an_unknown_client_fails_by_name():
    sign_in = dataclasses.replace(ZOHO_BOOKS.sign_in, client="hubspot")
    assert _rule(_with(sign_in=sign_in)) == "foreign_host"


@pytest.mark.parametrize("cls", ["", "X", "a", "D"])
def test_an_op_with_no_class_fails_by_name(cls):
    assert _rule(_with_op(_op(cls=cls))) == "no_class"


@pytest.mark.parametrize("query", [(("status", ""),), (("status", None),),
                                   (("status", "x"), ("status", "y"))])
def test_a_query_key_with_no_shape_fails_by_name(query):
    assert _rule(_with_op(_op(query=query))) == "query_no_shape"


@pytest.mark.parametrize("path", [
    "/v1/{rest}", "v1/things", "/v1/things?x=1", "/v1/things#x", "/v1/%2e%2e", "/v1/../x",
    "/v1//x", "/v1/things/", "/v1/a{id}", "/v1/{id}/{id}", "/v1/x.y",
])
def test_a_free_path_fails_by_name(path):
    op = _op(path=path, path_shapes=(("id", r"[0-9]{1,9}"),) if "{id}" in path else ())
    assert _rule(_with_op(op)) == "free_path"


@pytest.mark.parametrize("shape", [
    r".+", r"[^/]{1,9}", r"[a-z/]{1,9}", r"[a-z?]{1,9}", r"[a-z#]{1,9}", r"[a-z%]{1,9}",
    r"[a-z.]{1,9}", r"\w{1,9}", r"[a-z]{0,9}",
])
def test_an_open_path_shape_fails_by_name(shape):
    op = _op(path="/v1/things/{id}", path_shapes=(("id", shape),))
    assert _rule(_with_op(op)) == "open_path_shape"


def test_a_duplicate_op_fails_by_name():
    first = ZOHO_BOOKS.operations[0]
    assert _rule(_with_op(dataclasses.replace(first, title="Again."))) == "duplicate_op"


@pytest.mark.parametrize("cls", ["B", "C"])
def test_a_write_with_no_confirm_fails_by_name(cls):
    assert _rule(_with_op(_op(cls=cls, method="POST"))) == "write_no_confirm"
    validate(_with_op(_op(cls=cls, method="POST", always_confirm=True)), CLIENT_HOSTS)


def test_a_read_that_is_not_a_get_fails_by_name():
    assert _rule(_with_op(_op(method="POST"))) == "method"
    assert _rule(_with_op(_op(method="TRACE"))) == "method"


@pytest.mark.parametrize("changes", [
    {"answer_cap": MAX_ANSWER_CAP + 1},
    {"answer_cap": 0},
    {"query": (("page", r"[0-9]{1,4}"), ("per_page", r"[0-9]{1,3}")),
     "paging": Paging("page", "per_page", MAX_PAGE_SIZE + 1)},
    {"query": (("page", r"[0-9]{1,4}"), ("per_page", r"[1-4]{0,1}[0-9]{1}")),
     "paging": Paging("page", "per_page", 50),
     "total": Total("items", "amount", max_pages=MAX_TOTAL_PAGES + 1)},
])
def test_an_op_over_a_cap_fails_by_name(changes):
    assert _rule(_with_op(_op(**changes))) == "over_cap"


@pytest.mark.parametrize("limits", [
    Limits(per_minute=0), Limits(per_minute=10, per_day=0), Limits(per_minute=True),
    Limits(per_minute=MAX_PER_MINUTE + 1), Limits(per_minute=100, per_day=MAX_PER_DAY + 1),
])
def test_a_limit_out_of_range_fails_by_name(limits):
    assert _rule(_with(limits=limits)) == "over_cap"


def test_a_limit_at_its_cap_passes():
    validate(_with(limits=Limits(per_minute=MAX_PER_MINUTE, per_day=MAX_PER_DAY)), CLIENT_HOSTS)
    assert ZOHO_BOOKS.limits.per_minute == 100


# ── Query shapes (review P2) ────────────────────────────────────────────────


@pytest.mark.parametrize("shape", [
    r".*", r"[^x]+", "[a-z&=#\n]+", r"(?s).+", r"\d+", r"\d{1,9}", r"\w{1,9}", r"[a-z]+",
    r"[a-z]*", r"(a|b)", r"a|b", r"[a-z.]{1,9}", r"[a-z,]{1,9}", r"[a-z:]{1,9}",
    r"[a-z+]{1,9}", r"[a-z@]{1,9}", r"[a-z ]{1,9}", r"[a-z]{0,9}", r"(?i)[a-z]{1,9}",
    "[a-z]{\u0661}", "[a-z]{1,\u0669}",
])
def test_an_open_query_shape_fails_by_name(shape):
    assert not query_shape_is_closed(shape)
    assert _rule(_with_op(_op(query=(("q", shape),)))) == "query_open_shape"


@pytest.mark.parametrize("shape", [
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}", r"[a-z_]{1,24}", r"[AD]{1}", r"[0-9]{1,20}",
    r"[1-4]{0,1}[0-9]{1}",
])
def test_a_closed_query_shape_passes(shape):
    assert query_shape_is_closed(shape)


def test_each_zoho_query_shape_is_closed_and_takes_its_real_values():
    op = ZOHO_BOOKS.op("list_invoices")
    shapes = dict(op.query)
    for shape in shapes.values():
        assert query_shape_is_closed(shape)
    for key, value in (("date_start", "2026-01-31"), ("status", "partially_paid"),
                       ("sort_column", "due_date"), ("sort_order", "D"),
                       ("customer_id", "4600000000012345"), ("page", "12"), ("per_page", "49")):
        assert build(ZOHO_BOOKS, "list_invoices", {key: value}).query == ((key, value),)


@pytest.mark.parametrize("value", ["a&b=c", "x\n", "a#b", "a b", "\uff06", "a.b", "a,b"])
def test_a_query_value_with_a_breaker_is_refused(value):
    with pytest.raises(BadCall) as caught:
        build(ZOHO_BOOKS, "list_invoices", {"status": value})
    assert caught.value.why == "shape"


def test_a_door_row_with_an_open_query_shape_cannot_load():
    with pytest.raises(ValueError, match="not closed"):
        Route("GET", "/v1/files", tool="", cls="A", op="list_files", title="List.",
              query_shapes=(("q", r".*"),), api=True)


# ── The page size (review P3) ───────────────────────────────────────────────


@pytest.mark.parametrize("shape", [r"[0-9]{1,2}", r"[0-9]{1,3}", r"[1-5]{0,1}[0-9]{1}",
                                   r"[0-9]{3}"])
def test_a_size_shape_above_the_page_size_fails_by_name(shape):
    op = _op(query=(("page", r"[0-9]{1,4}"), ("per_page", shape)),
             paging=Paging("page", "per_page", 50))
    assert _rule(_with_op(op)) == "page"


def test_build_refuses_a_size_above_the_page_size():
    loose = _op(name="list_loose", query=(("page", r"[0-9]{1,4}"), ("per_page", r"[0-9]{1,3}")),
                paging=Paging("page", "per_page", 50))
    tpl = dataclasses.replace(ZOHO_BOOKS, operations=(loose,))
    for value in (51, "51", "999"):
        with pytest.raises(BadCall) as caught:
            build(tpl, "list_loose", {"per_page": value})
        assert (caught.value.name, caught.value.why) == ("per_page", "size")
    assert build(tpl, "list_loose", {"per_page": 50}).query == (("per_page", "50"),)
    with pytest.raises(BadCall) as caught:
        build(ZOHO_BOOKS, "list_invoices", {"per_page": 51})
    assert caught.value.why == "size"


@pytest.mark.parametrize(("field", "value"), [
    ("summary", "Reads https://books.example.com for the invoices."),
    ("summary", "Reads www.zohoapis.com for the invoices."),
    ("summary", "Reads books.example.com for the invoices."),
    ("summary", "Reads 10.0.0.8 for the invoices."),
    ("label", "Authorization: Zoho-oauthtoken"),
    ("summary", "Send the Bearer value."),
    ("summary", "The api_key goes here."),
    ("summary", "Uses 1000.0123456789abcdef0123456789abcdef as the key."),
    ("label", "a" * 40),
])
def test_a_secret_url_host_or_header_in_a_stored_field_fails_by_name(field, value):
    assert _rule(_with(**{field: value})) == "secret_in_field"


@pytest.mark.parametrize("sign_in", [
    SignIn(kind="oauth_code", header="Authorization", scheme="Zoho-oauthtoken 1000.abc",
           client="zoho"),
    SignIn(kind="oauth_code", header="Authorization: x", scheme="Zoho-oauthtoken",
           client="zoho"),
])
def test_a_value_in_a_sign_in_name_fails_by_name(sign_in):
    assert _rule(_with(sign_in=sign_in)) == "secret_in_field"


@pytest.mark.parametrize("sign_in", [
    SignIn(kind="oauth_code", header="Authorization", scheme="A" * 32, client="zoho"),
    SignIn(kind="oauth_code", header="X" * 44, scheme="Zoho-oauthtoken", client="zoho"),
    SignIn(kind="key_header", header="X-Refresh-Token-Abcdef0123456789abcdef0123456789"),
    SignIn(kind="oauth_code", header="Authorization", scheme="Oauthtoken", client="zoho"),
])
def test_a_secret_in_the_scheme_or_the_header_fails_by_name(sign_in):
    assert _rule(_with(sign_in=sign_in)) == "secret_in_field"


def test_the_real_zoho_scheme_and_header_pass():
    assert ZOHO_BOOKS.sign_in.scheme == "Zoho-oauthtoken"
    assert ZOHO_BOOKS.sign_in.header == "Authorization"
    validate(ZOHO_BOOKS, CLIENT_HOSTS)
    for header in ("X-API-Key", "Api-Key", "X-Key"):
        tpl = _with(sign_in=SignIn(kind="key_header", header=header),
                    hosts=(("us", "https://api.example.com"),))
        validate(tpl, CLIENT_HOSTS)


# ── Private hosts (review P3, advisory) ─────────────────────────────────────


@pytest.mark.parametrize("host", [
    "https://books.local", "https://api.internal", "https://a.localhost", "https://nas.lan",
    "https://router.home", "https://erp.corp", "https://xn--bcher-kva.example",
    "https://api.xn--p1ai",
])
def test_a_private_or_idn_host_with_no_client_fails_by_name(host):
    tpl = _with(sign_in=SignIn(kind="key_header", header="X-Key"), hosts=(("us", host),))
    assert _rule(tpl) == "private_host"


def test_a_client_host_list_still_rules_a_client_template():
    tpl = _with(hosts=(("us", "https://books.local"),))
    assert _rule(tpl) == "foreign_host"


def test_a_title_with_a_url_fails_by_name():
    assert _rule(_with_op(_op(title="Read https://x.example.com/v1."))) == "secret_in_field"


@pytest.mark.parametrize("sign_in", [
    SignIn(kind="cookie"),
    SignIn(kind="key_header"),
    SignIn(kind="key_query"),
    SignIn(kind="key_header", header="X-Key", query_key="key"),
    SignIn(kind="key_header", header="X-Key", client="zoho"),
])
def test_a_bad_sign_in_fails_by_name(sign_in):
    assert _rule(_with(sign_in=sign_in)) == "sign_in"


def test_an_op_that_names_a_context_key_fails_by_name():
    op = _op(query=(("organization_id", r"[0-9]{1,20}"),))
    assert _rule(_with_op(op)) == "context_arg"
    op = _op(path="/v1/{organization_id}", path_shapes=(("organization_id", r"[0-9]{1,20}"),))
    assert _rule(_with_op(op)) == "context_arg"


def test_a_shape_read_with_a_required_argument_fails_by_name():
    get = ZOHO_BOOKS.op("get_invoice")
    others = tuple(o for o in ZOHO_BOOKS.operations if o is not get)
    tpl = _with(operations=(*others, dataclasses.replace(get, shape_read=True)))
    assert _rule(tpl) == "shape_read"


def test_a_total_on_an_op_with_no_paging_fails_by_name():
    assert _rule(_with_op(_op(total=Total("items", "amount")))) == "total"


def test_paging_on_a_key_the_op_does_not_name_fails_by_name():
    assert _rule(_with_op(_op(paging=Paging("page", "per_page", 10)))) == "page"


@pytest.mark.parametrize(("changes", "rule"), [
    ({"id": "Zoho-Books"}, "id"),
    ({"label": ""}, "text"),
    ({"context": ("organization_id", "organization_id")}, "context"),
    ({"operations": ()}, "op_name"),
])
def test_a_bad_header_fails_by_name(changes, rule):
    assert _rule(_with(**changes)) == rule


def test_a_bad_op_name_fails_by_name():
    assert _rule(_with_op(_op(name="Read-Thing"))) == "op_name"


# ── Purity ──────────────────────────────────────────────────────────────────


def test_the_api_package_reads_no_environment_and_no_integration_resolver():
    for path in sorted(API_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert not (node.attr == "environ" and getattr(node.value, "id", "") == "os"), path
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", None) or ""
                for name in [module, *names]:
                    assert not name.startswith("acb_skills"), path
                    assert name not in ("os", "httpx", "requests"), path
                    assert "settings" not in name, path


# ── The flag ────────────────────────────────────────────────────────────────


def test_the_flag_is_declared_and_empty_by_default():
    assert Settings.model_fields["api_connections_orgs"].default == ""


def test_the_flag_is_off_by_default_and_reads_each_call(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_connections_orgs", "", raising=False)
    assert not api_connections_allowed("acme")
    monkeypatch.setattr(settings, "api_connections_orgs", "acme", raising=False)
    assert api_connections_allowed(" ACME")
    assert not api_connections_allowed("globex")
    assert not api_connections_allowed(None)
    assert not api_connections_allowed("")
    monkeypatch.setattr(settings, "api_connections_orgs", "*", raising=False)
    assert api_connections_allowed("globex")
    assert not api_connections_allowed(None)


def test_the_flag_has_one_reader():
    readers = []
    for base in (ROOT / "apps", ROOT / "packages"):
        for path in base.rglob("*.py"):
            if "api_connections_orgs" in path.read_text(encoding="utf-8", errors="ignore"):
                readers.append(path.relative_to(ROOT).as_posix())
    assert sorted(readers) == [
        "packages/acb_common/acb_common/api_connections_flag.py",
        "packages/acb_common/acb_common/settings.py",
    ]
