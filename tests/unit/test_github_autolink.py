"""WS-54 GH-4 — the auto-link grammar of a pull request (G-3, §10).

Spec: ``project-docs/specs/github_integration.md`` §10.1 and §10.2. The cases
live in ``tests/fixtures/github_autolink_cases.json`` (``grammar``), and this
module drives the REAL ``github_autolink.candidates`` with each one. The rules
that need the database (``rules`` in the same file) run in
``test_github_webhook_r8.py``.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* A1 compare the URL host with ``endswith`` -> the "another host" case.
* A2 drop the ``(?=$|[/_-])`` end of the short form -> the "longer word" case.
* A3 add ``re.IGNORECASE`` to the short form -> the "upper-case hex" case.
* A4 no dedupe in ``candidates`` -> the "named twice" case.
* A5 ``trusted_source`` returns True -> ``test_a_fork_from_an_outsider_is_not_trusted``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from gateway.routes.projects import github_autolink

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "github_autolink_cases.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES["grammar"], ids=[c["name"] for c in CASES["grammar"]])
def test_the_grammar(case: dict) -> None:
    got = github_autolink.candidates(case["body"], case["head_ref"], CASES["app_host"])
    assert [[c.kind, c.value] for c in got] == case["expect"]


def test_the_short_form_example_is_the_first_eight_hex() -> None:
    form = CASES["short_form"]
    got = github_autolink.candidates(None, form["example_branch"], CASES["app_host"])
    assert [[c.kind, c.value] for c in got] == [["short", form["example_task"][: form["hex_chars"]]]]
    assert form["prefix"] == "mt-"


def test_the_app_host_comes_from_the_public_url() -> None:
    assert github_autolink.app_host("https://app.metorite.com") == "app.metorite.com"
    assert github_autolink.app_host("https://dev.metorite.com/") == "dev.metorite.com"
    assert github_autolink.app_host("") == ""


def test_with_no_app_host_a_url_never_links() -> None:
    body = "https://app.metorite.com/projects?task=1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"
    assert github_autolink.candidates(body, "", "") == []


def _pr(head: str, base: str, association: str) -> dict:
    return {
        "head": {"repo": {"full_name": head}}, "base": {"repo": {"full_name": base}},
        "author_association": association,
    }


def test_a_pull_request_from_its_own_repository_is_trusted() -> None:
    assert github_autolink.trusted_source(_pr("acme/w", "Acme/W", "NONE"))


def test_a_fork_from_an_outsider_is_not_trusted() -> None:
    for association in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", ""):
        assert not github_autolink.trusted_source(_pr("mallory/w", "acme/w", association))
    for association in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert github_autolink.trusted_source(_pr("dana/w", "acme/w", association))


def test_the_rules_file_names_every_acceptance_case() -> None:
    names = {r["name"].split(" ")[0] for r in CASES["rules"]}
    assert {"L1", "L2", "L3", "L4a", "L4b", "L4d", "L5a", "L6"} <= names
