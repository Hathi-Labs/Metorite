# C5a

Tier: HIGH

Gate: AGENT-SAFE

## What

🔨 **BUILT, dark (2026-11-13, task 1159).** Flag `STUDIO_SEARCH_ORGS`, default empty, and no migration. A lexical `studio` source in `retrieval/sources.py`. It reads only the objects that `shell.search` names, in the packages that the member owns or has installed. It reads through `query_records` with the C8 viewer, so a row rule binds. The title and the snippet come from the text fields of those objects. For a viewer of an install, they come only from `project_record` fields. The source reads each app at query time, so a record is found when its write commits. For an installed app, an object is searched only when the installed manifest declares `object:<name>.list` or `.get` on it, as the door does. **Bounds:** 5 records for each package, 10 packages (4 places kept for installs), 20 records in all, and one clock of 1.2 s that starts before the packages load. Each statement takes the time left as its store deadline, so a slow read ends in a SAVEPOINT. The search then skips it and keeps the hits so far. **Non-goals:** vectors, the command bar, caps by org, and a new manifest key. **Known limit:** C9 is not built, so a manifest that names a sensitive text field in `shell.search` puts its value in a snippet. **Re-landed (2026-11-14):** C5a merged, collided with KR-ZERO on main (two tests assumed a registry of seven sources), was reverted (PR 2319), and came back unchanged in PR 2325 with one new fence. **Before the flip of `STUDIO_SEARCH_ORGS`, check two things.** (1) With `apps=studio` named first and a studio read that is slow on single words, the second pass of a zero answer gives the whole share of each word to studio, so the other apps are `skipped` for it (a KR-ZERO property of any source named first). (2) A member who holds `feature:studio` and no other app with an open switch gets `broadened` with 0 hits under `CONTEXT_RECENCY`, as a closed CRM does today. Code: `gateway/studio/search.py`. Fences: `test_studio_search.py`, `test_studio_search_r8.py` and `test_company_search_studio_off.py` (a dark `studio` source changes no answer, flag off or on)

## Done when

A new record is found by Mira as soon as its write commits. A record the viewer cannot open is never returned. **Met by query-time reads**
