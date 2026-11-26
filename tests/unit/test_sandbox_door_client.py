"""SBX-F22 — the door client ``metorite_door`` (SBX-13).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.3 (done when 1 to 6), and
the header, the codes and the limits of §12.2.

The module is ``apps/services/orchestrator/sandbox/metorite_door.py``. It
runs in the sandbox image, so these tests load it by its path. A fake
transport (a ``requests`` adapter, mounted on the client's session) answers
each call, so the tests run the real ``requests`` path with no network.

**The source scan** is a set of checkers. Each returns a list of problems,
and each is proven on a bad source too, so a checker that stops seeing fails.

**The Docker half** carries the ``sandbox_docker`` marker. It runs a stub
door from the sandbox image on a test-only network, under the alias
``metorite-api.sandbox`` on port 80. A tool container on the same network
reads two pages through the client. The broker is not in the test.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from tests.unit.test_coding_sandbox_image import (  # noqa: F401 — a fixture, used by name
    _DOCKERFILE,
    _RUN_FLAGS,
    _docker,
    _instructions,
    coding_sandbox_image,
)

_REPO = Path(__file__).resolve().parents[2]
_CLIENT_PATH = _REPO / "apps" / "services" / "orchestrator" / "sandbox" / "metorite_door.py"
_SITE_PATH = "/usr/local/lib/python3.12/site-packages/metorite_door.py"


def _load_client() -> ModuleType:
    spec = importlib.util.spec_from_file_location("metorite_door_under_test", _CLIENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses read the module of a class
    spec.loader.exec_module(module)
    return module


D = _load_client()

TOKEN = "msbx_" + "A1b2_C3-d4" * 4 + "xyz"
assert len(TOKEN) == 48


# --------------------------------------------------------------------------
# The fake transport.
# --------------------------------------------------------------------------

Handler = Callable[[requests.PreparedRequest], tuple[int, Any, dict[str, str]]]


class FakeTransport(BaseAdapter):
    """Answers each request with ``handler``, and records each request."""

    def __init__(self, handler: Handler) -> None:
        super().__init__()
        self.handler = handler
        self.sent: list[requests.PreparedRequest] = []
        self.kwargs: list[dict[str, Any]] = []

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        self.sent.append(request)
        self.kwargs.append(kwargs)
        status, body, headers = self.handler(request)
        answer = requests.Response()
        answer.status_code = status
        answer._content = body if isinstance(body, bytes) else json.dumps(body).encode()
        answer.headers = CaseInsensitiveDict({"Content-Type": "application/json", **headers})
        answer.url = request.url or ""
        answer.request = request
        return answer

    def close(self) -> None:
        pass

    def queries(self) -> list[dict[str, list[str]]]:
        return [parse_qs(urlsplit(r.url or "").query) for r in self.sent]


def _door(handler: Handler) -> tuple[Any, FakeTransport]:
    door = D.Door()
    fake = FakeTransport(handler)
    door._session.mount("http://", fake)
    door._session.mount("https://", fake)
    return door, fake


def _answers(*steps: tuple[int, Any, dict[str, str]]) -> Handler:
    queue = list(steps)

    def handler(_: requests.PreparedRequest) -> tuple[int, Any, dict[str, str]]:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return handler


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    monkeypatch.setenv("METORITE_API_TOKEN", TOKEN)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    slept: list[float] = []
    monkeypatch.setattr(D, "_sleep", slept.append)
    return slept


# --------------------------------------------------------------------------
# Done when 1: the source scan of decision 3, and no host but DOOR_URL.
# --------------------------------------------------------------------------

_TOKEN_KEY = "METORITE_API_TOKEN"
_ENV_ATTRS = frozenset({"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"})
_ALLOWED_IMPORTS = frozenset(
    {"__future__", "os", "re", "time", "collections.abc", "dataclasses", "typing", "requests"}
)
_HTTP_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request", "send"}
)
_DOOR_URL = "http://metorite-api.sandbox"


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _is_os(node: ast.AST, attr: str | None = None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and (node.attr in _ENV_ATTRS if attr is None else node.attr == attr)
    )


def _is_token_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == _TOKEN_KEY


def env_problems(source: str) -> list[str]:
    """The module reads ``os.environ`` for ``METORITE_API_TOKEN`` only."""
    tree = ast.parse(source)
    allowed: set[int] = set()
    problems: list[str] = []
    for node in ast.walk(tree):
        # os.environ.get("METORITE_API_TOKEN") and os.getenv("METORITE_API_TOKEN")
        if isinstance(node, ast.Call) and node.args and _is_token_key(node.args[0]):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and _is_os(func.value, "environ")
                and len(node.args) <= 2
                and not node.keywords
            ):
                allowed.add(id(func.value))
            elif _is_os(func, "getenv") and len(node.args) <= 2 and not node.keywords:
                allowed.add(id(func))
        # os.environ["METORITE_API_TOKEN"], a read only
        if (
            isinstance(node, ast.Subscript)
            and _is_os(node.value, "environ")
            and _is_token_key(node.slice)
            and isinstance(node.ctx, ast.Load)
        ):
            allowed.add(id(node.value))
    for node in ast.walk(tree):
        if _is_os(node) and id(node) not in allowed:
            problems.append(
                f"line {node.lineno}: os.{node.attr} with a key other than {_TOKEN_KEY}"
            )  # type: ignore[attr-defined]
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            problems.append(f"line {node.lineno}: from os import ...")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" and alias.asname:
                    problems.append(f"line {node.lineno}: import os as {alias.asname}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "vars", "__import__")
        ):
            problems.append(f"line {node.lineno}: {node.func.id}() can reach the environment")
    return problems


def proxy_problems(source: str) -> list[str]:
    """No ``trust_env`` and no ``proxies=``: requests reads the proxy itself."""
    tree = ast.parse(source)
    docs = _docstrings(tree)
    names = {"trust_env", "proxies"}
    problems: list[str] = []
    for node in ast.walk(tree):
        found = None
        if isinstance(node, ast.Attribute) and node.attr in names:
            found = node.attr
        elif isinstance(node, ast.Name) and node.id in names:
            found = node.id
        elif isinstance(node, ast.keyword) and node.arg in names:
            found = node.arg
        elif (
            isinstance(node, ast.Constant)
            and id(node) not in docs
            and isinstance(node.value, str)
            and node.value in names
        ):
            found = node.value
        if found:
            problems.append(f"line {getattr(node, 'lineno', '?')}: {found}")
    return problems


def _is_door_url(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and isinstance(node.left, ast.Name)
        and node.left.id == "DOOR_URL"
    )


def url_problems(source: str) -> list[str]:
    """One host, ``DOOR_URL``. Each call of the session is a GET to DOOR_URL + path."""
    tree = ast.parse(source)
    docs = _docstrings(tree)
    problems: list[str] = []
    door_urls = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "DOOR_URL" for t in node.targets)
    ]
    if len(door_urls) != 1 or not (
        isinstance(door_urls[0], ast.Constant) and door_urls[0].value == _DOOR_URL
    ):
        problems.append(f"DOOR_URL is not the one constant {_DOOR_URL!r}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name not in _ALLOWED_IMPORTS]
            problems += [f"line {node.lineno}: import {b}" for b in bad]
        if isinstance(node, ast.ImportFrom) and (node.module or "") not in _ALLOWED_IMPORTS:
            problems.append(f"line {node.lineno}: from {node.module} import ...")
        if (
            isinstance(node, ast.Constant)
            and id(node) not in docs
            and isinstance(node.value, str)
            and "://" in node.value
            and not node.value.startswith(_DOOR_URL)
        ):
            problems.append(f"line {node.lineno}: a URL that is not DOOR_URL: {node.value!r}")
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _HTTP_VERBS
        ):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id == "requests":
            problems.append(f"line {node.lineno}: requests.{node.func.attr}() skips the session")
        is_session = (isinstance(owner, ast.Attribute) and owner.attr == "_session") or (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Attribute)
            and owner.func.attr == "Session"
        )
        if not is_session:
            continue
        if node.func.attr != "get":
            problems.append(f"line {node.lineno}: the session sends {node.func.attr}, not GET")
        url = (
            node.args[0]
            if node.args
            else next((k.value for k in node.keywords if k.arg == "url"), None)
        )
        if url is None or not _is_door_url(url):
            problems.append(f"line {node.lineno}: a session call whose URL is not DOOR_URL + path")
        redirects = next((k.value for k in node.keywords if k.arg == "allow_redirects"), None)
        if not (isinstance(redirects, ast.Constant) and redirects.value is False):
            problems.append(f"line {node.lineno}: a session call without allow_redirects=False")
    return problems


def _client_source() -> str:
    return _CLIENT_PATH.read_text(encoding="utf-8")


def test_the_client_reads_no_env_value_but_the_token() -> None:
    assert env_problems(_client_source()) == []


def test_the_client_never_touches_the_proxy_settings() -> None:
    assert proxy_problems(_client_source()) == []


def test_the_client_has_one_host_and_sends_get_only() -> None:
    assert url_problems(_client_source()) == []
    assert D.DOOR_URL == _DOOR_URL


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.environ.get('HOME')\n",
        "import os\nos.getenv('HTTP_PROXY')\n",
        "import os\nx = os.environ['PATH']\n",
        "import os\nos.environ['METORITE_API_TOKEN'] = 'x'\n",
        "import os\nfor k in os.environ: pass\n",
        "import os\nos.environb.get(b'X')\n",
        "from os import environ\n",
        "import os as o\n",
        "import os\ngetattr(os, 'environ')\n",
    ],
)
def test_the_env_scan_sees_each_other_read(source: str) -> None:
    assert env_problems(source)


@pytest.mark.parametrize(
    "source",
    [
        "s.trust_env = False\n",
        "s.get(u, proxies={})\n",
        "setattr(s, 'trust_env', False)\n",
        "proxies = {}\n",
    ],
)
def test_the_proxy_scan_sees_each_case(source: str) -> None:
    assert proxy_problems(source)


@pytest.mark.parametrize(
    "source",
    [
        "DOOR_URL = 'http://evil.example'\n",
        "DOOR_URL = 'http://metorite-api.sandbox'\nX = 'https://sandbox-api.metorite.com'\n",
        "import requests\nDOOR_URL = 'http://metorite-api.sandbox'\nrequests.get('x')\n",
        "DOOR_URL = 'http://metorite-api.sandbox'\nself._session.get(host + p, allow_redirects=False)\n",
        "DOOR_URL = 'http://metorite-api.sandbox'\nself._session.post(DOOR_URL + p, allow_redirects=False)\n",
        "DOOR_URL = 'http://metorite-api.sandbox'\nself._session.get(DOOR_URL + p)\n",
        "import urllib.request\nDOOR_URL = 'http://metorite-api.sandbox'\n",
        "import os\nDOOR_URL = os.environ.get('METORITE_API_TOKEN')\n",
    ],
)
def test_the_url_scan_sees_each_case(source: str) -> None:
    assert url_problems(source)


def test_a_netrc_adds_no_authorization_and_the_env_proxy_still_applies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """trust_env stays on, so requests reads the proxy. A netrc must not add auth."""
    netrc = tmp_path / "netrc"
    netrc.write_text("default login x password y\n", encoding="utf-8")
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    door, fake = _door(_answers((200, {"rows": [], "total": 0}, {})))
    assert door._session.trust_env is True
    door.get("/crm/leads")
    list(door.pages("/crm/leads"))
    assert len(fake.sent) == 2
    for sent, kwargs in zip(fake.sent, fake.kwargs, strict=True):
        assert "Authorization" not in sent.headers
        assert sent.headers["X-Sandbox-Capability"] == TOKEN
        assert kwargs["proxies"].get("http") == "http://proxy.example:3128"


def test_the_client_sends_the_capability_header_and_no_other_auth() -> None:
    door, fake = _door(_answers((200, {"rows": [], "total": 0}, {})))
    assert door.get("/crm/leads") == {"rows": [], "total": 0}
    (sent,) = fake.sent
    assert sent.method == "GET"
    assert sent.url == "http://metorite-api.sandbox/crm/leads"
    assert sent.headers["X-Sandbox-Capability"] == TOKEN
    for name in ("Authorization", "X-User-Email", "X-User-Role", "X-Actor-Via"):
        assert name not in sent.headers
    assert not [h for h in sent.headers if h.lower().startswith("x-agent-")]


@pytest.mark.parametrize(
    "path",
    [
        "crm/leads",
        "//evil.example/crm/leads",
        "http://evil.example/crm/leads",
        "/crm/../admin",
        "/crm/%2e%2e/admin",
        "/crm/%2F/x",
        "/crm/leads?page_size=500",
        "/crm/leads#x",
        "/crm\\leads",
        "/crm/le ads",
        "/@evil.example/x",
        "/crm/leads\n",
        "",
    ],
)
def test_a_bad_path_is_refused_before_any_request(path: str) -> None:
    door, fake = _door(_answers((200, {}, {})))
    with pytest.raises(ValueError):
        door.get(path)
    assert fake.sent == []


# --------------------------------------------------------------------------
# Done when 2: 250 rows in three pages of 100, 100 and 50.
# --------------------------------------------------------------------------


def _rows_handler(total: int, key: str = "rows") -> Handler:
    rows = [{"id": f"r{i}"} for i in range(total)]

    def handler(request: requests.PreparedRequest) -> tuple[int, Any, dict[str, str]]:
        q = parse_qs(urlsplit(request.url or "").query)
        if "page" in q:
            size = int(q["page_size"][0])
            start = (int(q["page"][0]) - 1) * size
        else:
            size = int(q["limit"][0])
            start = int(q["offset"][0])
        return 200, {key: rows[start : start + size], "total": total}, {}

    return handler


def test_pages_reads_250_rows_in_three_pages_of_at_most_100() -> None:
    door, fake = _door(_rows_handler(250))
    rows = list(door.pages("/crm/leads", {"q": "acme"}))
    assert [r["id"] for r in rows] == [f"r{i}" for i in range(250)]
    queries = fake.queries()
    assert len(queries) == 3
    assert [q["page"] for q in queries] == [["1"], ["2"], ["3"]]
    assert all(q["page_size"] == ["100"] for q in queries)
    assert all(q["q"] == ["acme"] for q in queries)
    assert max(int(q["page_size"][0]) for q in queries) <= D.PAGE_CAP == 100


def test_pages_stops_on_an_empty_page() -> None:
    door, fake = _door(_rows_handler(200))
    assert len(list(door.pages("/projects/tasks"))) == 200
    assert len(fake.sent) == 3  # the third page is empty


def test_pages_stops_after_max_pages() -> None:
    door, fake = _door(_rows_handler(10_000))
    assert len(list(door.pages("/projects/intake", page_size=10, max_pages=4))) == 40
    assert len(fake.sent) == 4


def test_an_offset_route_sends_limit_and_offset_from_zero() -> None:
    door, fake = _door(_rows_handler(130, key="entries"))
    path = "/crm/modules/invoices/records/0b6a5b38-1d5e-4b80-9d36-2a3c7c0d5c11/timeline"
    assert len(list(door.pages(path))) == 130
    assert [(q["limit"], q["offset"]) for q in fake.queries()] == [
        (["100"], ["0"]),
        (["100"], ["100"]),
    ]


def test_the_rows_key_of_each_route_is_read() -> None:
    door, _ = _door(_rows_handler(5, key="overdue"))
    assert len(list(door.pages("/crm/aggregate/stage-dwell"))) == 5
    door, _ = _door(_rows_handler(5, key="rows"))
    with pytest.raises(D.DoorError) as caught:
        list(door.pages("/crm/aggregate/stage-dwell"))
    assert caught.value.code == "bad_answer"


@pytest.mark.parametrize(
    ("path", "kwargs"),
    [
        ("/crm/leads", {"page_size": 101}),
        ("/crm/leads", {"page_size": 0}),
        ("/crm/leads", {"page_size": True}),
        ("/crm/leads", {"max_pages": 0}),
        ("/crm/leads", {"params": {"page_size": 500}}),
        ("/crm/leads", {"params": {"page": 2}}),
        ("/crm/leads", {"params": {"limit": 500}}),
        ("/crm/leads", {"params": {"per_lane": 500}}),
        ("/crm/pipeline", {}),
        ("/projects/nodes/p1/updates", {}),
        ("/crm/owners", {}),
    ],
)
def test_pages_refuses_a_page_over_the_cap_and_a_route_it_cannot_page(
    path: str,
    kwargs: dict[str, Any],
) -> None:
    door, fake = _door(_rows_handler(10))
    with pytest.raises(ValueError):
        list(door.pages(path, **kwargs))
    assert fake.sent == []


# --------------------------------------------------------------------------
# Done when 3: the status codes of decision 5.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"detail": "Unauthorized"}, "not_allowed"),
        (401, {"detail": "sandbox_scope"}, "not_allowed"),
        (403, {"detail": "sandbox_read_only"}, "sandbox_read_only"),
        (403, {"detail": "sandbox_scope"}, "sandbox_scope"),
        (403, {"detail": "sandbox_route_excluded"}, "sandbox_route_excluded"),
        (403, {"detail": "You cannot see this project"}, "forbidden"),
        (404, {"detail": "sandbox_route_unknown"}, "sandbox_route_unknown"),
        (404, {"detail": "Record not found"}, "not_found"),
        (422, {"detail": "sandbox_page_cap"}, "sandbox_page_cap"),
        (422, {"detail": "sandbox_response_too_large"}, "sandbox_response_too_large"),
        (422, {"detail": "sandbox_bad_query"}, "sandbox_bad_query"),
        (422, {"detail": [{"loc": ["query", "page"], "msg": "bad"}]}, "bad_input"),
        (422, {"detail": {"code": "sandbox_page_cap"}}, "sandbox_page_cap"),
        (429, {"detail": "sandbox_call_cap"}, "sandbox_call_cap"),
        (429, b"not json", "sandbox_call_cap"),
        (500, {"detail": "boom"}, "failed"),
    ],
)
def test_each_status_maps_to_its_code(
    status: int,
    body: Any,
    code: str,
    _token: list[float],
) -> None:
    door, fake = _door(_answers((status, body, {"Retry-After": "30"})))
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    err = caught.value
    assert (err.status, err.code) == (status, code)
    assert len(fake.sent) == 1, "only a 503 is retried"
    assert _token == []


def test_a_429_carries_retry_after_and_is_not_retried(_token: list[float]) -> None:
    door, fake = _door(_answers((429, {"detail": "sandbox_call_cap"}, {"Retry-After": "40"})))
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    assert caught.value.retry_after == 40.0
    assert len(fake.sent) == 1
    assert _token == []


def test_a_503_is_retried_once_then_raises(_token: list[float]) -> None:
    door, fake = _door(_answers((503, {"detail": "x"}, {"Retry-After": "2"})))
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    assert (caught.value.status, caught.value.code) == (503, "unavailable")
    assert len(fake.sent) == 2
    assert _token == [2.0]


def test_a_503_then_a_200_answers() -> None:
    door, fake = _door(_answers((503, {}, {"Retry-After": "1"}), (200, [1, 2], {})))
    assert door.get("/crm/leads") == [1, 2]
    assert len(fake.sent) == 2


@pytest.mark.parametrize(
    ("header", "wait"),
    [
        ({"Retry-After": "60"}, 5.0),
        ({"Retry-After": "5"}, 5.0),
        ({"Retry-After": "3"}, 3.0),
        ({}, 1.0),
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 1.0),
    ],
)
def test_the_wait_of_a_503_is_capped_at_5_seconds(
    header: dict[str, str],
    wait: float,
    _token: list[float],
) -> None:
    door, _ = _door(_answers((503, {}, header)))
    with pytest.raises(D.DoorError):
        door.get("/crm/leads")
    assert _token == [wait]


def test_a_redirect_is_not_followed_and_raises() -> None:
    door, fake = _door(
        _answers(
            (302, {}, {"Location": "http://metorite-api.sandbox/crm/deals"}),
            (200, {"rows": []}, {}),
        )
    )
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    assert (caught.value.status, caught.value.code) == (302, "redirect")
    assert len(fake.sent) == 1


def test_the_error_text_holds_no_token_and_no_body() -> None:
    secret = "You cannot see project Zeta of acme@example.com"
    door, _ = _door(_answers((403, {"detail": secret}, {})))
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    text = f"{caught.value} {caught.value!r} {caught.value.args}"
    assert TOKEN not in text and "Zeta" not in text
    assert str(caught.value) == "door 403 forbidden /crm/leads"


def test_a_transport_error_hides_its_text() -> None:
    def handler(_: requests.PreparedRequest) -> tuple[int, Any, dict[str, str]]:
        raise requests.ConnectionError(f"proxy http://user:{TOKEN}@proxy failed")

    door, _ = _door(handler)
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    err = caught.value
    assert (err.status, err.code) == (0, "unreachable")
    assert err.__cause__ is None and err.__suppress_context__
    assert TOKEN not in str(err)


def test_a_bad_json_answer_is_a_door_error() -> None:
    door, _ = _door(_answers((200, b"<html>", {})))
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    assert caught.value.code == "bad_answer"


# --------------------------------------------------------------------------
# Done when 4: no capability, no request.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "Bearer abc", "msbx_short", TOKEN + "\nX: y"])
def test_with_no_capability_the_first_call_raises_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("METORITE_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("METORITE_API_TOKEN", value)
    door, fake = _door(_rows_handler(10))
    with pytest.raises(D.DoorError) as caught:
        door.get("/crm/leads")
    assert caught.value.code == "no_capability"
    with pytest.raises(D.DoorError):
        next(door.pages("/crm/leads"))
    assert fake.sent == []


# --------------------------------------------------------------------------
# The paging table: each route is a GET row of the manifests, opened to the
# door, and its keys are query parameters of the live handler.
# --------------------------------------------------------------------------

#: The rows that §12.2 decision 2 opens (2026-11-26). The test reads them only
#: while ``app_manifests.SANDBOX_ROUTES`` does not exist. SBX-2 adds that set.
_SPEC_OPENED_CRM = (
    "/crm/leads",
    "/crm/leads/{record_id}",
    "/crm/deals",
    "/crm/deals/{record_id}",
    "/crm/contacts",
    "/crm/contacts/{record_id}",
    "/crm/organizations",
    "/crm/organizations/{record_id}",
    "/crm/modules",
    "/crm/modules/{module}/records",
    "/crm/modules/{module}/records/{record_id}",
    "/crm/modules/{module}/records/{record_id}/timeline",
    "/crm/reports/pipeline",
    "/crm/reports/funnel",
    "/crm/reports/win-loss",
    "/crm/reports/owners",
    "/crm/pipeline",
    "/crm/pipelines",
    "/crm/aggregate/stage-dwell",
    "/crm/fields/{entity}",
    "/crm/fields-search",
    "/crm/catalogue",
    "/crm/statuses/{kind}",
    "/crm/lost-reasons",
    "/crm/mode",
    "/crm/owners",
    "/crm/activities/{activity_id}",
    "/crm/change-history",
    "/crm/deals/{deal_id}/contacts",
    "/crm/views",
)
_SPEC_OPENED_PROJECTS = tuple(
    "/projects" + p
    for p in (
        "/tree",
        "/nodes/{project_id}",
        "/nodes/{project_id}/updates",
        "/tasks",
        "/tasks/stats",
        "/tasks/{task_id}",
        "/tasks/{task_id}/timeline",
        "/tasks/{task_id}/attachments",
        "/tasks/{task_id}/relations",
        "/assignable-members",
        "/nodes/{project_id}/statuses",
        "/nodes/{project_id}/types",
        "/nodes/{project_id}/fields",
        "/nodes/{project_id}/tags",
        "/plan/order",
        "/plan/analysis",
        "/search",
        "/summary",
        "/nodes/{project_id}/summary",
        "/nodes/{project_id}/views",
        "/calendar",
        "/intake",
        "/my/project",
        "/my/contexts",
        "/my/areas",
        "/my/reminders",
        "/my/today",
        "/notifications",
        "/analytics/stuck",
        "/analytics/throughput",
        "/analytics/finished",
        "/analytics/load",
        "/reports",
        "/reports/templates",
        "/reports/subjects",
        "/reports/{report_id}",
        "/reports/{report_id}/render",
        "/tasks/{task_id}/recurrence",
    )
)
_SPEC_OPENED = frozenset(("GET", p) for p in _SPEC_OPENED_CRM + _SPEC_OPENED_PROJECTS)


def test_the_spec_list_has_the_68_rows_of_decision_2() -> None:
    assert (len(_SPEC_OPENED_CRM), len(_SPEC_OPENED_PROJECTS), len(_SPEC_OPENED)) == (30, 38, 68)


def _opened() -> frozenset[tuple[str, str]]:
    """``SANDBOX_ROUTES`` when it exists on main, else the spec's list."""
    from acb_common import app_manifests

    routes = getattr(app_manifests, "SANDBOX_ROUTES", None)
    if routes is None:
        return _SPEC_OPENED
    return frozenset((str(m).upper(), str(p)) for m, p in routes)


def paging_problems(table: dict[str, Any], opened: frozenset[tuple[str, str]]) -> list[str]:
    return [f"GET {path} is not open on the door" for path in table if ("GET", path) not in opened]


def test_each_paged_route_is_open_on_the_door() -> None:
    assert paging_problems(D.PAGED_ROUTES, _opened()) == []


def test_the_paging_check_sees_a_route_off_the_door() -> None:
    opened = _opened() - {("GET", "/crm/change-history")}
    assert paging_problems(D.PAGED_ROUTES, opened) == [
        "GET /crm/change-history is not open on the door"
    ]


def test_the_sandbox_routes_arm_is_read_when_it_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """When SBX-2 adds ``SANDBOX_ROUTES``, the check reads it and not the spec list."""
    from acb_common import app_manifests

    full = {("GET", path) for path in D.PAGED_ROUTES}
    monkeypatch.setattr(app_manifests, "SANDBOX_ROUTES", frozenset(full), raising=False)
    assert _opened() == frozenset(full)
    assert paging_problems(D.PAGED_ROUTES, _opened()) == []
    short = frozenset(full - {("GET", "/projects/intake")})
    monkeypatch.setattr(app_manifests, "SANDBOX_ROUTES", short, raising=False)
    assert paging_problems(D.PAGED_ROUTES, _opened()) == [
        "GET /projects/intake is not open on the door"
    ]


def test_each_row_of_the_spec_list_is_a_get_row_of_the_manifests() -> None:
    from acb_common.app_manifests.crm import CRM
    from acb_common.app_manifests.projects import PROJECTS_DOOR

    rows = {(r.method, r.path) for r in (*CRM.routes, *PROJECTS_DOOR.routes)}
    assert sorted(_SPEC_OPENED - rows) == []


def test_each_paged_route_is_a_class_a_get_row_of_the_manifests() -> None:
    from acb_common.app_manifests.crm import CRM
    from acb_common.app_manifests.projects import PROJECTS_DOOR

    rows = {(r.method, r.path): r for r in (*CRM.routes, *PROJECTS_DOOR.routes)}
    for path in D.PAGED_ROUTES:
        row = rows.get(("GET", path))
        assert row is not None, f"GET {path} is not a manifest row"
        assert row.cls == "A", f"GET {path} is class {row.cls}"


def _live_route(path: str) -> Any:
    from gateway.routes.crm import router as crm_router
    from gateway.routes.projects import router as projects_router

    for router in (crm_router, projects_router):
        for route in router.routes:
            if getattr(route, "path", None) == path and "GET" in (
                getattr(route, "methods", None) or ()
            ):
                return route
    raise AssertionError(f"GET {path} is not a route of the gateway")


def _query_names(dependant: Any) -> set[str]:
    """The query keys of a handler and of each of its dependencies."""
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _query_names(sub)
    return names


def test_each_paged_route_takes_its_keys_and_answers_its_rows_key() -> None:
    for path, paging in D.PAGED_ROUTES.items():
        route = _live_route(path)
        names = _query_names(route.dependant)
        wanted = {"page", "page_size"} if paging.style == "page" else {"limit", "offset"}
        assert wanted <= names, f"GET {path} takes {sorted(names)}, not {sorted(wanted)}"
        model = getattr(route, "response_model", None)
        if model is not None and hasattr(model, "model_fields"):
            assert paging.rows_key in model.model_fields, f"GET {path}: no {paging.rows_key}"
        else:
            # A dict route: the handler must build its answer with the key.
            source = inspect.getsource(route.endpoint)
            assert f'"{paging.rows_key}":' in source, f"GET {path}: no {paging.rows_key!r} key"


# --------------------------------------------------------------------------
# Done when 6, first half: the one COPY line.
# --------------------------------------------------------------------------


def test_the_dockerfile_copies_the_client_read_only_into_site_packages() -> None:
    steps = _instructions(_DOCKERFILE.read_text(encoding="utf-8"))
    copies = [args for word, args in steps if word == "COPY" and "metorite_door" in args]
    assert copies == [f"--chmod=0444 sandbox/metorite_door.py {_SITE_PATH}"]
    words = [word for word, _ in steps]
    at = next(i for i, (w, a) in enumerate(steps) if w == "COPY" and "metorite_door" in a)
    pip = max(i for i, (w, a) in enumerate(steps) if w == "RUN" and "pip install" in a)
    user = max(i for i, w in enumerate(words) if w == "USER")
    assert pip < at < user, "the COPY goes after pip and before USER, so root owns it"


# --------------------------------------------------------------------------
# The Docker half (done when 5 and 6). Deselected by default (pyproject.toml);
# .github/workflows/sandbox-docker.yml runs it and fails on any skip.
# --------------------------------------------------------------------------

_STUB = r"""
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
TOKEN = os.environ["STUB_TOKEN"]
ROWS = [{"id": f"lead-{i}"} for i in range(150)]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if self.headers.get("X-Sandbox-Capability") != TOKEN or self.headers.get("Authorization"):
            return self.send(401, {"detail": "Unauthorized"})
        if url.path != "/crm/leads":
            return self.send(404, {"detail": "sandbox_route_unknown"})
        q = parse_qs(url.query)
        size = int(q["page_size"][0]); page = int(q["page"][0])
        if size > 100:
            return self.send(422, {"detail": "sandbox_page_cap"})
        rows = ROWS[(page - 1) * size: page * size]
        return self.send(200, {"rows": rows, "total": len(ROWS)})
    def send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass
HTTPServer(("0.0.0.0", 80), H).serve_forever()
"""

_TOOL = r"""
import json, os, time
import metorite_door as md
door = md.Door(timeout_s=5)
deadline = time.monotonic() + 60
while True:
    try:
        door.get("/crm/ready")
    except md.DoorError as e:
        if e.code != "unreachable":
            break
        if time.monotonic() > deadline:
            raise SystemExit("the stub door did not answer")
        time.sleep(0.5)
rows = list(door.pages("/crm/leads"))
out = {"count": len(rows), "first": rows[0]["id"], "last": rows[-1]["id"],
       "file": md.__file__}
os.environ["METORITE_API_TOKEN"] = "msbx_" + "z" * 43
try:
    door.get("/crm/leads")
    out["wrong"] = "answered"
except md.DoorError as e:
    out["wrong"] = [e.status, e.code]
print("RESULT " + json.dumps(out))
"""


def _tool_flags() -> list[str]:
    flags = list(_RUN_FLAGS)
    at = flags.index("--network")
    del flags[at : at + 2]
    return flags


@pytest.mark.sandbox_docker
def test_the_client_imports_under_isolated_mode_as_uid_1000(
    coding_sandbox_image: str,  # noqa: F811 — the fixture, imported above
) -> None:
    """Done when 6: ``python3 -I -c "import metorite_door"`` as uid 1000, 0444, root."""
    script = (
        'python3 -I -c "import metorite_door" && echo "import: 0"\n'
        "stat -c 'file: %n %a %u %g' " + _SITE_PATH + "\n"
    )
    result = _docker(
        "run", *_RUN_FLAGS, "--user", "1000:1000", coding_sandbox_image,
        "bash", "-o", "pipefail", "-c", script, timeout=300,
    )  # fmt: skip
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    lines = result.stdout.splitlines()
    assert "import: 0" in lines, result.stdout
    assert f"file: {_SITE_PATH} 444 0 0" in lines, result.stdout


@pytest.mark.sandbox_docker
def test_a_tool_reads_two_pages_from_a_stub_door(
    coding_sandbox_image: str,  # noqa: F811 — the fixture, imported above
) -> None:
    """Done when 5: a stub door on a test-only network serves two pages."""
    tag = uuid.uuid4().hex[:12]
    network, stub = f"sbx13-net-{tag}", f"sbx13-stub-{tag}"
    try:
        made = _docker("network", "create", "--internal", network, timeout=120)
        assert made.returncode == 0, made.stderr
        started = _docker(
            "run", "-d", "--name", stub, "--network", network,
            "--network-alias", "metorite-api.sandbox",
            "--read-only", "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
            "--security-opt", "no-new-privileges", "--user", "0:0",
            "-e", f"STUB_TOKEN={TOKEN}", coding_sandbox_image,
            "python3", "-I", "-c", _STUB, timeout=300,
        )  # fmt: skip
        assert started.returncode == 0, started.stderr
        result = _docker(
            "run", *_tool_flags(), "--network", network, "--user", "1000:1000",
            "-e", f"METORITE_API_TOKEN={TOKEN}", coding_sandbox_image,
            "python3", "-I", "-c", _TOOL, timeout=300,
        )  # fmt: skip
        logs = _docker("logs", stub, timeout=60)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\nstub:\n{logs.stderr}"
        )
        line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT "))
        out = json.loads(line[7:])
        assert out["count"] == 150
        assert (out["first"], out["last"]) == ("lead-0", "lead-149")
        assert out["file"] == _SITE_PATH
        assert out["wrong"] == [401, "not_allowed"]
    finally:
        _docker("rm", "-f", stub, timeout=120)
        _docker("network", "rm", network, timeout=120)
