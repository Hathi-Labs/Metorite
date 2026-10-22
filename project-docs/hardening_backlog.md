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
