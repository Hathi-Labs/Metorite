# Third-party notices

Code that Metorite took from other projects, and the licence of each.

## Docmost (AGPL-3.0)

- **Source.** https://github.com/docmost/docmost, core only.
- **Licence.** GNU Affero General Public License, version 3.
- **Decision.** `project-docs/work_plan.md` D107, `project-docs/specs/knowledge_base.md` §21.1.
- **Never ported.** Files under `apps/server/src/ee`, `apps/client/src/ee` and
  `packages/ee`. Docmost licenses those folders separately.
- **Marker.** Each ported file carries the line `Adapted from Docmost` in its
  first lines, with the path in the Docmost repo.
- **Fence.** `tests/unit/test_third_party_notices.py` fails when a file with the
  marker is missing from the list below.

### Ported files

Each line is `path in this repo` and `path in Docmost`.

<!-- PORTED-FILES-START -->
<!-- PORTED-FILES-END -->
