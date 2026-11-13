"""Studio skill <-> Agent Skills folder (``SKILL.md``), pure (STU-F8a).

``export_skill`` turns a skill body into a folder: a map of relative path to
text. ``import_skill`` reads such a folder and answers a body.

This module is pure. It does no I/O, no DB and no network call, and it imports
nothing of the gateway. No route uses it yet (F8c). An imported skill is only
a body. The later route must run the store's ``check_body`` on it and land it
private and unpublished, through ``create_skill`` and ``add_version``.

Export carries four parts of the body: description, instructions, actions and
examples. It never carries cards, knowledge links, tests, ids or owner data.

Import reads UNTRUSTED text. It refuses unsafe paths, too many files, too
much text, bad UTF-8, hidden characters, prompt variables and any frontmatter
that is not plain. YAML goes through ``yaml.safe_load`` only, after a scan that
refuses anchors, aliases, tags, merge keys and duplicate keys.

Frontmatter rules: the keys are ``name``, ``description`` and ``metadata``
and no others. ``name`` is required: lowercase letters, digits and hyphens,
at most 64 characters. ``description`` is required, at most 1024 characters.
``metadata`` is optional, a map of short strings, and it only gives a warning.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from acb_skills.hidden_text import hidden_problem, strip_hidden
from acb_skills.tool_packs import MAX_PACK_TOOLS

__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_FILES",
    "MAX_META_ITEMS",
    "MAX_META_VALUE_CHARS",
    "MAX_NAME_CHARS",
    "MAX_TEXT_BYTES",
    "ImportResult",
    "SkillMdError",
    "export_skill",
    "import_skill",
]

MAX_FILES = 20
MAX_TEXT_BYTES = 262144  # the store's MAX_BODY_BYTES
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
MAX_META_ITEMS = 20
MAX_META_VALUE_CHARS = 200

_ALLOWED_KEYS = frozenset({"name", "description", "metadata"})
_NAME = re.compile(r"[a-z0-9-]{1,64}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_EXAMPLE_HEAD = re.compile(r"^## Example \d+$")
_SKILL = "SKILL.md"
_ACTIONS = "references/actions.md"
_EXAMPLES = "references/examples.md"
_ACTIONS_NOTE = "These are Metorite catalog actions. Another client cannot run them."
_FENCE = "```"


class SkillMdError(ValueError):
    """A refusal. ``code`` is fixed. ``message`` is one sentence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ImportResult:
    name: str
    body: dict[str, Any]
    dropped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- export

def _slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:MAX_NAME_CHARS].rstrip("-")


def _clean(text: object) -> str:
    return strip_hidden(str(text)).replace("\r\n", "\n").replace("\r", "\n").strip()


def _quote(text: str) -> str:
    """A YAML double-quoted scalar. JSON text is valid YAML."""
    return json.dumps(text, ensure_ascii=False)


def export_skill(name: str, body: Mapping[str, Any]) -> dict[str, str]:
    """The folder of a skill: ``{relative path: text}``, in a stable order."""
    slug = _slug(_clean(name))
    if not slug:
        raise SkillMdError("name_not_slug", "The name gives no slug of letters and digits.")
    description = _clean(body.get("description") or "")
    instructions = _clean(body.get("instructions") or "")
    head = f"---\nname: {slug}\ndescription: {_quote(description)}\n---\n\n"
    files = {_SKILL: head + instructions + "\n"}
    actions = [_clean(a) for a in (body.get("actions") or []) if _clean(a)]
    if actions:
        files[_ACTIONS] = (
            "# Actions\n\n" + _ACTIONS_NOTE + "\n\n" + _FENCE + "\n"
            + "\n".join(actions) + "\n" + _FENCE + "\n")
    examples = [_clean(e) for e in (body.get("examples") or []) if _clean(e)]
    if examples:
        parts = [f"## Example {i}\n\n{text}\n" for i, text in enumerate(examples, 1)]
        files[_EXAMPLES] = "# Examples\n\n" + "\n".join(parts)
    return files


# ---------------------------------------------------------------- import

def _bad_path(path: str) -> bool:
    return (not path or "\x00" in path or "\\" in path or ".." in path
            or path.startswith("/") or bool(_DRIVE.match(path)))


def _read_files(files: Mapping[str, str | bytes]) -> dict[str, str]:
    if len(files) > MAX_FILES:
        raise SkillMdError("too_many_files", f"A skill folder holds at most {MAX_FILES} files.")
    out: dict[str, str] = {}
    total = 0
    for path, raw in files.items():
        if not isinstance(path, str) or _bad_path(path):
            raise SkillMdError("bad_path", "A file path must be relative, with no '..' and no backslash.")
        size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8", "surrogatepass"))
        total += size
        if size > MAX_TEXT_BYTES or total > MAX_TEXT_BYTES:
            raise SkillMdError("too_large", f"The folder holds at most {MAX_TEXT_BYTES} bytes.")
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise SkillMdError("not_utf8", f"The file {path} is not UTF-8 text.") from None
        else:
            text = raw
        if "\x00" in text:
            raise SkillMdError("bad_text", f"The file {path} holds a NUL character.")
        problem = hidden_problem(text, path)
        if problem is not None:
            raise SkillMdError("hidden_text", problem)
        if "{{" in text or "}}" in text:
            raise SkillMdError("prompt_variable", f"The file {path} holds '{{{{' or '}}}}'. Remove it.")
        out[path] = text.replace("\r\n", "\n").replace("\r", "\n")
    if _SKILL not in out:
        raise SkillMdError("missing_skill_md", "The folder has no SKILL.md file.")
    return out


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    if lines[0] != "---":
        raise SkillMdError("bad_frontmatter", "SKILL.md must start with a line of three hyphens.")
    for i in range(1, len(lines)):
        if lines[i] == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise SkillMdError("bad_frontmatter", "The frontmatter of SKILL.md has no closing line of three hyphens.")


def _scan_yaml(raw: str) -> None:
    """Refuse anchors, aliases, tags, merge keys and duplicate keys."""
    try:
        for ev in yaml.parse(raw, Loader=yaml.SafeLoader):
            if isinstance(ev, yaml.AliasEvent):
                raise SkillMdError("yaml_unsafe", "The frontmatter must not use a YAML alias.")
            if getattr(ev, "anchor", None) is not None:
                raise SkillMdError("yaml_unsafe", "The frontmatter must not use a YAML anchor.")
            if getattr(ev, "tag", None) is not None:
                raise SkillMdError("yaml_unsafe", "The frontmatter must not use a YAML tag.")
        root = yaml.compose(raw, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise SkillMdError("bad_frontmatter", "The frontmatter is not valid YAML.") from None
    stack = [root] if root is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key, value in node.value:
                if key.tag == "tag:yaml.org,2002:merge":
                    raise SkillMdError("yaml_unsafe", "The frontmatter must not use a merge key.")
                if isinstance(key, yaml.ScalarNode):
                    if key.value in seen:
                        raise SkillMdError("duplicate_key", f"The frontmatter repeats the key {key.value}.")
                    seen.add(key.value)
                stack.extend([key, value])
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)


def _frontmatter(raw: str) -> tuple[str, str, list[str]]:
    _scan_yaml(raw)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise SkillMdError("bad_frontmatter", "The frontmatter is not valid YAML.") from None
    if not isinstance(data, dict):
        raise SkillMdError("bad_frontmatter", "The frontmatter must be a map of keys.")
    extra = sorted(str(k) for k in data if k not in _ALLOWED_KEYS)
    if extra:
        raise SkillMdError("unknown_key", f"The frontmatter has an unknown key: {extra[0]}.")
    name = data.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise SkillMdError(
            "bad_name", "The name must use lowercase letters, digits and hyphens, at most 64 characters.")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillMdError("bad_description", "The description must be a non-empty text.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillMdError(
            "bad_description", f"The description holds at most {MAX_DESCRIPTION_CHARS} characters.")
    warnings: list[str] = []
    meta = data.get("metadata")
    if meta is not None:
        if (not isinstance(meta, dict) or len(meta) > MAX_META_ITEMS
                or not all(isinstance(k, str) and isinstance(v, str)
                           and len(v) <= MAX_META_VALUE_CHARS for k, v in meta.items())):
            raise SkillMdError(
                "bad_metadata",
                f"The metadata must be a map of at most {MAX_META_ITEMS} short texts.")
        warnings.append("The metadata of SKILL.md was ignored.")
    return name, description.strip(), warnings


def _action_ids(text: str) -> list[str]:
    lines = text.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _FENCE)
    except StopIteration:
        raise SkillMdError("bad_actions", "references/actions.md needs a code block of action ids.") from None
    ids: list[str] = []
    for ln in lines[start + 1:]:
        if ln.strip() == _FENCE:
            return ids
        if ln.strip():
            ids.append(ln.strip())
    raise SkillMdError("bad_actions", "The code block of references/actions.md does not close.")


def _examples(text: str) -> list[str]:
    chunks: list[list[str]] = []
    for ln in text.split("\n"):
        if _EXAMPLE_HEAD.match(ln):
            chunks.append([])
        elif chunks:
            chunks[-1].append(ln)
    return [t for t in ("\n".join(c).strip() for c in chunks) if t]


def import_skill(
    files: Mapping[str, str | bytes],
    known: frozenset[str],
    max_actions: int = MAX_PACK_TOOLS,
) -> ImportResult:
    """Read a skill folder. Raise ``SkillMdError`` on any unsafe input."""
    texts = _read_files(files)
    raw, instructions = _split_frontmatter(texts[_SKILL])
    name, description, warnings = _frontmatter(raw)
    instructions = instructions.strip()
    if not instructions:
        raise SkillMdError("no_instructions", "SKILL.md has no instructions after the frontmatter.")
    body: dict[str, Any] = {"description": description, "instructions": instructions}
    dropped: list[dict[str, str]] = []
    if _ACTIONS in texts:
        kept: list[str] = []
        for action in _action_ids(texts[_ACTIONS]):
            if action not in known:
                dropped.append({"id": action, "reason": "not_in_catalog"})
            elif action in kept:
                dropped.append({"id": action, "reason": "duplicate"})
            elif len(kept) >= max_actions:
                dropped.append({"id": action, "reason": "over_cap"})
            else:
                kept.append(action)
        if kept:
            body["actions"] = kept
    if _EXAMPLES in texts:
        examples = _examples(texts[_EXAMPLES])
        if examples:
            body["examples"] = examples
    for path in texts:
        if path not in (_SKILL, _ACTIONS, _EXAMPLES):
            warnings.append(f"The file {path} was ignored.")
    return ImportResult(name=name, body=body, dropped=dropped, warnings=warnings)
