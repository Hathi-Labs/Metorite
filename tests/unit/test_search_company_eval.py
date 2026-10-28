"""WS-KG KG-1: the retrieval eval of ``GET /search/company``, without a database.

The score maths, the nearest-rank percentiles on fixed timings, the empty-source
report, the verdict, the synthetic set, and the ``--live`` mode against a fake
HTTP server on a loopback port. The route on a real database is
``test_search_company_eval_r8.py``.

⚠️ Each bound below is the one a mutation breaks: ``k`` off by one, a percentile
by floor, a rank that counts another source's hits. The mutations were run by
hand and are in the report of KG-1.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from evals.search_company import questions as qs
from evals.search_company import run, score
from evals.search_company.score import Outcome

REGISTRY = ("tasks", "email", "people", "crm", "artifacts", "whatsapp", "knowledge")


def _hits(*pairs: tuple[str, str]) -> list[dict]:
    return [{"app": app, "id": ident} for app, ident in pairs]


def _o(qid: str, source: str, rank: int | None, place: int | None = None,
       seconds: float = 0.1, **kw) -> Outcome:
    return Outcome(qid, source, rank, place if place is not None else rank, seconds, **kw)


# ── locate: the rank is among the hits of the gold's own source ─────────────


def test_rank_counts_only_the_hits_of_the_source() -> None:
    hits = _hits(("tasks", "t1"), ("email", "e1"), ("tasks", "t2"), ("email", "e2"))
    assert score.locate(hits, "email", ["e2"]) == (2, 4)
    assert score.locate(hits, "tasks", ["t2"]) == (2, 3)
    assert score.locate(hits, "tasks", ["e1"]) == (None, None)  # the id of another source
    assert score.locate(hits, "people", ["e1"]) == (None, None)
    assert score.locate([], "email", ["e1"]) == (None, None)


def test_any_gold_id_counts() -> None:
    hits = _hits(("crm", "a"), ("crm", "b"))
    assert score.locate(hits, "crm", ["x", "b"]) == (2, 2)


# ── recall@5: the boundary is 5 in, 6 out ───────────────────────────────────


def test_recall_boundary_is_five_in_and_six_out() -> None:
    assert score.K == 5
    assert score.recall_at_k([_o("a", "email", 5)]) == 1.0
    assert score.recall_at_k([_o("a", "email", 6)]) == 0.0
    assert score.recall_at_k([_o("a", "email", 1)]) == 1.0
    assert score.recall_at_k([_o("a", "email", None)]) == 0.0
    assert score.merged_at_k([_o("a", "email", 1, place=5)]) == 1.0
    assert score.merged_at_k([_o("a", "email", 1, place=6)]) == 0.0


def test_recall_is_the_mean_over_questions() -> None:
    outs = [_o("a", "email", 1), _o("b", "email", 5), _o("c", "tasks", 6), _o("d", "tasks", None)]
    assert score.recall_at_k(outs) == pytest.approx(0.5)
    assert score.recall_at_k([]) == 0.0


# ── p50 and p95: nearest rank, on fixed timings ─────────────────────────────


def test_percentiles_are_nearest_rank() -> None:
    twenty = [float(n) for n in range(1, 21)]            # 1..20
    assert score.percentile(twenty, 50) == 10.0           # ceil(0.50 * 20) = 10
    assert score.percentile(twenty, 95) == 19.0           # ceil(0.95 * 20) = 19
    assert score.percentile(list(reversed(twenty)), 95) == 19.0  # order does not matter
    fifty = [n / 100 for n in range(1, 51)]
    assert score.percentile(fifty, 50) == 0.25            # rank 25, not 26
    assert score.percentile(fifty, 95) == 0.48            # ceil(47.5) = 48
    assert score.percentile([7.0], 95) == 7.0
    assert score.percentile([1.0, 2.0], 50) == 1.0        # ceil(1.0) = 1
    assert score.percentile([], 50) == 0.0
    # A value is always a time that a request took: no interpolation.
    assert score.percentile([1.0, 3.0], 95) == 3.0


def test_summary_uses_the_outcome_timings() -> None:
    outs = [_o(f"q{n}", "email", 1, seconds=float(n)) for n in range(1, 21)]
    s = score.summarize(outs, REGISTRY)
    assert (s.p50, s.p95) == (10.0, 19.0)


# ── empty sources, failing sources, skipped ─────────────────────────────────


def test_empty_sources_and_skipped_are_reported() -> None:
    outs = [
        _o("q1", "email", 1, hits={"email": 2, "tasks": 1}),
        _o("q2", "tasks", 1, hits={"tasks": 3}, skipped=("crm",)),
        _o("q3", "email", None, hits={"email": 1}, skipped=("knowledge", "crm")),
    ]
    s = score.summarize(outs, REGISTRY)
    assert s.empty == ("people", "crm", "artifacts", "whatsapp", "knowledge")
    assert s.skipped == ("crm", "knowledge")
    assert s.failing == ()
    assert s.missed == ("q3",)


def test_a_source_with_questions_and_no_own_hit_fails() -> None:
    # Email has two questions and gets no email hit. Another source's hits do not save it.
    outs = [_o("q1", "email", None, hits={"tasks": 4}), _o("q2", "email", None, hits={"tasks": 1}),
            _o("q3", "tasks", 1, hits={"tasks": 1})]
    s = score.summarize(outs, REGISTRY)
    assert s.failing == ("email",)
    assert score.verdict(s) == score.EXIT_FAIL


def test_a_source_without_questions_is_not_a_failure() -> None:
    outs = [_o(f"q{n}", "tasks", 1, hits={"tasks": 1}) for n in range(10)]
    s = score.summarize(outs, REGISTRY)
    assert "whatsapp" in s.empty and s.failing == ()
    assert score.verdict(s) == score.EXIT_PASS


def test_verdict_floor_and_errors() -> None:
    ok = [_o(f"q{n}", "tasks", 1, hits={"tasks": 1}) for n in range(9)]
    nine_of_ten = [*ok, _o("q9", "tasks", None, hits={"tasks": 1})]
    assert score.verdict(score.summarize(nine_of_ten, REGISTRY)) == score.EXIT_PASS   # 0.90
    eight_of_ten = [*ok[:8], *[_o("a", "tasks", None, hits={"tasks": 1})] * 2]
    assert score.verdict(score.summarize(eight_of_ten, REGISTRY)) == score.EXIT_FAIL  # 0.80
    failed = [*ok, _o("q9", "tasks", None, error="ReadTimeout")]
    assert score.verdict(score.summarize(failed, REGISTRY)) == score.EXIT_FAIL
    assert score.verdict(score.summarize([], REGISTRY)) == score.EXIT_FAIL


def test_the_report_holds_no_question_text() -> None:
    words = "zxqv secret words"
    outs = [_o("q001", "email", None, hits={"email": 1})]
    text = score.render(score.summarize(outs, REGISTRY), mode="live",
                        flags={"CONTEXT_RECENCY": "on"})
    assert words not in text and "q001" in text
    assert "CONTEXT_RECENCY=on" in text and "recall@5: 0.000" in text


# ── the synthetic set ───────────────────────────────────────────────────────


def test_the_synthetic_set_is_whole() -> None:
    fx = qs.load_fixture()
    assert len(fx.questions) == 50
    for source in qs.SOURCES:
        assert sum(1 for q in fx.questions if q["source"] == source) == 10
    assert qs.check(fx) == []


def test_check_names_each_fault() -> None:
    fx = qs.load_fixture()
    bad = qs.Fixture(fx.records, tuple(
        {**q, "gold": ["no-such-record"]} if q["id"] == "q001" else q for q in fx.questions))
    assert any("unknown record" in p for p in qs.check(bad))
    short = qs.Fixture(fx.records, fx.questions[:-1])
    assert any("not 10" in p for p in qs.check(short))
    with pytest.raises(ValueError):
        qs.load_real(__import__("pathlib").Path(__file__))  # not JSON


def test_gold_ids_follow_the_org_and_the_key() -> None:
    assert qs.gold_id("org-a", "email-01") == qs.gold_id("org-a", "email-01")
    assert qs.gold_id("org-a", "email-01") != qs.gold_id("org-b", "email-01")
    assert qs.gold_id("org-a", "email-01") != qs.gold_id("org-a", "email-02")
    fx = qs.load_fixture()
    for raw, q in zip(fx.questions, qs.resolve(fx, "org-a"), strict=True):
        assert q.gold == tuple(qs.gold_id("org-a", k) for k in raw["gold"])


def test_the_seed_gives_each_record_the_gold_id() -> None:
    from evals.search_company import seed

    fx = qs.load_fixture()
    org = "00000000-0000-0000-0000-00000000aaaa"
    rows = [r for _sql, batch in seed.statements(org, fx) for r in batch]
    seeded = {r.get("i") or r.get("t") for r in rows}
    for q in qs.resolve(fx, org):
        assert set(q.gold) <= seeded, q.id


# ── --live, against a fake gateway on a loopback port ───────────────────────


class _Gateway:
    """Answers ``GET /search/company`` from a table of words → hits, and keeps what it saw."""

    def __init__(self, answers: dict[str, dict]) -> None:
        self.seen: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                from urllib.parse import parse_qs, urlsplit

                parts = urlsplit(self.path)
                words = parse_qs(parts.query).get("q", [""])[0]
                outer.seen.append({"path": parts.path, "q": words, "k": parse_qs(parts.query).get("k"),
                                   "auth": self.headers.get("Authorization"),
                                   "who": self.headers.get("X-User-Email")})
                if words == "boom":
                    self.send_response(500)
                    self.end_headers()
                    return
                body = json.dumps(answers.get(words, {"query": words, "hits": [], "skipped": []}))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *_a) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gateway():
    made: list[_Gateway] = []

    def make(answers: dict[str, dict]) -> _Gateway:
        made.append(_Gateway(answers))
        return made[-1]

    yield make
    for g in made:
        g.close()


def _set_file(tmp_path, items: list[dict], mode: int = 0o600):
    path = tmp_path / "real.json"
    path.write_text(json.dumps({"questions": items}), encoding="utf-8")
    os.chmod(path, mode)
    return str(path)


SECRET_WORDS = "quasar lantern ledger"
TOKEN = "tok-DO-NOT-PRINT-1234"


def test_live_scores_and_prints_no_question_text(gateway, tmp_path, monkeypatch, capsys) -> None:
    gw = gateway({
        SECRET_WORDS: {"hits": _hits(("tasks", "t9"), ("email", "g1")), "skipped": ["crm"]},
        "second words": {"hits": _hits(("email", "zz")), "skipped": []},
    })
    path = _set_file(tmp_path, [
        {"id": "r001", "source": "email", "text": SECRET_WORDS, "gold": ["g1"]},
        {"id": "r002", "source": "email", "text": "second words", "gold": ["g2"]},
    ])
    monkeypatch.setenv(run.TOKEN_ENV, TOKEN)
    code = run.live(gw.url, "someone@example.test", path, {"CONTEXT_RECENCY": "off"})
    out = capsys.readouterr().out
    assert code == score.EXIT_PASS
    assert "recall@5: 0.500 (1/2)" in out and "missed question ids: r002" in out
    assert "skipped: crm" in out and "CONTEXT_RECENCY=off" in out
    # Not a word of a question, and not the bearer.
    assert "quasar" not in out and "lantern" not in out and "second words" not in out
    assert TOKEN not in out
    # The wire: the route, k=5, the bearer and the member in headers, no org anywhere.
    assert {s["path"] for s in gw.seen} == {"/search/company"}
    assert all(s["k"] == ["5"] and s["auth"] == f"Bearer {TOKEN}"
               and s["who"] == "someone@example.test" for s in gw.seen)


def test_live_counts_a_failed_request_and_exits_one(gateway, tmp_path, monkeypatch, capsys) -> None:
    gw = gateway({})
    path = _set_file(tmp_path, [{"id": "r001", "source": "crm", "text": "boom", "gold": ["x"]}])
    monkeypatch.setenv(run.TOKEN_ENV, TOKEN)
    assert run.live(gw.url, "someone@example.test", path, {}) == score.EXIT_FAIL
    out = capsys.readouterr().out
    assert "failed requests: 1" in out and "boom" not in out


def test_live_without_a_real_set_exits_two(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(run.TOKEN_ENV, TOKEN)
    assert run.live("http://127.0.0.1:1", "a@b.test", str(tmp_path / "absent.json"), {}) == 2
    assert "no real set" in capsys.readouterr().out
    assert run.live("http://127.0.0.1:1", "a@b.test", "", {}) == 2


def test_live_refuses_a_set_that_others_can_read(tmp_path, monkeypatch, capsys) -> None:
    path = _set_file(tmp_path, [{"id": "r1", "source": "crm", "text": "x y", "gold": ["g"]}], 0o644)
    monkeypatch.setenv(run.TOKEN_ENV, TOKEN)
    assert run.live("http://127.0.0.1:1", "a@b.test", path, {}) == 2
    assert "chmod 600" in capsys.readouterr().out


def test_live_refuses_a_url_that_is_not_this_machine(tmp_path, monkeypatch, capsys) -> None:
    path = _set_file(tmp_path, [{"id": "r1", "source": "crm", "text": "x y", "gold": ["g"]}])
    monkeypatch.setenv(run.TOKEN_ENV, TOKEN)
    assert run.live("https://app.metorite.com", "a@b.test", path, {}) == 2
    assert "loopback" in capsys.readouterr().out


def test_live_needs_the_token_and_a_member(tmp_path, monkeypatch, capsys) -> None:
    path = _set_file(tmp_path, [{"id": "r1", "source": "crm", "text": "x y", "gold": ["g"]}])
    monkeypatch.delenv(run.TOKEN_ENV, raising=False)
    assert run.live("http://127.0.0.1:1", "a@b.test", path, {}) == 2
    monkeypatch.setenv(run.TOKEN_ENV, TOKEN)
    assert run.live("http://127.0.0.1:1", "not-an-address", path, {}) == 2
    assert TOKEN not in capsys.readouterr().out


def test_check_mode_and_the_parser(capsys) -> None:
    assert run.main(["--check"]) == 0
    assert "50 questions" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run.main([])  # a mode is required
