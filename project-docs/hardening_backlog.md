# Hardening backlog — security tests and evals we do later

Owner directive, 2026-10-19: build the feature first, then harden it. This
file holds the guardrails we chose to defer. The contract is
`specs/engineering_practice.md` §7a.

**How to use this file.**

- During the build, add a line here for each guardrail you skip. Do not
  write the test yet.
- At the hardening pass, do the lines for that feature. Then delete them.
- A line names the feature, the risk, and the test or eval to write.

⚠️ This file defers work. It does not defer the SAFETY fences, the R8
database suites or the migration guards. Those block at all times.

## Across the product

| # | Item | Why it matters | Done when |
|---|---|---|---|
| X-1 | Make the gitleaks secret scan BLOCKING | It is advisory (`continue-on-error`). A leaked key merges green today | `secret-scan` has no `continue-on-error`, and one fake-token false positive (`setupTokens.test.ts`) is allowlisted |
| X-2 | A cross-org test for each new table and route | Every customer shares one database. RLS is the last line, not the only line | Each new table has a two-org R8 test: org A cannot read or write org B's rows |
| X-3 | An authorization test for each new WRITE route | The org and the member must come from the session, never from the request (R5) | Each new write route has a test with a forged org id and a member without the role |
| X-4 | Prompt-injection and tool-misuse evals for the assistant tools | Email, CRM and WhatsApp tools read text that an outside person wrote | `evals/` has a suite per channel: an injected instruction in an email or a message does not call a write tool |
| X-5 | Repair or delete the quarantined browser specs | 33 specs failed on 2026-09-22 and sit in `testIgnore` | The `ci` project in `playwright.config.ts` has no quarantine list |
| X-6 | mypy BLOCKING on new files only | 1,508 type errors, all report-only | pr-check runs mypy on the files a PR adds, and it blocks |
| X-7 | Close H-97: database passwords in agent transcripts | Four passwords reached transcripts | The four passwords are rotated, and the DSN stderr leak is fenced |
| X-8 | Review the timing tests that need `--reruns 1` | A rerun hides a flake. It does not fix it | No test needs the rerun on 20 green runs in a row (read the junit `rerun` count) |
| X-9 | Clear the process-fence warnings on `main` | They are advisory now, so they can pile up | `bash scripts/prepush.sh --harden` passes on `main` |

## Per feature

Add a section per feature as you build it. Example:

```
### Email (Gmail connect)
| # | Item | Why | Done when |
| E-1 | Two-org test for email_threads | ... | ... |
```

### Knowledge (WS-61)

| # | Item | Why | Done when |
|---|---|---|---|
| K-1 | `diff-reviewer` on the whole wave 2 diff, with KB-2h first | KB-2h landed in build mode, with the verifier only | The reviewer reports no P0 on the backlinks write, the read and `read_links` |
| K-2 | A backfill of `kb_links` for the pages written before KB-2h | Old pages gain links only on their next write | Each live page has the rows its body names, or the spec says why not |
| K-3 | Visual review of the "Linked from" list | Build mode skipped it | The list reads right in light mode, compact density, a changed accent, and at 390 px |
| K-4 | A test that `kb_index.query.query(..., title_only=True)` skips the semantic half when a caller passes a vector | Only the route's guard has a test. The second guard in `query.py` has none (KB-3d verify, 2026-10-22) | A test passes a vector with `title_only=True` and finds no semantic hit |
| K-5 | Two `runImport.test.ts` cases: a relative link to a file outside the import stays as written, and the PATCH list holds only the ids made in the run | A mutation of each survived `runImport.test.ts`. Only `roundTrip.test.ts` caught one (KB-2m verify, 2026-10-22) | Both mutations fail a `runImport.test.ts` test |
| K-6 | Refuse a zip entry whose header declares less than its real size, not truncate it | fflate cuts the entry to the declared size, so the page loses its tail in silence. Memory stays safe | The import report names the entry as refused |
| K-7 | Cap the batches that `_edit_rows` reads for one list call (about 10 batches of 500) | A member who keeps few rows makes the list read every pending row in the org, until KB-5c expires them (KB-5a review, 2026-10-22) | A test with 6000 pending rows shows the list reads at most the cap |
| K-8 | Keep any generic gateway or HTTP tool away from `/knowledge/proposals/*/accept` and `/reject` | `_require_web` treats the internal token plus a member email and no `X-Actor-Via` as a browser. No caller can reach it today (KB-5a review) | A fence test fails when a tool or agent path names the accept or reject route |
| K-9 | Let a space manager take over the draft page of an entity when the draft is idle for 14 days | A hidden draft of another member holds the one page slot of an entity. The 409 of `POST /knowledge/entities/{id}/page` tells the caller that a page exists (KC-8a review, 2026-10-25) | An R8 test shows a manager takes over an idle draft, and a member who cannot see it gets no hint |
| K-10 | Handle the `kb_entities` rows of a space before any space delete | `kb_entities.space_id` has no ON DELETE action, so a delete of a space with a product or a topic fails (KC-8a verify, 2026-10-25) | A space delete moves or removes its entity rows first, with a test |
| K-11 | Make each KC-9 caller of `resolve_person` call `ensure_entity` when `entity_id` is None | `resolve_person` returns `entity_id` None for a person with no entity row (KC-8a review, 2026-10-25) | The KC-9 fact pass never stores a fact with no entity id |
| K-12 | Put `tests/unit/test_kc_entities_r8.py` on the `--must-run` list of `pr-check.yml` | The hermetic fence checks call names only, so the owner rules rely on the R8 suite. pr-check runs the file in its shards against the ladder database, and a skip for an unset `TENANT_LADDER_DATABASE_URL` fails the guard. But the file is not on `--must-run`, so a lost file goes unseen (KC-8a review, 2026-10-25) | The `--must-run` list names the file |
| K-13 | Make the SQL fold of `kb_entities/registry.py` agree with Python `fold()` for every script | The SQL map covers Latin-1 and Latin Extended-A only. "Straße", "Nguyễn", "कृष्ण", a ligature and a no-break space do not match a search for their own text. No data leaks, but an Indian name can be missed (KC-8a re-verify, 2026-10-25) | A test searches each of those five names and finds it |
| K-14 | Read fewer rows for a directory list with no type, and say when `resolve_alias` cuts its list | A list with no type reads up to 2000 rows for each of 8 rules to return 200. `resolve_alias` stops at `DIRECTORY_CAP` aliased rows and does not report the cut (KC-8a re-verify, 2026-10-25) | A test shows the read is bounded by the page size, and an alias past the cap is found or reported |
| K-15 | Close the race of a Drive revoke and a parallel consent in another tenant | A Drive disconnect revokes after its commit. A callback of another tenant can join the same Google grant in that gap, and the revoke then kills its new token. It fails toward a dead token, which a reconnect repairs (KC-1a review, 2026-10-25) | The revoke checks the grant again after a short delay, or a lock for each Google account covers the check and the revoke, with a test |
| K-16 | Revoke the old token when a member connects Drive to another Google account | A consent to another account deletes the old tree but keeps the old account's grant at Google. That grant can be the Gmail grant of the old account, so it needs the shared-grant check first (KC-1a review, 2026-10-25) | A reconnect to another account runs the shared-grant check for the old address and revokes when it is free, with a test |
| K-17 | Keep `kb_sources.provider` equal to `integration_connections.provider` | The source row copies the provider, because no route may join the registry. Nothing checks that the two agree. Advisory: one provider exists today (KC-1a review, 2026-10-25) | A test or a constraint fails when the two differ |
| K-18 | After the deploy of migration 300, watch the log for `email.revoke_skipped` with `reason=owner_blind` | The replaced `email_gmail_grant_shared` now needs its owner to see through RLS on `email_accounts` AND `integration_connections`. A blind owner skips every Gmail revoke (KC-1a review, 2026-10-25) | One week of production logs shows no `owner_blind` line, or the operator fixes the owner of the function |
| K-19 | Revoke the Drive grants of an organization that the operator purges | The org purge deletes the Drive rows by the cascade of `organization`, and nothing revokes their Google grants. The member purge revokes them since KC-1a review P1 (KC-1a repair, 2026-10-25) | An R8 test shows an org purge revokes each free Drive grant once |

