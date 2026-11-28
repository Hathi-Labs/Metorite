"""Task 4053: an edit to a tool that does not mark the chat fails until a
person classifies it again.

``tool_packs.PACK_OUTSIDE_TEXT`` says, for each tool of a pack whose owner
reads stranger text, if its answer can bring outside text into the chat. An
entry ``True`` marks each call. An entry ``False`` marks no call. A tuple of
argument names marks a call that passes one of them with a value, so a call
with none of them marks nothing. ``test_pack_outside_text.py`` holds each
tool to one entry, but not to what the tool answers. While PR 2518 was open,
three tools started to quote drafts or mail, and no test went red.

This fence pins a digest for each ``(owner, tool)`` whose entry is ``False``
or a tuple. The digest covers the tool and each helper that the walk of
``tests/unit/_source_digest.py`` reaches from it. The idiom is the digest
fence of ``test_tenant_request_binding.py`` (``_branch_zero_digest``): the
source is cut from the module FILE by the AST, it is normalised by
``ast.dump(ast.parse(source))``, and each name and its dump go into one
SHA-256 in sorted order. The pins are keyed by the Python minor version.

The helper rule. A helper is each name that the code loads and that a
top-level ``def``, ``async def``, ``class`` or bare-name assignment of the
SAME module binds: a call, a callback passed by name, a decorator, or a
constant. The walk repeats on each helper, up to ``WALK_DEPTH`` (8) hops. A
walk that still finds a new helper at that depth fails. Today each walk ends
at 5 hops.

What the walk does not follow, and why that is safe here:

* A name that the module imports, at the top or inside a function or a
  ``try`` (``_fenced``, ``request_confirmation``, ``_annotate_risk``). Each is
  a shared seam of another package, with its own fences. It frames text or
  asks a card, and it reads no mail by itself.
* An attribute call (``client.post``, ``_log.info``, ``_CARD_KINDS.get``).
  The object of a module constant is in the digest, because the walk loads
  its name.
* ``getattr``. Each call in the walk today reads a fixed attribute name of an
  exception or a card answer (``detail``, ``choice``, ``subject``,
  ``body``). None chooses a function.

The cross-module pins. Some imported helpers put the text of a stored draft
into the answer of ``send_email``. :data:`CROSS_MODULE_PINS` names each one
by its module FILE and its function. The fence digests each one with the
same walk, inside ITS OWN module, with the same depth rule. An edit fails
with the same words: classify the tool again.

The known limits. The walk covers the helpers of the same module and the
named cross-module pins. It does not cover the other imported seams, such as
``refusals_as_text``, ``ask_tools`` and ``UntrustedFrame``. It does not cover
the gateway routes either. A tool prints the JSON of a route, and the route
can change what that JSON holds. A change there does not fail this fence.

When this fence fails, read the answer of the tool again. Classify it in
``PACK_OUTSIDE_TEXT``. When it stays ``False`` or a tuple, put the new digest
in :data:`DIGESTS` in the same commit. A tool that now marks (``True``) leaves
:data:`DIGESTS`.

Run: ``uv run pytest tests/unit/test_pack_outside_text_digest.py -q``.
"""
from __future__ import annotations

import ast
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp

from tests.unit import _source_digest as sd

REPO = Path(__file__).resolve().parents[2]
EA = "email-assistant"

#: The module FILE of each owner of :data:`egress.UNTRUSTED_INPUT_AGENTS`,
#: relative to the repository. The tools of its packs are its top-level
#: functions of the same name.
OWNER_FILES: dict[str, str] = {
    EA: "apps/agents/agent-email-assistant/agents.py",
}

#: SHA-256 of each tool that does not always mark, and of the helpers that
#: build its answer, keyed by the Python minor version and ``(owner, tool)``.
#: Task 4053 (2026-11-27): computed by ``_source_digest.digest`` under CPython
#: 3.12.16 and 3.13.18. Update an entry ONLY in the commit that classifies the
#: tool again.
DIGESTS: dict[tuple[int, int], dict[tuple[str, str], str]] = {
    (3, 12): {
        (EA, "apply_settings_to_mailboxes"):
            "b679ee247f107583b83cfcdf20cd1fc3bd5b9ac33b766718fe8eb9c7b1af057d",
        (EA, "bulk_manage_inbox"):
            "b1e82e8bbdc3af9a8bafd75d3c461722868d765ea5b0f7ea262de1defceb6138",
        (EA, "cancel_scheduled_send"):
            "843a17675d64b1c663c10ecfeb9793dc142f0f47d748824e11f7ad86a4f8ed00",
        (EA, "create_label"):
            "1ecacb1887eb3b897bb756ac404a0c12f3545cb8365a1d2bbbc7d26dcae332e5",
        (EA, "create_rule"):
            "5285ee1f727ebef543ab58de9c563326509644c4e58476e24bc48328c9607170",
        (EA, "delete_knowledge"):
            "195809c3142e1d46c2153880fec785fe6bc7c49b5c653395c1a1b02033f75db5",
        (EA, "delete_rule"):
            "933219d8c5fa22fab532772f748520b318a058ac90878d5e1689dda90b81918f",
        (EA, "install_default_rules"):
            "312c92c5533db934482dbe97f7884ecfac47a7d4c78eb03720d5330ccbe12b9d",
        (EA, "list_accounts"):
            "f755d9d8ed1b92f1ce742d9abef2f0f789ab3e3a023e1a4a6615aa5f6b85b8a2",
        (EA, "mark_thread_done"):
            "84986ea0eaa65c3dc01fd3b1ac09b2eeeb18aaab7d9287b41768134a3f1e56a0",
        (EA, "reclassify_reply_zero"):
            "85ace65c38201f2ff0fe4bda8f616641d1febf3192c656228dae3218f7bbf749",
        (EA, "save_knowledge"):
            "be9778e336cecef196d3c4648b09c2f42c0177a9a06bdeb253c5d1821bff1c02",
        (EA, "send_email"):
            "6d3b067ec477141c904ca801d561e4c0592b46cefd0b58b4fba66cbc38454fac",
        (EA, "sync_account"):
            "4614daaa83c4049ba8bd6e0928a17892537ceed6bab1848240ff7ff14834eea1",
        (EA, "update_account"):
            "c9e4888874cc048007e0f5df95d17da52bbe203fa8d58aec388dd81716f8a680",
        (EA, "update_assistant_settings"):
            "86bf0ebd63fd6199cec423d0bc162ee3f31ac09d92caac8105fdbb296168b4b1",
    },
    (3, 13): {
        (EA, "apply_settings_to_mailboxes"):
            "026229a726c2718119269aacc64ec9d043c87a95abd5755f2834685f7ea5ae69",
        (EA, "bulk_manage_inbox"):
            "3b03c9fe05cb96d0ca0a6663eb4cb37fd1a89b6514488de231ec710410732158",
        (EA, "cancel_scheduled_send"):
            "883412b82e0d213933b4b5bd343905a3aba75aa2652db7c27b060679d2815c6d",
        (EA, "create_label"):
            "8ebc8a8ea9bfaf1edc1d17a00c62ee524cb79f6faffcbd3f4d90144f1458a0f1",
        (EA, "create_rule"):
            "209b8180d74d42f9239822d50a42a0ad76bda919978c28b44f2b31b54e806371",
        (EA, "delete_knowledge"):
            "1ccfcb4b61f9790b16bc46a45bc0541c0a7f542ef4b03ae5c635649c70d7c701",
        (EA, "delete_rule"):
            "6dc8c3c69ec07749694fbb0b9c558d6657dbcd5348afd92c4c2d091d7bb1b0b9",
        (EA, "install_default_rules"):
            "75ddc5fc04653d7b63c685a105a2dc5291f5b8fe62e4ede79312bd08d093b5c9",
        (EA, "list_accounts"):
            "4d64ac973019f7037b58960cf15fb5fcc340e4d11d3f53fbf26bf73eba32d06c",
        (EA, "mark_thread_done"):
            "a20f872924740106d5e76e4f93833e066f5c29d5a7f530707601bac3e8a9f94b",
        (EA, "reclassify_reply_zero"):
            "5c529c2a93cebc4867a4fd7b8f4cc93b76c1c818f37c49cf82eaae76cebc8a2e",
        (EA, "save_knowledge"):
            "ca686a6d9a4f8cd6fc488c21b6280368f0503676b75fc2316eaa82d3ebafb9ec",
        (EA, "send_email"):
            "034bbfb4d0dfcc468900d19ba44a4eb8234eaef8fa945bf4f9952d5ca48df5c4",
        (EA, "sync_account"):
            "fa229072fc390a0bccd9b9bd63c129784c01c92f8aaa5069d3c4182b8491b743",
        (EA, "update_account"):
            "82355ab15f2cf0539a7afffd894689bacc7c49e9c200073627ef76b080139d5e",
        (EA, "update_assistant_settings"):
            "405bd6ab63a496d2c1d0ca912a15a2580f237130df64fe5ba08e011b077d4534",
    },
}

#: The imported helpers that put the text of a stored draft into the answer
#: of ``send_email`` (task 4053, review follow-up). Each is a module FILE,
#: relative to the repository, and a top-level function of that FILE.
CROSS_MODULE_PINS: tuple[tuple[str, str], ...] = (
    ("packages/acb_skills/acb_skills/card_revise.py", "target_state"),
    ("packages/acb_skills/acb_skills/draft_choice.py", "describe"),
    ("packages/acb_skills/acb_skills/draft_choice.py", "report"),
)

#: SHA-256 of each entry of :data:`CROSS_MODULE_PINS` and of the helpers of
#: its own module that the walk reaches, keyed by the Python minor version.
#: Computed by ``_source_digest.digest`` under CPython 3.12.16 and 3.13.18
#: (2026-11-28). Update an entry ONLY in the commit that classifies
#: ``send_email`` again.
CROSS_DIGESTS: dict[tuple[int, int], dict[tuple[str, str], str]] = {
    (3, 12): {
        ("packages/acb_skills/acb_skills/card_revise.py", "target_state"):
            "7f17dc7c59048580ab9e9b2c94fe8d0c61b2f39e409e8a3b1baf4f167ed8c21f",
        ("packages/acb_skills/acb_skills/draft_choice.py", "describe"):
            "2d346145908be74d8e76e594d7421e32426cfef562450c6551c4cc5fabb403df",
        ("packages/acb_skills/acb_skills/draft_choice.py", "report"):
            "931a3bc1bae5ab39b921e660aedf5957af3097b84e0c2e7323be47e0dac08e44",
    },
    (3, 13): {
        ("packages/acb_skills/acb_skills/card_revise.py", "target_state"):
            "ed9fe1601f232a3108f44f023c1d9e11dda69386fa81ad8a6cbe03646b31d3f9",
        ("packages/acb_skills/acb_skills/draft_choice.py", "describe"):
            "fd415235f9cd52605ca34891b3a05c25ae1826549c8819a97964a6e1e8fbe651",
        ("packages/acb_skills/acb_skills/draft_choice.py", "report"):
            "98f4092643352fb7d16f6d2722ce9d5b845c7d69f009c8ceebf2df3403a308d1",
    },
}


def _cross_problems(entries: tuple[tuple[str, str], ...],
                    pins: Mapping[tuple[str, str], str], root: Path) -> list[str]:
    """Each way in which *pins* do not hold *entries*. *root* stands for the
    repository, so a red proof can pass a copy."""
    problems: list[str] = []
    texts: dict[str, str] = {}
    for rel, fn in entries:
        if (rel, fn) not in pins:
            problems.append(
                f"{rel}:{fn} has no digest. Read the answer of send_email, classify "
                "it in tool_packs.PACK_OUTSIDE_TEXT, and then add its digest to "
                "CROSS_DIGESTS.")
            continue
        text = texts.setdefault(rel, sd.module_text(str(root / rel)))
        try:
            got = sd.digest(text, fn)
        except KeyError:
            problems.append(f"{rel}:{fn} is no top-level function of {rel}.")
            continue
        except ValueError as exc:
            problems.append(f"{rel}:{fn}: {exc}. Raise WALK_DEPTH after a review.")
            continue
        if got != pins[(rel, fn)]:
            problems.append(
                f"{rel}:{fn} changed, or a helper that builds its answer changed. "
                "Read the answer of send_email again and classify it in "
                "tool_packs.PACK_OUTSIDE_TEXT. Then update its digest in "
                f"CROSS_DIGESTS in the same commit: {got}")
    for rel, fn in sorted(set(pins) - set(entries)):
        problems.append(
            f"{rel}:{fn} has a stale digest: CROSS_MODULE_PINS does not name it. "
            "Remove it from CROSS_DIGESTS.")
    return problems


def _covered(rules: Mapping[tuple[str, str], object]) -> set[tuple[str, str]]:
    """Each pair whose entry is ``False`` or a tuple of argument names."""
    return {key for key, rule in rules.items() if rule is False or isinstance(rule, tuple)}


def _problems(rules: Mapping[tuple[str, str], object],
              pins: Mapping[tuple[str, str], str],
              files: Mapping[str, str]) -> list[str]:
    """Each way in which *pins* do not hold the covered tools of *rules*.
    *files* gives the module FILE of each owner."""
    covered = _covered(rules)
    problems: list[str] = []
    texts: dict[str, str] = {}
    for owner, tool in sorted(covered):
        if owner not in files:
            problems.append(f"{owner}: no module FILE. Add it to OWNER_FILES.")
            continue
        if (owner, tool) not in pins:
            problems.append(
                f"{owner}:{tool} has no digest. Read its answer, classify it in "
                "tool_packs.PACK_OUTSIDE_TEXT, and then add its digest to DIGESTS.")
            continue
        text = texts.setdefault(owner, sd.module_text(files[owner]))
        try:
            got = sd.digest(text, tool)
        except KeyError:
            problems.append(f"{owner}:{tool} is no top-level function of {files[owner]}.")
            continue
        except ValueError as exc:
            problems.append(f"{owner}:{tool}: {exc}. Raise WALK_DEPTH after a review.")
            continue
        if got != pins[(owner, tool)]:
            problems.append(
                f"{owner}:{tool} changed, or a helper that builds its answer changed. "
                "Read its answer again and classify it in tool_packs.PACK_OUTSIDE_TEXT. "
                f"Then update its digest in DIGESTS in the same commit: {got}")
    for owner, tool in sorted(set(pins) - covered):
        problems.append(
            f"{owner}:{tool} has a stale digest: the map does not classify it as "
            "False or a tuple. Remove it from DIGESTS.")
    return problems


def _repo_files() -> dict[str, str]:
    return {owner: str(REPO / rel) for owner, rel in OWNER_FILES.items()}


def _pins(table: Mapping[tuple[int, int], dict[tuple[str, str], str]] = DIGESTS,
          ) -> dict[tuple[str, str], str]:
    version = sys.version_info[:2]
    assert version in table, (
        f"No digest is pinned for Python {version[0]}.{version[1]}. "
        "Add one after review.")
    return table[version]


# ── 1. The fence on the tree ────────────────────────────────────────────────


def test_each_tool_that_does_not_mark_is_pinned() -> None:
    """Mutation: edit a covered tool, or a helper that it reaches → red."""
    covered = _covered(tp.PACK_OUTSIDE_TEXT)
    assert len(covered) >= 10, "non-vacuity: the email packs hold the covered tools"
    assert (EA, "send_email") in covered, "non-vacuity: the tuple entry is covered"
    problems = _problems(tp.PACK_OUTSIDE_TEXT, _pins(), _repo_files())
    assert problems == [], "\n".join(problems)


def test_each_cross_module_pin_holds() -> None:
    """Mutation: edit ``draft_choice.describe``, ``draft_choice.report`` or
    ``card_revise.target_state``, or a helper of its module → red."""
    assert CROSS_MODULE_PINS, "non-vacuity"
    assert set(CROSS_DIGESTS) == set(DIGESTS), "pin the same Python versions"
    problems = _cross_problems(CROSS_MODULE_PINS, _pins(CROSS_DIGESTS), REPO)
    assert problems == [], "\n".join(problems)


def test_each_owner_that_reads_stranger_text_has_a_module_file() -> None:
    """A new owner of ``UNTRUSTED_INPUT_AGENTS`` fails until its FILE is
    named. Mutation: drop the entry of OWNER_FILES → red."""
    owners = {str(sf.SKILL_FAMILIES[slug].get("owner")) for slug in sf.pack_slugs()}
    for owner in owners & set(eg.UNTRUSTED_INPUT_AGENTS):
        assert owner in OWNER_FILES, owner
        assert (REPO / OWNER_FILES[owner]).is_file(), OWNER_FILES[owner]


def test_the_walk_reaches_helpers_and_ends() -> None:
    """The digest of a tool covers more than its own body, and each walk ends
    inside the bound. Mutation: walk zero hops → red."""
    text = sd.module_text(_repo_files()[EA])
    defs = sd.top_level(text)
    for _owner, tool in sorted(_covered(tp.PACK_OUTSIDE_TEXT)):
        assert len(sd.closure(defs, tool)) > 1, tool


# ── 2. The red proofs, on a copy of the owner package ───────────────────────


def _copy(tmp_path: Path) -> Path:
    """A copy of the email assistant package. Returns its ``agents.py``."""
    src = REPO / OWNER_FILES[EA]
    dst = tmp_path / src.parent.name
    shutil.copytree(src.parent, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst / src.name


def _append_to_body(path: Path, name: str) -> None:
    """Add one statement at the end of the body of the top-level *name*."""
    text = path.read_text(encoding="utf-8-sig")
    node = next(n for n in ast.parse(text).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = text.splitlines(keepends=True)
    indent = " " * node.body[-1].col_offset
    lines.insert(node.end_lineno, f"{indent}_t4053_probe = 1\n")
    path.write_text("".join(lines), encoding="utf-8")


def _problems_of(path: Path, rules: Mapping[tuple[str, str], object] | None = None) -> list[str]:
    return _problems(tp.PACK_OUTSIDE_TEXT if rules is None else rules, _pins(), {EA: str(path)})


def _green_copy(tmp_path: Path) -> Path:
    """A fresh copy, held green BEFORE a red proof edits it. So a copy that
    is red already cannot make a red proof pass for the wrong reason."""
    path = _copy(tmp_path)
    assert _problems_of(path) == [], "the fresh copy must be green"
    return path


def test_an_edit_to_the_body_of_a_tool_goes_red(tmp_path: Path) -> None:
    path = _green_copy(tmp_path)
    _append_to_body(path, "create_label")
    problems = _problems_of(path)
    assert any(p.startswith(f"{EA}:create_label changed") for p in problems), problems


def test_an_edit_to_a_helper_goes_red(tmp_path: Path) -> None:
    """An edit to a helper that a covered tool calls, and that is no tool."""
    path = _green_copy(tmp_path)
    text = path.read_text(encoding="utf-8-sig")
    defs = sd.top_level(text)
    helper = sorted(
        name for name in sd.closure(defs, "sync_account")
        if name != "sync_account"
        and isinstance(defs[name][0], (ast.FunctionDef, ast.AsyncFunctionDef))
        and (EA, name) not in tp.PACK_OUTSIDE_TEXT)[0]
    _append_to_body(path, helper)
    problems = _problems_of(path)
    assert any(p.startswith(f"{EA}:sync_account changed") for p in problems), (helper, problems)


def test_a_new_tool_that_does_not_mark_and_has_no_digest_goes_red(tmp_path: Path) -> None:
    path = _green_copy(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('\n\nasync def t4053_new_tool() -> str:\n    return "Done."\n')
    rules = {**tp.PACK_OUTSIDE_TEXT, (EA, "t4053_new_tool"): False}
    problems = _problems_of(path, rules)
    assert any(p.startswith(f"{EA}:t4053_new_tool has no digest") for p in problems), problems


def test_a_stale_digest_of_a_removed_tool_goes_red(tmp_path: Path) -> None:
    path = _green_copy(tmp_path)
    rules = {k: v for k, v in tp.PACK_OUTSIDE_TEXT.items() if k != (EA, "create_label")}
    problems = _problems_of(path, rules)
    assert any(p.startswith(f"{EA}:create_label has a stale digest") for p in problems), problems


def test_a_tool_that_now_marks_leaves_a_stale_digest(tmp_path: Path) -> None:
    """A tool classified True again keeps no pin. Mutation: cover True → red."""
    rules = {**tp.PACK_OUTSIDE_TEXT, (EA, "create_label"): True}
    problems = _problems_of(_green_copy(tmp_path), rules)
    assert any(p.startswith(f"{EA}:create_label has a stale digest") for p in problems), problems


def _green_skills_copy(tmp_path: Path) -> Path:
    """A copy of ``packages/acb_skills/acb_skills`` under a stand-in for the
    repository, held green before an edit. Returns the stand-in root."""
    src = REPO / "packages/acb_skills/acb_skills"
    shutil.copytree(src, tmp_path / "packages/acb_skills/acb_skills",
                    ignore=shutil.ignore_patterns("__pycache__"))
    assert _cross_problems(CROSS_MODULE_PINS, _pins(CROSS_DIGESTS), tmp_path) == [], (
        "the fresh copy must be green")
    return tmp_path


def test_an_edit_to_a_cross_module_pin_goes_red(tmp_path: Path) -> None:
    root = _green_skills_copy(tmp_path)
    rel = "packages/acb_skills/acb_skills/draft_choice.py"
    _append_to_body(root / rel, "describe")
    problems = _cross_problems(CROSS_MODULE_PINS, _pins(CROSS_DIGESTS), root)
    assert any(p.startswith(f"{rel}:describe changed") for p in problems), problems


def test_a_cross_module_pin_with_no_digest_and_a_stale_one_go_red(tmp_path: Path) -> None:
    root = _green_skills_copy(tmp_path)
    rel = "packages/acb_skills/acb_skills/draft_choice.py"
    pins = {k: v for k, v in _pins(CROSS_DIGESTS).items() if k != (rel, "report")}
    problems = _cross_problems(CROSS_MODULE_PINS, pins, root)
    assert any(p.startswith(f"{rel}:report has no digest") for p in problems), problems
    entries = tuple(e for e in CROSS_MODULE_PINS if e != (rel, "report"))
    problems = _cross_problems(entries, _pins(CROSS_DIGESTS), root)
    assert any(p.startswith(f"{rel}:report has a stale digest") for p in problems), problems


# ── 3. The key holds the owner ──────────────────────────────────────────────


def test_each_key_is_an_owner_and_a_tool() -> None:
    """Mutation: key an entry on the bare tool name again → red."""
    for key in tp.PACK_OUTSIDE_TEXT:
        assert isinstance(key, tuple) and len(key) == 2, key
        assert all(isinstance(part, str) and part for part in key), key


def test_two_packs_with_one_tool_name_do_not_collide(monkeypatch) -> None:
    """Two owners that each read stranger text hold a tool of one name. Each
    owner's entry decides for its own call. Mutation: key on the tool name
    alone → one entry wins for both → red."""
    other = "t4053-other-owner"
    rules = {(EA, "t4053_list"): False, (other, "t4053_list"): True}
    assert len(rules) == 2
    monkeypatch.setattr(tp, "PACK_OUTSIDE_TEXT", rules)
    monkeypatch.setattr(eg, "UNTRUSTED_INPUT_AGENTS", frozenset({EA, other}))
    assert tp.marks_chat(EA, "t4053_list", {}) is False
    assert tp.marks_chat(other, "t4053_list", {}) is True
    # A third owner that the map does not name still fails closed.
    monkeypatch.setattr(eg, "UNTRUSTED_INPUT_AGENTS", frozenset({EA, other, "t4053-third"}))
    assert tp.marks_chat("t4053-third", "t4053_list", {}) is True
