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
| K-3 | Visual review of the "Linked from" list | Build mode skipped it | The list reads right in light mode, compact density, a changed accent, and at 390 px |
| K-9 | Let a space manager take over the draft page of an entity when the draft is idle for 14 days | A hidden draft of another member holds the one page slot of an entity. The 409 of `POST /knowledge/entities/{id}/page` tells the caller that a page exists (KC-8a review, 2026-10-25) | An R8 test shows a manager takes over an idle draft, and a member who cannot see it gets no hint |
| K-10 | Handle the `kb_entities` rows of a space before any space delete | `kb_entities.space_id` has no ON DELETE action, so a delete of a space with a product or a topic fails (KC-8a verify, 2026-10-25) | A space delete moves or removes its entity rows first, with a test |
| K-11 | Make each KC-9 caller of `resolve_person` call `ensure_entity` when `entity_id` is None | `resolve_person` returns `entity_id` None for a person with no entity row (KC-8a review, 2026-10-25) | The KC-9 fact pass never stores a fact with no entity id |
| K-12 | Put `tests/unit/test_kc_entities_r8.py` on the `--must-run` list of `pr-check.yml` | The hermetic fence checks call names only, so the owner rules rely on the R8 suite. pr-check runs the file in its shards against the ladder database, and a skip for an unset `TENANT_LADDER_DATABASE_URL` fails the guard. But the file is not on `--must-run`, so a lost file goes unseen (KC-8a review, 2026-10-25) | The `--must-run` list names the file |
| K-13 | Make the SQL fold of `kb_entities/registry.py` agree with Python `fold()` for every script | The SQL map covers Latin-1 and Latin Extended-A only. "Straße", "Nguyễn", "कृष्ण", a ligature and a no-break space do not match a search for their own text. No data leaks, but an Indian name can be missed (KC-8a re-verify, 2026-10-25) | A test searches each of those five names and finds it |
| K-14 | Read fewer rows for a directory list with no type, and say when `resolve_alias` cuts its list | A list with no type reads up to 2000 rows for each of 8 rules to return 200. `resolve_alias` stops at `DIRECTORY_CAP` aliased rows and does not report the cut (KC-8a re-verify, 2026-10-25) | A test shows the read is bounded by the page size, and an alias past the cap is found or reported |
| K-15 | Write a gap when `verified` hides every Knowledge page that a plain search would find | With `verified` set and no hits, `company_search` writes no gap, so a question that only unverified pages answer shows nowhere (KB-AI-4 verify, 2026-10-25) | A test shows a verified search with no hits records the gap or a separate signal |
| K-16 | Close the race of a Drive revoke and a parallel consent in another tenant | A Drive disconnect revokes after its commit. A callback of another tenant can join the same Google grant in that gap, and the revoke then kills its new token. It fails toward a dead token, which a reconnect repairs (KC-1a review, 2026-10-25) | The revoke checks the grant again after a short delay, or a lock for each Google account covers the check and the revoke, with a test |
| K-17 | Revoke the old token when a member connects Drive to another Google account | A consent to another account deletes the old tree but keeps the old account's grant at Google. That grant can be the Gmail grant of the old account, so it needs the shared-grant check first (KC-1a review, 2026-10-25) | A reconnect to another account runs the shared-grant check for the old address and revokes when it is free, with a test |
| K-18 | Keep `kb_sources.provider` equal to `integration_connections.provider` | The source row copies the provider, because no route may join the registry. Nothing checks that the two agree. Advisory: one provider exists today (KC-1a review, 2026-10-25) | A test or a constraint fails when the two differ |
| K-19 | After the deploy of migration 300, watch the log for `email.revoke_skipped` with `reason=owner_blind` | The replaced `email_gmail_grant_shared` now needs its owner to see through RLS on `email_accounts` AND `integration_connections`. A blind owner skips every Gmail revoke (KC-1a review, 2026-10-25) | One week of production logs shows no `owner_blind` line, or the operator fixes the owner of the function |
| K-20 | Revoke the Drive grants of an organization that the operator purges | The org purge deletes the Drive rows by the cascade of `organization`, and nothing revokes their Google grants. The member purge revokes them since KC-1a review P1 (KC-1a repair, 2026-10-25) | An R8 test shows an org purge revokes each free Drive grant once |
| K-26 | Give the node rows one order when a discovery preempts a loop page | A removal-only page and a discovery clear both take the source row `FOR KEY SHARE`, so they do not wait on each other. They can then lock node rows in opposite orders. This needs a re-consent that preempts a loop page in flight, and it existed before KC-3h (KC-3h review and verify, 2026-10-25) | An R8 test runs a removal-only page and a re-consent discovery at once, and neither fails |
| K-27 | R8 tests for the member purge against a disconnect and against `close_ok` | The order before KC-3h deadlocked both pairs, and KC-3h fixes them. No test covers either pair. The pair-1 disconnect case also reads the outcome of `close()`, not the status of the run row (KC-3h review and verify, 2026-10-25) | Two R8 pair tests pass, and the pair-1 case reads the run row |
| K-29 | Verify `organization.domain` before a domain share proves a member | OQ1 reads the column as an admin typed it. Nothing checks that the org owns that domain (KC-4a, 2026-10-26) | The domain rule reads a verified domain only, and a test shows that an unverified one proves nobody |
| K-30 | A composite foreign key from `kb_node_access (organization_id, node_id)` to its node | The foreign key on `node_id` alone does not check the org, and a foreign key check does not see row level security. Today the INSERT of the pass joins the node under the org predicate, so no row names a node of another org (KC-4a, 2026-10-26) | A constraint refuses a row whose org is not the org of its node, with an R8 test |
| K-31 | Bound the access pass of a capped source | The pass lists each file that the member reads, also for a source past 50 000 nodes, and writes rows for the stored nodes only. A big drive takes many ticks for one pass (KC-4a, 2026-10-26) | A capped source lists its explored folders only, or a test bounds the calls of one full pass |
| K-32 | Put `tests/unit/test_kc_access_r8.py` on the `--must-run` list of `pr-check.yml` | The filing rule and the document clause rely on this R8 suite, as K-12 says of the entity suite. A lost file goes unseen (KC-4a, 2026-10-26) | The `--must-run` list names the file |
| K-33 | R8 tests for an access page against a re-consent discovery and against the member purge | The clear of a capped discovery now takes the source row `FOR UPDATE`, so it waits for an access page (KC-4a fix round 1). No R8 test runs the pair, because the deadlock needs a pause between the page's DELETE and INSERT. The purge pair follows the lock order, and no test covers it (KC-4a, 2026-10-26) | Two R8 pair tests run each pair at once, and neither fails with DeadlockDetected |
| K-34 | Count `kb_filings` as a document read in the one-seam fence, and fence any read of `kb_source_nodes.name` | The fence counts a document read only for `kb_node_access` and `kb_documents`. KC-6 adds `kb_filings`, and a read of a node name outside `kb_sync/` and `routes/knowledge/sources.py` shows a document title with no clause (KC-4a fix round 1, 2026-10-26) | `test_kb_visibility_one_seam.py` fails a read of either one with no `visible_documents_clause` |
| K-35 | Read `incompleteSearch` in `list_access` | With `corpora=allDrives`, Google can leave items out of a page. The sweep then deletes their readers. This fails closed, but real readers lose documents (KC-4a fix round 1, 2026-10-26) | A hermetic test with `incompleteSearch` true keeps the old rows of the missing items, or ends the pass as not complete |
| K-36 | Count the calls of a failed read after a 401 | After a 401 the budget counts one extra call, and not the calls of the read that failed. A pass can then pass its cap of calls by a few (KC-4a fix round 1, 2026-10-26) | A hermetic test shows `ACCESS_CALLS_PER_TICK` holds after a 401 in the middle of a page |
| K-37 | Reset `explored` when a gone folder comes back in an uncapped source | K-23 stops a gone folder from keeping a source capped. A folder that went gone with `explored = false` and comes back through the change feed keeps that flag (the change upsert keeps `explored`). It then makes the source capped again (KC-3i, 2026-10-26) | An R8 test revives such a folder and shows the next reconcile takes the path that the live folders decide |
| K-38 | Bound the `source_sync_failed` card by recency after a member fixes a source | The card reads the three newest finished runs. After a fix, it stays until one new run ends ok. That takes about one loop interval, 15 minutes (KB-9b slice 2, 2026-10-26) | The owner accepts the delay, or a test shows the card gone at the first ok run |
| K-39 | Bump both owners when a re-consent by another member moves `owner_email` | Neither the old nor the new owner gets a bump. The cached feed of the old owner can show the card for up to 600 seconds. The read filters by the current owner, so no card leaks (KB-9b slice 2, 2026-10-26) | The re-consent path calls `bump_member_safe` for both owners, with a test |
| K-45 | Check the kind of each `kb_links` row in `read_links` | `read_links` (`core.py`) returns links of kinds other than `page` with no visibility check. Nothing writes those kinds today, so it is a latent leak (K-1 review, 2026-10-26) | `read_links` checks each kind, or refuses a kind with no checker, before any writer of another kind ships. A test shows it |
| K-46 | Tell the caller when `MAX_BACKLINKS` cuts the "Linked from" list | The route lists at most 200 sources and sets no `truncated` flag (K-1 review, 2026-10-26) | The answer carries `truncated`, and a test with 201 sources shows it true |
| K-47 | `diff-reviewer` on the rest of wave 2: KB-2b, KB-2e, KB-2k and KB-2m | K-1 reviewed KB-2h only. The other four rows have no adversarial review (K-1 review, 2026-10-26) | The reviewer reports no P0 on each of the four |

