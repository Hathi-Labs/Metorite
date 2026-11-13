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
