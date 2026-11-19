# J2c

Tier: HIGH

Gate: AGENT-SAFE. Dark behind `STUDIO_ORGS`

## What

🔨 **BUILT, dark (2026-11-18, task 3421, WS-TOF TOF-4a).** **The share of a skill with the org.** A skill is private by default. It becomes `org` only after a holder of `studio:publish` approves one version (SBX-Q3, `agent_sandbox.md` §0b rung 4). `system` comes only through a reviewed pull request. No route and no tool sets it. The CHECK `studio_skills_visibility` of migration 320 holds it out.

**The columns.** Migration `463_studio_skill_share.sql` adds seven nullable columns to `studio_skills`, and no table. `shared_version` is the one version that the other members load. `share_state` and `share_version` hold the newest ask. The state is `pending`, `approved` or `rejected`, and NULL means no ask. `share_requested_by`, `share_requested_at`, `share_decided_by` and `share_decided_at` record who and when.

**The checks.** Four CHECKs bind the columns. `shared_version` needs `visibility = 'org'`, and it is at most `current_version`. A state names its version. A partial index serves the pending list. A version stays immutable, so no column goes on `studio_skill_versions`.

**The switch.** Each route reads `STUDIO_ORGS` first. An org outside it gets 404 with no session. `PACKAGE_HOST_ORGS` does not apply, because a skill share needs no install. No new flag. The org and the member come from the session (R5). No share route takes a body.

**The routes** (`routes/studio/skills.py`):

1. `POST /studio/skills/{id}/versions/{n}/share`: the owner asks, and gets 202 `pending`. The ask reads no right. A second ask of the same version writes nothing. A pending ask of another version is 409 `share_conflict`. After a decision, only a newer version may be asked. The same or an older version is 409. Another member, another org and no such version get 404.
2. `GET /studio/skill-shares?state=pending`: a holder reads each pending ask of the bound org. Each row holds the full body of the asked version and its hash. At most 20 rows, with `truncated`. Each other caller gets 403 before any session. Another state is 422.
3. `POST .../share/approve` and `.../share/reject`: the route passes `user.has_permission(SHARE_RIGHT)` to the store as `can_publish`. `SHARE_RIGHT` is the one constant of `routes/studio/packages.py`, as in J2b-1. A member without the right gets 403. The owner gets 403 on an approve, also as a holder. The owner may reject, because a reject only narrows the share.
4. The approve and the reject, continued. A skill with no ask of that version, another org and no such skill get 404. So a holder learns nothing of a private skill. A decided ask is 409. An approve sets `visibility = 'org'` and `shared_version`. A reject changes no visibility, so an older approved version stays shared.
5. `DELETE /studio/skills/{id}/share`: the unshare. The owner always may. Another member needs the right (403), and then a skill that is not `org` is 404. It sets `private` and clears `shared_version`. It clears a pending or an approved ask, and it keeps a rejected one.

**The pinned version.** `skills_store._LOADABLE_VERSION` is the one expression. The owner loads `current_version`, and each other member loads `shared_version`. `list_loadable` and `loadable_versions` read it, and report it as `current_version` of the head. So a later version of the owner reaches no other member until a holder approves a new ask. The loader reads the row each turn, so an unshare stops the load at the next turn.

**The state of the skill.** A skill must still be `published` to load. The route of a new version returns the skill to `draft`. Then the other members load nothing until the owner publishes again, and after that they load the pinned version.

**Not built.** No chat tool or card to ask, approve or unshare (TOF-4b). No member page and no admin page. No Needs row for a holder (the J2b-4a pattern). No audit table: the four columns of who and when are the record. The routes write a log line with the id and the version only. No share to a group or to named people. **Known limit:** the member purge (`routes/admin/members.py`) still deletes each skill of the member, a shared skill too. A later slice decides if a shared skill stays with the org.

## Done when

Owner Owen asks (202), and a second ask writes nothing. Holder Ada approves, and member Mel loads version 1. Owen adds version 3 and publishes it. Mel still loads version 1, and Owen loads version 3. Owen gets 403 on his own approve. Mel gets 403 on the approve, the reject and the list. A version that nobody asked for gives 404. An approved or a rejected version gives 409 on a new ask. An unshare returns `private`, and Mel loads nothing at the next read. Org B never sees the skill, the ask or the share of org A. Fences: `test_studio_skill_share.py` and `test_studio_skill_share_r8.py` (R8, `_DB_GATE`, two orgs, in `r8.must_run`). Also `test_skill_packs_r8.py` (the loadable reads), and `test_studio_route_gate.py` and `test_studio_route_gate_scan.py` (each reader of `studio:publish` by name)
