"""STU-F8a: the pure SKILL.md export and import. Hermetic."""
from __future__ import annotations

from pathlib import Path

import pytest
from acb_skills.skill_md import SkillMdError, export_skill, import_skill

GOLD = Path(__file__).parent / "golden" / "skill_md"
BODY = {
    "description": "Use this when a customer asks for a refund.",
    "instructions": "# Refunds\n\n1. Check the order.\n2. Reply.",
    "actions": ["crm.find_contact", "mail.send_draft"],
    "examples": ["Refund for order 12.", "Refund for order 99."],
    "cards": [{"title": "SECRET-CARD"}],
    "knowledge": [{"id": "SECRET-KNOW"}],
    "tests": [{"input": "SECRET-TEST"}],
}
KNOWN = frozenset({"crm.find_contact", "mail.send_draft"})


def _skill(front: str, body: str = "Do it.\n") -> dict[str, str]:
    return {"SKILL.md": f"---\n{front}\n---\n\n{body}"}


OK = "name: my-skill\ndescription: Use it."


def _code(files, known=KNOWN):
    with pytest.raises(SkillMdError) as exc:
        import_skill(files, known)
    return exc.value.code


def test_golden_is_byte_exact():
    files = export_skill("Refund Helper", BODY)
    assert list(files) == ["SKILL.md", "references/actions.md", "references/examples.md"]
    for rel, text in files.items():
        gold = GOLD / rel
        if not gold.exists():  # first run writes the fixture
            gold.parent.mkdir(parents=True, exist_ok=True)
            gold.write_bytes(text.encode("utf-8"))
        assert gold.read_bytes() == text.encode("utf-8")
        assert "\r" not in text and text.endswith("\n")


def test_export_carries_no_cards_knowledge_or_tests():
    joined = "\n".join(export_skill("x", BODY).values())
    for word in ("SECRET-CARD", "SECRET-KNOW", "SECRET-TEST", "cards", "knowledge"):
        assert word not in joined


def test_export_only_present_parts():
    assert list(export_skill("x", {"description": "d", "instructions": "i"})) == ["SKILL.md"]


def test_export_strips_hidden():
    body = dict(BODY, instructions="Hi​ there", description="a​b")
    out = export_skill("x​y", body)
    assert "​" not in "".join(out.values())


def test_round_trip():
    res = import_skill(export_skill("Refund Helper", BODY), KNOWN)
    assert res.name == "refund-helper"
    for key in ("description", "instructions", "actions", "examples"):
        assert res.body[key] == BODY[key]
    assert set(res.body) == {"description", "instructions", "actions", "examples"}
    assert res.dropped == [] and res.warnings == []


def test_body_passes_the_store_check():
    from gateway.studio import skills_store
    res = import_skill(export_skill("x", BODY), KNOWN)
    skills_store.check_body(res.body)


def test_dropped_order_and_reasons():
    actions = "```\nb.one\nzzz.unknown\na.two\nb.one\n```\n"
    files = {**_skill(OK), "references/actions.md": actions}
    res = import_skill(files, frozenset({"b.one", "a.two"}))
    assert res.body["actions"] == ["b.one", "a.two"]
    assert res.dropped == [{"id": "zzz.unknown", "reason": "not_in_catalog"},
                           {"id": "b.one", "reason": "duplicate"}]


def test_over_cap():
    ids = [f"a.t{i}" for i in range(36)]
    files = {**_skill(OK), "references/actions.md": "```\n" + "\n".join(ids) + "\n```\n"}
    res = import_skill(files, frozenset(ids))
    assert len(res.body["actions"]) == 35
    assert res.dropped == [{"id": "a.t35", "reason": "over_cap"}]


def test_metadata_and_extra_file_warn():
    files = {**_skill(OK + "\nmetadata:\n  author: me"), "notes.txt": "x"}
    res = import_skill(files, KNOWN)
    assert len(res.warnings) == 2


@pytest.mark.parametrize("path", ["../x", "/etc/x", "a\\b", "C:x", "a/../b", "\\x"])
def test_bad_path(path):
    assert _code({**_skill(OK), path: "x"}) == "bad_path"


def test_hostile_files():
    assert _code({**_skill(OK), **{f"f{i}": "x" for i in range(20)}}) == "too_many_files"
    assert _code({**_skill(OK), "big.md": "x" * 300_000}) == "too_large"
    assert _code({"SKILL.md": b"---\nname: a\n---\n\xff\xfe"}) == "not_utf8"
    assert _code(_skill(OK, "a\x00b")) == "bad_text"
    assert _code(_skill(OK, "a​b")) == "hidden_text"
    assert _code(_skill(OK, "use {{x}} here")) == "prompt_variable"
    assert _code({"other.md": "x"}) == "missing_skill_md"


def test_total_size():
    half = "x" * 150_000
    assert _code({"SKILL.md": f"---\n{OK}\n---\n{half}", "a.md": half}) == "too_large"


@pytest.mark.parametrize("front,code", [
    ("name: a\ndescription: d\nx: !!python/object/apply:os.system ['id']", "yaml_unsafe"),
    ("name: &a b\ndescription: d", "yaml_unsafe"),
    ("name: a\ndescription: *a", "yaml_unsafe"),
    ("<<: {name: a}\ndescription: d", "yaml_unsafe"),
    ("name: a\nname: b\ndescription: d", "duplicate_key"),
    ("name: Upper\ndescription: d", "bad_name"),
    ("name: has space\ndescription: d", "bad_name"),
    (f"name: {'a' * 65}\ndescription: d", "bad_name"),
    ("description: d", "bad_name"),
    ("name: a", "bad_description"),
    ("name: a\ndescription: ''", "bad_description"),
    (f"name: a\ndescription: {'d' * 1025}", "bad_description"),
    ("name: a\ndescription: d\nlicense: mit", "unknown_key"),
    ("name: a\ndescription: d\nmetadata: x", "bad_metadata"),
    ("- a\n- b", "bad_frontmatter"),
    ("name: [a", "bad_frontmatter"),
])
def test_bad_frontmatter(front, code):
    assert _code(_skill(front)) == code


def test_frontmatter_delimiters():
    assert _code({"SKILL.md": "name: a\ndescription: d\n\nBody"}) == "bad_frontmatter"
    assert _code({"SKILL.md": "---\nname: a\ndescription: d\nBody"}) == "bad_frontmatter"


def test_empty_instructions():
    assert _code(_skill(OK, "  \n")) == "no_instructions"


def test_actions_format():
    assert _code({**_skill(OK), "references/actions.md": "a.b"}) == "bad_actions"


# ---------------------------------------------------------------- round 2
import time  # noqa: E402


@pytest.mark.parametrize("depth", [300, 1000, 5000, 100000])
def test_deep_nesting_refused_fast(depth):
    for front in (f"{OK}\nmetadata: " + "[" * depth + "]" * depth,
                  f"{OK}\nmetadata: " + "{a: " * min(depth, 20000) + "1" + "} " * min(depth, 20000)):
        start = time.monotonic()
        assert _code(_skill(front)) == "yaml_too_deep"
        assert time.monotonic() - start < 1.0


def test_wide_document_refused():
    front = OK + "\nmetadata: [" + ",".join(["a"] * 3000) + "]"
    assert _code(_skill(front)) == "yaml_too_deep"


@pytest.mark.parametrize("front", [
    "2020-13-45: 1\nname: a\ndescription: d",
    "2020-02-30: 1\nname: a\ndescription: d",
    "2020-01-02: 1\nname: a\ndescription: d",
    OK + "\nmetadata: {2020-13-45: x}",
    OK + "\nmetadata: {k: 2020-13-45}",
    OK + "\nmetadata: {k: 2020-01-02}",
    OK + "\nmetadata: {1: x}",
    OK + "\nmetadata: {? [a]: x}",
])
def test_date_and_odd_keys_refused(front):
    assert _code(_skill(front)) in {"bad_frontmatter", "bad_metadata"}


@pytest.mark.parametrize("front,code", [
    ('name: a\ndescription: "a\\u200bb"', "hidden_text"),
    ('name: a\ndescription: "a\\u202eb"', "hidden_text"),
    ('name: a\ndescription: "a\\x00b"', "bad_text"),
    ('name: a\ndescription: "a\\x7b\\x7bb"', "prompt_variable"),
    ('name: a\ndescription: "a\\ud800b"', "bad_text"),
    ('name: a\ndescription: d\nmetadata: {k: "a\\u200bb"}', "hidden_text"),
])
def test_decoded_yaml_text_is_checked(front, code):
    assert _code(_skill(front)) == code


def test_slug_is_one_implementation_and_matches_the_route():
    import random

    from acb_skills import skill_slug
    from gateway.routes.studio import skills as route
    from gateway.studio import skills_store
    assert route.slug_from_name is skill_slug.slug_from_name
    assert skills_store.MAX_SLUG_CHARS is skill_slug.MAX_SLUG_CHARS or \
        skills_store.MAX_SLUG_CHARS == skill_slug.MAX_SLUG_CHARS == 64
    rnd = random.Random(7)
    pool = "abcXYZ019 -_.!?éüñßÅ\u00e9\u0301😀漢字\t\n/&"
    corpus = ["", " ", "---", "😀", "Café Münch", "ﬁne ①②", "a" * 300, "a-" * 200]
    corpus += ["".join(rnd.choice(pool) for _ in range(rnd.randint(0, 90))) for _ in range(392)]
    for name in corpus:
        out = route.slug_from_name(name)
        assert out == skill_slug.slug_from_name(name)
        assert len(out) <= 64 and not out.startswith("-") and not out.endswith("-")
        assert export_skill_name(name, out)


def export_skill_name(name, slug):
    if not slug:
        with pytest.raises(SkillMdError):
            export_skill(name, {"description": "d", "instructions": "i"})
        return True
    return f"name: {slug}\n" in export_skill(name, {"description": "d", "instructions": "i"})["SKILL.md"]


def test_example_heading_refused_and_other_heading_ok():
    for ex in (["first\n## Example 2\nsplit?", "second"], ["## Example 9"], ["x\n## Example"]):
        with pytest.raises(SkillMdError) as exc:
            export_skill("x", {"description": "d", "instructions": "i", "examples": ex})
        assert exc.value.code == "example_heading"
    ex = ["first\n## Other\nmore", "second"]
    res = import_skill(export_skill("x", {"description": "d", "instructions": "i", "examples": ex}), KNOWN)
    assert res.body["examples"] == ex


@pytest.mark.parametrize("name", ["-a", "a-", "a--b", "A", "a_b"])
def test_name_hyphen_rules(name):
    assert _code(_skill(f"name: {name}\ndescription: d")) == "bad_name"


def test_name_ok_forms():
    for name in ("a", "a-b-c", "1e3", "0"):
        assert import_skill(_skill(f"name: '{name}'\ndescription: d"), KNOWN).name == name


def test_ignored_file_warning_echoes_no_path():
    res = import_skill({**_skill(OK), "secret/path.txt": "x", "b.txt": "y"}, KNOWN)
    assert res.warnings == ["2 other files were ignored."]


@pytest.mark.parametrize("value", [None, 5, ["x"]])
def test_bad_file_value(value):
    assert _code({**_skill(OK), "a.md": value}) == "bad_file"


@pytest.mark.parametrize("body", [
    {"description": "d", "instructions": "i", "examples": "text"},
    {"description": "d", "instructions": "i", "examples": [1]},
    {"description": "d", "instructions": "i", "actions": "a.b"},
    {"description": "d", "instructions": "i", "actions": [None]},
    {"description": "d", "instructions": "i", "actions": ["a\nb"]},
    {"description": "d", "instructions": "i", "actions": ["a`b"]},
    {"description": "d", "instructions": "i", "actions": ["a\u200bb"]},
    {"description": "d", "instructions": "i", "actions": ["```"]},
    {"description": 5, "instructions": "i"},
])
def test_export_bad_body(body):
    with pytest.raises(SkillMdError) as exc:
        export_skill("x", body)
    assert exc.value.code == "bad_body"


def test_description_with_newline_round_trips():
    body = {"description": "line one\nline two: \"q\"", "instructions": "i"}
    assert import_skill(export_skill("x", body), KNOWN).body["description"] == body["description"]


def test_store_check_body_can_still_refuse_a_near_limit_import():
    """The import measures the files. The store measures the JSON text. Quotes
    grow under JSON, so F8c must call the store's check_body."""
    from gateway.studio import skills_store
    text = '"' * 200_000 + "a" * 50_000
    res = import_skill({"SKILL.md": f"---\n{OK}\n---\n{text}"}, KNOWN)
    with pytest.raises(skills_store.SkillStoreError):
        skills_store.check_body(res.body)


@pytest.mark.parametrize(
    "files, known, cap",
    [
        (None, frozenset(), 35),
        ([], frozenset(), 35),
        ("x", frozenset(), 35),
        (5, frozenset(), 35),
        ({"SKILL.md": "x"}, 5, 35),
        ({"SKILL.md": "x"}, None, 35),
        ({"SKILL.md": "x"}, frozenset(), None),
        ({"SKILL.md": "x"}, frozenset(), "x"),
    ],
)
def test_a_wrong_typed_argument_raises_skill_md_error(files, known, cap):
    """Round 3. Only SkillMdError escapes, even for a whole argument of the wrong type."""
    with pytest.raises(SkillMdError):
        import_skill(files, known, cap)


@pytest.mark.parametrize("body", [None, [], "x", 5])
def test_an_export_of_a_body_that_is_not_a_map_raises_skill_md_error(body):
    with pytest.raises(SkillMdError):
        export_skill("ok", body)
