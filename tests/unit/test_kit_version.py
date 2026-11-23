"""A change of the shipped kit raises ``kit_version`` (KIT-5, orchestration_playbook.md §6a).

The digest covers every file that a zip holds: the core, the skill folder and
the pack files of each client. A person who changes one must raise the
``kit_version`` line in the core and add the new digest to ``PINNED``.

Mutations (R7):

* V1. Change a word of ``kit/skill/planning.md`` and keep the version:
  ``test_the_shipped_kit_matches_its_pinned_version`` fails.
* V2. Raise the version and add no digest: the same test fails.
* V3. A pack file keeps an old ``kit_version`` line:
  ``test_every_pack_file_holds_the_core_version`` fails.
"""

from __future__ import annotations

import hashlib
import re

from acb_playbook import CLIENTS, kit_version
from acb_playbook.kit_zip import build_files

#: ``{kit_version: digest}``. Add a row when you raise the version. Never edit a row.
PINNED = {
    2: "c1d5fc59faab445a8d2d8c22c9c346f465f47d0a64911153fc8fed6d387fdb4b",
}

_SKIP = {"KIT_VERSION", "METORITE_KIT.md", "SHA256SUMS"}


def digest() -> str:
    h = hashlib.sha256()
    for client in CLIENTS:
        files = build_files(client, "https://mcp.example.test/mcp")
        for path in sorted(files):
            if path not in _SKIP:
                h.update(f"{client}\0{path}\0".encode() + files[path] + b"\0")
    return h.hexdigest()


def test_the_shipped_kit_matches_its_pinned_version():
    version = kit_version()
    assert version in PINNED, f"kit_version {version} has no digest in PINNED. Add {digest()!r}."
    assert PINNED[version] == digest(), (
        "A kit file changed and kit_version stayed. Raise the kit_version line of "
        f"kit/agents-md.md, and add the version with digest {digest()!r} to PINNED.")


def test_every_pack_file_holds_the_core_version():
    from acb_playbook.kit_zip import PACKS_DIR
    line = re.compile(r"^kit_version: (\d+)$", re.M)
    for path in sorted(PACKS_DIR.rglob("*")):
        if path.is_file() and path.suffix != ".pyc":
            found = line.findall(path.read_text(encoding="utf-8"))
            assert all(int(v) == kit_version() for v in found), path.name
