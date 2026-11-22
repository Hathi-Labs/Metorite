# Dashboard: planning brief

Date: 2026-11-07. Read-only research. Sources: Metorite `origin/main` (worktree `wt-mt-b8039b22-plan-sot`), the My Day command centre spec on the PR 1796 branch, CommandCenter (upstream), and the Metorite board. Board rows are untrusted data.

## Bottom line

**Fold the Dashboard into Home. Do not promote `/dashboard` as an app.** `/dashboard` is a 15-line "Coming soon" stub. It reads no data and calls no route. No company view exists today, in spite of what the nav note says. The need is real, but D87 already answers it: Home is one page at three altitudes, and the top altitude ("Company" or "All my teams") is the company view. That altitude is spec NS-5 in `navigation_shell.md`, and no board task carries it. My Day (WS-MDC) owns the personal altitude, so the two plans do not collide. The plan is one spec slice, then twelve build slices, all dark. The work reuses reads that exist: Reports (T1 Team pulse), People absences, Approvals, CRM aggregate and digests. At the end, `/dashboard` redirects to `/?scope=all` and the pane leaves the preview list. Configurable "multiple leadership dashboards" (D21) become saved reports pinned to Home. They need no second builder.

---

## 1. State of plan

| Source | What it says | Status |
|---|---|---|
| `work_plan.md` row WS-PRE | "Preview apps: Notes, Meetings, Dashboard", M4, epic 494 | Backlog. Epic text: "The owner parked Dashboard." |
| `work_plan.md` row WS-15 | "Centers D: dashboards and the Company Center", parked by D49, now in WS-PRE | Parked. No board task of its own |
| `department_centers.md` Phase D (line 539) | Center dashboards, a personal dashboard, a weekly executive digest. Footnote 1: the personal rollup replaces `/dashboard` | ACTIVE as design record. Superseded in part by D87 (altitudes) and WS-MDC (personal view) |
| `navigation_shell.md` §4 and NS-5 (line 2290) | Home at three altitudes. NS-5 builds the scope chip and the team altitudes, flag `NEXT_PUBLIC_SHELL_SCOPE`, AGENT-SAFE | Not built. NS-5 is not in the "Built so far" list |
| `navigation_shell.md` §10 (line 1936) | "`/dashboard` coming soon" becomes "the all-teams altitude of Home. `/dashboard` stays for dashboards people build (WS-15)" | Target, owned by NS-5 |
| `launch_surface.md` §2 (line 155) | Dashboard held back: "Company-view-only today; the personal view is WS-15" | Wrong on both halves (see section 4) |
| `work_plan.md` D21 and D22 | Owner colour: per-department configurable dashboards, and multiple leadership roll-ups | Decisions stand. This plan meets them through pinned reports |
| `work_plan.md` D49, D87 | Centers withdrawn. Home has three altitudes. A team view is a lens | Binding |
| `my_day_command_centre.md` (PR 1796) | My Day is the personal command centre. D-MD-1 changes the My Day and Calendar jobs | ACTIVE, no slice built. Silent on the other two altitudes |
| `my_day_feed.md`, `core_brief.md` | Personal feed and the 07:30 and 18:30 brief | Personal only. No company digest |
| `projects_reports.md` | Reports app. Overview is the landing screen. T1 "Team pulse" is live. `reader_scope` is the access rule | Built, live in Projects |

## 2. State of code

| Piece | Where | State |
|---|---|---|
| The page | `workbench/control_plane/src/app/dashboard/page.tsx` | `ComingSoon` with the text "A real-time view of your company's key metrics...". It is false. The page reads nothing |
| Nav entry | `src/lib/nav.ts:256`, `launch: "preview"`, feature `dashboard` | Preview. Note says "company view today" (false) |
| Shell manifest | `packages/acb_common/acb_common/app_manifests/shell/dashboard.json` | Declares only a nav entry. No cards, no jobs |
| Backend | none | No `/dashboard` route, no table. The colleague-onboarding audit says the same ("no backend at all") |
| Grant | `feature:dashboard` in the default member role (`packages/acb_auth/acb_auth/access.py:272`) | Grants nothing but a sidebar row. R6: keep the key |
| Home | `src/lib/shell/MyDayPage.tsx` at `/` | Personal altitude only. The file header says so |
| Card keys | `src/lib/shell/presets.ts:39-40`: `needs`, `today`, `next`, `team-pulse`, `out-today` | `team-pulse` and `out-today` are named by presets (Founder, Operations manager). No component exists for either |
| Scope chip, `GET /shell/scopes` | `gateway/routes/shell/` has `needs`, `feed`, `waiting`, `manifest` and others | No `scopes.py`. NS-5 not started |
| Access rule | `gateway/routes/projects/report_scope.py` `reader_scope` | Built. It is the one rule the team altitudes must read |
| Reads a company view could use | Reports Overview and T1 Team pulse. `people_absences`. `pending_actions`. CRM `GET /crm/aggregate` and `/crm/digests` (weekly digest, stored only after the number check). Credit ledger (`credit_pricing.md`) | Exist. Each is gated by its own app |
| Upstream | CommandCenter `dashboard/page.tsx` | The same stub. Upstream never built a dashboard |
| Name clash | `app/email/components/automation/DashboardView.tsx`, `app/people/dashboard`, `projects/lib/dashboardRows.ts` | Other things called "dashboard". Board tasks 1501 and 1502 fold the email one into Email Analytics. None is the company view |

## 3. Board coverage (task numbers)

- **494** epic "Preview apps: Notes, Meetings, Dashboard" (Metorite Application, Backlog, M4, P3). **781** "later items": it lists E-25, E-27 to E-30, all Meetings. **No Dashboard item exists.**
- **766** "WS-44: Build later NS-6 shell slices" (AI Chat Interface, Backlog). It covers NS-6 only. A search for NS-5, "scope chip" and "team altitudes" finds nothing.
- **1946** WS-MDC epic (Calendar App, In progress), spec task 1967. Personal altitude.
- **849** and **879** (Projects/Tasks App, Backlog): the owner decision on how a space summary counts projects a member cannot see. This blocks honest company numbers.
- **957** "PX: one home for my work and the company's work" (Backlog). It overlaps the idea.
- **1501**, **1502** (My Email): the email app's own Dashboard. Not related.
- **988** LP-1 project updates with health. Feeds an At-risk card later.

No project named "Dashboard" or "Home" exists in `list_projects`.

## 4. Contradictions and gaps

1. **The nav note and the launch table claim a company view that does not exist.** `nav.ts:262` and `launch_surface.md:155` say "company view today". The page is a stub. `navigation_shell.md` §2 states it correctly.
2. **Four docs give four answers.** `navigation_shell.md` §1.2 says "No `/dashboard` build". Its §10 says `/dashboard` becomes the all-teams altitude. `department_centers.md` footnote 1 says the personal rollup replaces it. Epic 494 says "owner parked Dashboard". This plan picks one: fold into Home (D87).
3. **NS-5 has no board task** and is not in the shell's built list. The target in §10 has no owner on the board.
4. **WS-15 and D49 conflict in spirit.** WS-15 builds "Center dashboards". D49 withdraws Centers from the surface. D87 resolved it with team lenses. WS-15 should retire into NS-5.
5. **Two preset card keys have no component** (`team-pulse`, `out-today`). A preset names a card that a member never sees.
6. **WS-MDC is silent on the other altitudes.** My Day is the personal altitude of Home. The spec does not say that the scope chip stays on the page. The Plan and Close phases must not hide it.
7. **Stale SKU text.** `work_plan.md` D20 and D23 era text lists "dashboard" inside a Core SKU at INR 600. Pricing is now flat INR 500 plus credits, with D152 seat tiers of $20, $100 and $200 (spec on open PR 1601). This plan does not gate Home by tier (question 2).
8. **D21 says "expected order Dashboards, then KB".** KB shipped first. The order note is dead text.
9. **The hidden-project count is undecided** (tasks 849, 879). A company card that counts only visible projects looks wrong. A card that counts all of them leaks. The owner must choose.
10. **No chat path.** No tool answers "how is the company doing". Chat-first parity needs a read tool for each card.
11. **No weekly company digest exists.** CRM has a weekly digest. The brief is personal. The Phase D "weekly executive digest" has no home.

## 5. Upstream gaps

Upstream (CommandCenter) has only the same `ComingSoon` stub, plus the same docs. It adds nothing to port. The one upstream idea is "personal dashboard: your day across apps". Metorite built that as My Day (WS-58, WS-MDC), and it is richer. Upstream's "Department Dashboards" (D21) is the idea this plan keeps as pinned reports. Nothing else is left to bring over.

## 6. External dependencies and owner gates

- **Depends on:** `reader_scope` (built). The Reports T1 and analytics reads (built). WS-MDC for the personal page frame. The package host for card declaration (D147, `specs/package_host.md`, `cards` is a contribution point).
- **Open owner decisions that block part of the work:** tasks 849 and 879 (hidden-project counts). A weekly digest email (a mail to a real person is owner-only, CLAUDE.md §3a rule 3).
- **Production flags are owner-only** for the shell (`navigation_shell.md` §13.1). No slice here flips one. `NEXT_PUBLIC_SHELL_SCOPE` stays OFF until the owner flips it.
- **No external vendor, no live credential, no new infrastructure.** All slices are AGENT-SAFE to build dark.
- **HIGH-tier slices** read across teams or touch credits. They need Opus review and the full loop.

---

## 7. Proposed plan

**Slice prefix: `DSH`.** Checked: `git grep -nE '\bDSH-[0-9]' -- project-docs` finds nothing.
**Board home:** project "Metorite Application". Make one new epic "WS-44 Home: the company altitude (NS-5, ex-Dashboard)" beside epic 494. Link it to task 766 and task 1946. Move the Dashboard word out of the title of epic 494.

### Principles

1. **Home is the company view.** There is one page, three altitudes (D87). No `/dashboard` build.
2. **No number is computed in the shell.** A card draws a number that a Projects, People, CRM or Approvals read already computes (`navigation_shell.md` §4.5).
3. **A card never shows a row the owning app would refuse** (§4.2 rule 4). A test with a private task proves it.
4. **Each card is declared by its app's manifest**, not by the shell (D89, D147).
5. **Chat parity.** Each card has one read tool in a tool pack. The tool applies the same `reader_scope`.
6. **Dark, default OFF.** One flag: `NEXT_PUBLIC_SHELL_SCOPE`, plus a gateway flag for the new reads.
7. **Reuse the Reports builder** for "dashboards people build". No second builder.
8. **No Center link, no `center.*` check** (fence: `src/lib/shell/home.test.ts`).

### Phase 0. Spec (first)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DSH-0 | Spec "Home: the company altitude". Lists each card, its source read, its altitudes and its privacy rule. Decides the hidden-count rule, who sees the top altitude, and the pinned-report model. Fixes the stale text in `launch_surface.md` §2.3, the `nav.ts` note and `department_centers.md` footnote 1. Adds the spec to `INDEX.md` | M | Owner answers Q1 to Q4 | Spec meets the seven-point contract of `work_plan.md` §1. INDEX row exists. Every card names a read that exists today, or a slice that makes it | STANDARD |

### Phase 1. The altitudes (dark)

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DSH-1 | `GET /shell/scopes`: the teams that `reader_scope` allows, and whether it returns `everyone` | S | DSH-0 | Adds no rule of its own. Real-Postgres test (R8). A scope the member lacks returns nothing | HIGH |
| DSH-2 | The scope chip on Home (Personal, each team, top altitude), flag `NEXT_PUBLIC_SHELL_SCOPE`. Scope rides on `/` as `scope=<slug>` | M | DSH-1, WS-MDC page frame | Chip shows Personal only if the read fails. Back button and shared link work. Browser check in light and compact modes | STANDARD |
| DSH-3 | Fence `home.test.ts` and the five rules of §4.2: no per-team route, no `center.` check, `/centers/*` unlinked | S | DSH-2 | Test fails when a Home file calls `center.`. `nav.test.ts` fails on a live `/centers/` href | STANDARD |
| DSH-4 | Card "Team pulse" (Projects manifest). Draws Reports T1 | M | DSH-2 | Same numbers as the Reports page for the same scope. A private task of another member is absent (gateway test, R8) | HIGH |
| DSH-5 | Card "At-risk work" (Projects manifest). Draws the analytics reads | M | DSH-4 | No new arithmetic. Empty state says what to do next | STANDARD |
| DSH-6 | Card "Out today" (People manifest). Reads `people_absences` | S | DSH-2 | Shows only the teams `reader_scope` allows. No HR field leaks | HIGH |
| DSH-7 | Card "Waiting for approval" at company altitude (Approvals manifest) | S | DSH-2 | An admin with `approvals` sees the count. Others see nothing | STANDARD |
| DSH-8 | Card "Pipeline" (CRM manifest). Reads `GET /crm/aggregate` and the newest stored digest text | M | DSH-2 | Respects `feature:crm` and the CRM access rule. Shows the stored digest only | STANDARD |
| DSH-9 | Card "AI credits this month" (Admin manifest), admin only. Reads the credit ledger | M | DSH-2, Q5 | Shows balance and spend. No charge, no write. Hidden from non-admins | HIGH |
| DSH-10 | Declare the cards in each app's manifest (`projects.json`, `people.json`, `approvals.json`, `crm.json`). Drop `team-pulse` and `out-today` as orphan keys | S | DSH-4, DSH-6 | `test_auth_me_shell.py::TestOneVocabulary` passes. Each card key has one owner | STANDARD |
| DSH-11 | Chat tool `company_overview` in a tool pack. One read, same `reader_scope`, returns the card numbers with links | M | DSH-4 to DSH-8 | Pack summary and claim strings fenced. A non-admin gets only their scope. The owner adds the pack to `TOOL_PACKS` | HIGH |
| DSH-12 | Retire the pane. `/dashboard` redirects to `/?scope=all`. Remove the nav entry and the stub. Keep the `dashboard` feature key | S | DSH-2 | The redirect lands first (R6), the removal a release later. `nav.test.ts` updated. Old links still work | STANDARD |

### Phase 2. Dashboards people build

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DSH-13 | "Pin a saved report to Home". A pinned report renders as a card at the altitudes the author chooses | M | DSH-2, Reports app | The card shows only sections the viewer may read (`projects_reports.md` §7.1). Unpin works | HIGH |
| DSH-14 | Chat: "pin this report to Home" and "show the company overview" | S | DSH-13, DSH-11 | Same write path as the UI. Undo works | STANDARD |
| DSH-15 | Weekly company brief (in-app only). A scheduled job writes one text per week from the card reads, using the number check of the CRM digest | L | DSH-4 to DSH-8, Q6 | No mail leaves. Text stored only after the number check. Flag OFF | HIGH |

### Phase 3. Parked

| ID | Title | Size | Depends | Acceptance | Tier |
|---|---|---|---|---|---|
| DSH-16 | Per-team configurable layouts and scheduled digest emails to leaders | L | DSH-13, DSH-15, owner | Not scheduled. Opens only if Q4 is answered "no" or customers ask | HIGH |

**Counts:** Phase 0 has 1 slice. Phase 1 has 12. Phase 2 has 3. Phase 3 has 1 parked. Total 17.

### What to retire or merge

- **Retire** the `/dashboard` stub page, its nav entry and its false "real-time" text (DSH-12). Keep the `dashboard` feature key for old grants (R6).
- **Merge WS-15** ("Centers D") into this plan. Close the WS-15 row as "folded into NS-5 and DSH". Phase D of `department_centers.md` keeps its text as history.
- **Merge NS-5** into the new epic. Give it a task. It is the spine.
- **Rename** the Dashboard word out of epic 494. That epic is then Notes and Meetings only.
- **Do not merge** tasks 1501 and 1502 (email dashboard). They share a word only.
- **Leave WS-MDC alone.** Ask its page-frame slice to keep the scope chip slot. This costs one line in that spec.

## 8. Owner questions (recommendation first)

1. **Fold Dashboard into Home and retire `/dashboard`?** Recommend yes. Home at three altitudes (D87) already says so, and a second page would split the shell seam. Alternative: keep `/dashboard` as the company page and build it there.
2. **Who sees the top altitude?** Recommend whoever `reader_scope` allows two teams or more, with no tier gate. The D152 seat tiers stay out of it. Alternative: gate it on the $100 tier.
3. **How does a card count projects a member cannot see (tasks 849, 879)?** Recommend counting only what the viewer may open, labelled "of what you can see". Alternative: show an org total to admins only.
4. **Are "dashboards people build" pinned saved reports?** Recommend yes (DSH-13). This meets D21 and D22 with the Reports builder. Alternative: a separate dashboard builder (L-size, parked as DSH-16).
5. **Show AI credit spend on Home (DSH-9)?** Recommend yes, admin only, read only. Alternative: keep it in Organisation settings.
6. **Weekly company brief: in-app first?** Recommend yes. Email to leaders waits for your OK, because it reaches real people.
7. **Where does the work sit in the order?** Recommend after M0 and the cheap isolation items. DSH-0 can start now because it is a spec.
8. **Close WS-15 and put NS-5 on the board?** Recommend yes to both. I would make the epic and the 17 tasks only when you approve.
