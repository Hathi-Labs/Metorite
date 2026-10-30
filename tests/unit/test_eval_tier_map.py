"""The eval-only tier map of the live measure (``--tier-map``).

The map sends the requests of one tier to another tier, in the eval process
only. The wire tap makes the rewrite. Mutations are named in the docstrings.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from evals.one_assistant_measure import run, spend
from evals.one_assistant_measure.scenarios import scenario
from evals.routing import harness as hm
from evals.routing.harness import ModelCall, _WireTap

MAP = {"tier-balanced": "tier-fast"}
OFF_PEAK = datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp()
PEAK = datetime(2026, 11, 2, 7, tzinfo=UTC).timestamp()


def _send(model: str, monkeypatch, tier_map: dict[str, str]) -> tuple[dict, ModelCall, httpx.Request]:
    """Send one request through a wire tap. Return the body that the Router got."""
    monkeypatch.setattr(hm, "MODEL_REWRITE", dict(tier_map))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"model": "deepseek/deepseek-flash", "usage": {}})

    sink: list[ModelCall] = []
    tap = _WireTap(httpx.MockTransport(handler), "orchestrator", sink)
    body = {"model": model, "stream": False, "reasoning_effort": "low", "temperature": 0.2,
            "messages": [{"role": "user", "content": "café"}], "tools": [{"type": "function"}]}
    request = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions", json=body)
    asyncio.run(tap.handle_async_request(request))
    return json.loads(seen[0].content), sink[0], seen[0]


def test_the_rewrite_happens_only_for_the_named_tier(monkeypatch) -> None:
    """Mutation: rewrite every model in ``_WireTap`` -> the powerful case goes red."""
    got, call, request = _send("tier-balanced", monkeypatch, MAP)
    assert got["model"] == "tier-fast"
    assert (call.model, call.requested) == ("tier-fast", "tier-balanced")
    assert request.headers["content-length"] == str(len(request.content))
    got, call, _ = _send("tier-powerful", monkeypatch, MAP)
    assert got["model"] == "tier-powerful" and call.requested == ""


def test_only_the_model_changes(monkeypatch) -> None:
    """Mutation: drop a key in ``_rewritten`` -> the other options differ -> red."""
    got, _, _ = _send("tier-balanced", monkeypatch, MAP)
    assert {k: v for k, v in got.items() if k != "model"} == {
        "stream": False, "reasoning_effort": "low", "temperature": 0.2,
        "messages": [{"role": "user", "content": "café"}], "tools": [{"type": "function"}]}


def test_system_one_is_unchanged_unless_it_is_named(monkeypatch) -> None:
    """Mutation: add ``tier-fast`` to every map -> the first case goes red."""
    got, call, _ = _send("tier-fast", monkeypatch, MAP)
    assert got["model"] == "tier-fast" and call.requested == ""
    got, call, _ = _send("tier-fast", monkeypatch, {"tier-fast": "tier-balanced"})
    assert got["model"] == "tier-balanced" and call.requested == "tier-fast"


def test_no_map_changes_nothing(monkeypatch) -> None:
    got, call, _ = _send("tier-balanced", monkeypatch, {})
    assert got["model"] == "tier-balanced" and call.requested == ""


def test_the_map_takes_tiers_only() -> None:
    """Mutation: let ``parse_tier_map`` pass any target -> the model id case goes red."""
    assert run.parse_tier_map("tier-balanced=tier-fast, tier-powerful=tier-fast") == {
        "tier-balanced": "tier-fast", "tier-powerful": "tier-fast"}
    assert run.parse_tier_map("") == {}
    for bad in ("tier-balanced=deepseek/deepseek-flash", "gpt-4o=tier-fast", "tier-balanced",
                "tier-balanced=tier-balanced"):
        with pytest.raises(ValueError):
            run.parse_tier_map(bad)


def test_a_bad_map_stops_main_before_any_run(capsys) -> None:
    assert run.main(["--scripted", "--tier-map", "tier-balanced=deepseek/deepseek-flash"]) \
        == run.EXIT_NO_GO
    assert "NO-GO" in capsys.readouterr().out


def test_the_arm_name_shows_the_map() -> None:
    assert run.arm_label("pack", None) == "pack"
    assert run.arm_label("pack", MAP) == "pack[balanced=flash]"
    both = {"tier-balanced": "tier-fast", "tier-powerful": "tier-fast"}
    assert run.arm_label("pack", both) == "pack[balanced=flash,powerful=flash]"


def _call(**kw) -> ModelCall:
    base = dict(agent="orchestrator", model="tier-fast", stream=True, messages=3, tools=5,
                request_chars=30_000, started=1.0, ended=2.0, status=200, wall=OFF_PEAK,
                requested="tier-balanced")
    return ModelCall(**{**base, **kw})


def test_a_mapped_call_records_both_names_and_is_priced_by_the_served_model() -> None:
    """Mutation: drop ``card_for`` from ``call_row`` -> the Flash price reads as Pro -> red."""
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    flash = run.call_row(_call(usage=usage, served="deepseek/deepseek-flash"))
    pro = run.call_row(_call(usage=usage, served="deepseek/deepseek-v4-pro"))
    assert (flash["requested"], flash["model"], flash["mapped"]) == (
        "tier-balanced", "tier-fast", True)
    assert flash["cost_usd"] == pytest.approx(0.15) and pro["cost_usd"] == pytest.approx(0.66)
    # No served name: the card of the target tier.
    assert run.call_row(_call(usage=usage))["cost_usd"] == pytest.approx(0.15)
    # The peak factor stays.
    assert run.call_row(_call(usage=usage, wall=PEAK))["cost_usd"] == pytest.approx(0.30)
    # An unmapped call keeps the Pro card.
    plain = run.call_row(_call(usage=usage, requested="", served="deepseek/deepseek-flash"))
    assert plain["cost_usd"] == pytest.approx(0.66) and not plain["mapped"]
    # No usage: the worst case on the Flash card.
    assert run.call_row(_call())["cost_usd"] < run.call_row(_call(requested=""))["cost_usd"]
    assert spend.PEAK_FACTOR == 2.0


def _row(arm: str, tier_map: dict[str, str], served: str = "") -> dict:
    return {"arm": arm, "arm_label": run.arm_label(arm, tier_map), "tier_map": tier_map,
            "scenario": "s", "turn": 1, "repeat": 1, "checks": {}, "passed": True,
            "first_text_s": 0, "total_s": 0, "error": None, "answer": "", "model_calls": [
                {"agent": "o", "cost_usd": 0.1, "served": served, "prompt": 0, "cached": 0,
                 "completion": 0, "dur_s": 0}]}


def _chunk(tmp_path, name: str, tier_map: dict[str, str], arm_names: tuple[str, ...] = ("pack",),
           label_map: dict[str, str] | None = None) -> object:
    rows = [_row(a, tier_map if label_map is None else label_map, "deepseek/deepseek-flash")
            for a in arm_names]
    data = {"sha": "abc", "tier_map": tier_map, "rows": rows, "spent_usd": 0.1,
            "spent_before_usd": 0.0}
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_header_holds_the_map_and_the_served_models(tmp_path) -> None:
    """Mutation: drop ``served_models`` from ``_write`` -> the header lacks it -> red."""
    out = tmp_path / "o.json"
    data = {"tier_map": MAP, "rows": [_row("pack", MAP, "deepseek/deepseek-flash")],
            "spent_usd": 0.1}
    run._write(out, data)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["tier_map"] == MAP and saved["served_models"] == ["deepseek/deepseek-flash"]
    assert list(saved["summary"]) == ["pack[balanced=flash]"]


def test_run_live_writes_the_map_in_the_header(monkeypatch, tmp_path) -> None:
    """Mutation: drop ``tier_map`` from the ``data`` of ``run_live`` -> red."""
    seen: dict = {}

    def fake(sc, arm, *, tier_map=None, **_k):
        seen["map"] = tier_map
        return []

    monkeypatch.setattr(run, "run_chat", fake)
    monkeypatch.setattr(run, "router_up", lambda *_a, **_k: True)
    monkeypatch.setattr(run, "_write", lambda out, data: None)
    monkeypatch.setattr(run, "_lost_the_router", lambda rows: False)
    sc = scenario("five_turns")
    data = run.run_live(("http://127.0.0.1:1", "k"), (sc,), ("pack",), repeat=1, tier="t",
                        budget=5.0, out=tmp_path / "o.json", timeout=1, tier_map=MAP)
    assert data["tier_map"] == MAP and seen["map"] == MAP


def test_a_merge_refuses_a_mapped_and_an_unmapped_arm_under_one_name(tmp_path) -> None:
    """Mutation: drop the label check in ``merge`` -> the mismatch merges -> red."""
    plain = _chunk(tmp_path, "plain", {})
    mapped = _chunk(tmp_path, "mapped", MAP)
    # A mapped chunk whose rows carry the plain name.
    liar = _chunk(tmp_path, "liar", MAP, label_map={})
    with pytest.raises(ValueError, match="named 'pack'"):
        run.merge([liar], tmp_path / "o.json")
    # The same arm name with two maps.
    other = _chunk(tmp_path, "other", {"tier-powerful": "tier-fast"})
    other_data = json.loads(other.read_text(encoding="utf-8"))
    other_data["rows"][0]["arm_label"] = run.arm_label("pack", MAP)
    other.write_text(json.dumps(other_data), encoding="utf-8")
    with pytest.raises(ValueError):
        run.merge([mapped, other], tmp_path / "o.json")
    # Honest chunks merge, and the two arms stay apart.
    data = run.merge([plain, mapped], tmp_path / "o.json")
    assert sorted(data["summary"]) == ["pack", "pack[balanced=flash]"]
    assert data["tier_maps"] == {"pack[balanced=flash]": MAP} and "tier_map" not in data


def test_the_scripted_run_passes_with_a_map() -> None:
    """Mutation: drop ``tier_map`` from ``run_scripted`` -> the labels stay plain -> red."""
    rows, problems = run.run_scripted((scenario("five_turns"),), ("pack",), MAP)
    assert problems == [] and rows
    assert {r["arm_label"] for r in rows} == {"pack[balanced=flash]"}
    assert {r["arm"] for r in rows} == {"pack"}
    assert all(r["tier_map"] == MAP for r in rows)
