# Onboarding — the first hour of a founder and of a member

**Status: ACTIVE, 2026-11-02.** Verified against code on 2026-11-02, at
`origin/main` `a5cbbdee2`. Board row: **WS-OB** (`work_plan.md` §2). Metorite:
the Onboarding project, epic 669. OB-0 is built on a branch. The owner accepted
§9 on 2026-11-02, recorded as **D150**.

**Source.** Owner report, 2026-11-01. The owner said that the first-run
onboarding fails from the start. The founder's welcome dialog did not
close, and the founder landed on "Nothing is enabled for your account yet". The
owner asked for a flow from first principles.

---

## 1. Scope and non-goals

**In scope.**

- What a founder sees from the end of `/signup` to the first useful act.
- What an invited member sees from the invite email to the first useful act.
- The Get started card on My Day, the first-week set of the setup coach.
- The empty state of each live app, and the Help entry that opens the card again.

**Not in scope.**

- The signup form itself (`specs/customer_console.md` CP-2c).
- Who may invite whom, and the grant model (`specs/colleague_onboarding.md`,
  `user_management_contract.md`).
- Setup health per app over time (the SH tasks, epic 940). This spec builds the
  first items of the same `setup_status` seam, and SH extends it.
- Billing, seats and plans (`specs/launch_surface.md` §4, WS-PLN).
- A product tour with coach marks. §3 P2 says why.

## 2. What is wrong today

Each row was verified in the code on 2026-11-02.

| # | Defect | Evidence |
|---|---|---|
| 1 | The founder's welcome did not close on a slow server | `WelcomeDialog.tsx` called `router.replace`, which waits on a server round trip. Fixed by OB-0 |
| 2 | A new founder lands with no apps for up to 2 minutes | `acb_auth/access.py:1195-1220` caches the refusal that signup's own auth check made. `signup.py` never calls `invalidate_access`. OB-0b |
| 3 | A founder meets three dialogs in a row before the product | Question, "Choose the apps for your sidebar", then "Your organization is ready" (`WelcomeDialog.tsx`) |
| 4 | The invite step only says where to invite | It names "Settings → Organization" and links there. It does not invite |
| 5 | Nothing shows progress, and nothing comes back | No checklist component exists. My Day has no setup card |
| 6 | The invite dialog says "does not send an email" | `OrganizationAdmin.tsx:1369-1371`. Invite email is ON in production |
| 7 | The invite email opens a bare `/signin` | `src/lib/inviteEmail.ts:110` |
| 8 | An empty app does not say what to do first | Each live app draws its own empty text |

## 3. Principles

These are the rules of the design. Each slice in §8 obeys them.

- **P1. Value first.** The first screen does a useful thing. It is never a dead
  end such as "Nothing is enabled".
- **P2. One interruption at most.** A dialog asks only a question whose answer
  changes what the member sees. All other guidance stays in the page and never
  blocks. A tour of coach marks teaches a layout that the member has not yet
  needed, so this spec has none.
- **P3. A way out works at once and stays.** Every overlay closes in the same
  frame, with no server round trip. The server holds the dismissal, so a reload
  or a second device does not bring it back.
- **P4. Do the step, do not describe it.** A step does its act in place. "Invite
  your team" takes the addresses on the card. It does not send the member to
  another page to find a form.
- **P5. Progress comes from real state.** A step is done when the real row
  exists, such as a connected mailbox. A click on the step never ticks it.
- **P6. The member stays in control.** A step can be skipped. The whole card can
  be hidden, and Help → Get started opens it again.
- **P7. Two people, two paths.** A founder sets up the company. A member sets up
  only their own work. Each sees only the steps that they can do.
- **P8. Teach in place.** An empty app says what it is for, and gives one first
  act and one "Ask the assistant how".
- **P9. The chat can do it too.** Each step works from the chat (owner,
  2026-11-01: full parity). The chat fills the form, and the person presses the
  button (D89 jobs).

## 4. The founder's path

| Step | What the founder sees | State it leaves |
|---|---|---|
| F0 | `/signup`: the account, then the organization form. Unchanged | The org, its owner row and `?welcome=new-org` |
| F1 | My Day, with every app the owner holds. Never the "Nothing is enabled" card (OB-0b) | Access resolves fresh |
| F2 | ONE dialog: "Welcome to {Org}", and the question "What will you do most here?" (NS-7, `navigation_shell.md` §8.4) | `shell_prefs.answered` and the preset |
| F3 | My Day, with the Get started card at the top | Nothing until the founder acts |
| F4 | Each step ticks when its real row exists. The last one shows "You're set up", once | `setup` prefs (§7.2) |

The founder's default steps, in this order:

| Id | Step | Its act | Done when |
|---|---|---|---|
| `invite_team` | Invite your team | Addresses on the card, then Send (OB-1c) | The org has a member other than the founder, invited or active |
| `connect_email` | Connect your email | Opens the email connect flow (`email/components/ConnectChoices.tsx`) | The member has one row or more in `email_accounts` |
| `first_project` | Start your first project | Opens the new project form. A second link opens the file import (`specs/project_import.md`) | The org has one `pm_projects` row or more with `personal_owner` null |
| `ask_assistant` | Ask the assistant something | Opens the assistant with three example asks | `user_settings.shell_prefs.chat_first_message_at` is set |

Under "More steps", for an admin:

| Id | Step | Done when |
|---|---|---|
| `org_profile` | Add your logo and company details | The org settings row has a logo (`234_org_settings_per_org.sql`) |
| `phone_push` | Get notifications on your phone | The member has a `push_subscriptions` row |
| `know_me` | Tell the assistant how you work (PZ-27) | `assistant_profile.interview.started_at` is set |
| `connect_calendar` | Connect your calendar | Waits on CE-10 (`calendar_events_reminders.md`). Hidden until then |

## 5. The invited member's path

| Step | What the member sees | State it leaves |
|---|---|---|
| M0 | The invite email: "{Org} added you on Metorite", and one button, "Join {Org}". The link fills the address on the sign-in form (OB-0c) | None |
| M1 | First sign-in. The invite turns active (`routes/signin.py:145`, D50.3) | `app_user.status` is `active` |
| M2 | ONE dialog: "Welcome to {Org}", and the same question as F2 | `shell_prefs.answered` |
| M3 | My Day, with the Get started card | Nothing until the member acts |

The member's default steps: `connect_email`, `ask_assistant`, `know_me` and
`phone_push`. A member never sees `invite_team`, `first_project` or
`org_profile`, unless their role can do that act.

## 6. The Get started card

**Where.** The top of My Day, above the feed. It is the "one card that lists
the open setup items" of `personalization.md` §12.5. It is not a second card.

**What it shows.**

- A header: "Get started", the count ("2 of 4"), and a menu with "Hide this list".
- At most four open steps. "More steps" opens the others.
- Each step: an icon, the title, one line on why, and one button for its act.
- A done step shows a tick and moves to the foot of the list, muted.
- Each open step has "Skip" in its row menu.

**When it shows.** It shows while one step or more is open and the member has
not hidden it. When the last step is done, it says "You're set up" once, and
then it goes away for good.

**Hide.** "Hide this list" hides it at once (P3). One toast says that Help →
Get started opens it again (OB-3).

**The coach's limits.** D110 (D-PZ-13) limits the questions that the coach
ASKS: one a day, and 14 days after a dismiss. The card asks nothing, so those
limits do not bind it. A step that the chat offers as a question obeys them.

## 7. The server

### 7.1 One function

`setup_status(org, member)` is the one function of `personalization.md`
§12.5. OB-1a builds it with the steps of §4 and §5. PZ-16 adds the profile
items to the same registry later. A second "onboarding status" function is a
defect.

- Each step is one registry row: its id, its audience (role and feature), its
  app, and a "done when" query.
- Each query reads under the member's own session, through the `get_db()`
  seam, so RLS binds it (R5).
- The answer per step is `done`, `open` or `skipped`, with `done_at` from the
  real row where the row has a timestamp.
- The gateway serves it at `GET /shell/setup`. The chat reads the same
  function as a tool (OB-5).

### 7.2 The member's choices

The card's state is one new key, `setup`, in `user_settings.my_day_prefs`.
It goes through the existing `PUT /shell/feed/prefs` route, which writes one
whitelisted key at a time (`routes/shell/prefs.py:65`, `PREF_KEYS`).

```json
{ "hidden_at": "2026-11-02T10:00:00Z", "done_seen_at": null,
  "skipped": { "first_project": "2026-11-02T10:05:00Z" } }
```

No new table and no migration. Tenancy is the row of `user_settings`, which is
already per member and per org (`239_user_settings_per_org.sql`).

## 8. The slices

| Slice | What | Done when | Gate · tier |
|---|---|---|---|
| **OB-0** | The founder's welcome closes at once (§2 row 1). Task 1337 | `e2e/shell-presets.spec.ts`, "a slow server never holds the founder's welcome open": each way out closes with the RSC request held. Back after "Invite my team" does not bring it back | AGENT-SAFE · LOW. BUILT 2026-11-02 on branch `ob-onboarding` |
| **OB-0b** | Signup invalidates the access cache of the new owner (§2 row 2). Task 1339 | A route test with the real access resolve reads `/auth/me` within the TTL and gets the owner's features. It fails with the call removed | AGENT-SAFE · HIGH (auth) |
| **OB-0c** | The invite copy tells the truth (§2 rows 6 and 7) | The dialog names the email. The email's button opens `/signin` with the address filled. `inviteEmail.test.ts` holds it | AGENT-SAFE · LOW |
| **OB-1a** | `setup_status` and `GET /shell/setup`, with the steps of §4 and §5 | A test per step shows `open`, then `done` after its row lands. R8: a real database | AGENT-SAFE · MEDIUM |
| **OB-1b** | The Get started card on My Day (§6), and the `setup` prefs key (§7.2) | A vitest test per rule of §6. An e2e test: hide, reload, still hidden | AGENT-SAFE · MEDIUM |
| **OB-1c** | Invite on the card. One `InviteForm`, moved out of `OrganizationAdmin.tsx`, serves the card and Settings | One component in both places, with `lib/inviteSeat.ts` unchanged. An e2e test sends from the card | AGENT-SAFE · MEDIUM |
| **OB-2** | One welcome dialog (F2, M2). The invite step leaves it, because the card does that now | A founder sees one dialog, then My Day with the card. The welcome e2e cases change in the same PR | AGENT-SAFE · LOW |
| **OB-3** | Help → Get started opens the card again. It joins HLP-1 (task 673) | Clears `hidden_at`. One e2e test | AGENT-SAFE · LOW |
| **OB-4** | The empty state of each live app teaches (P8), through `components/EmptyState.tsx` | Each live pane has one first act and one "Ask the assistant how". A source test lists the panes | AGENT-SAFE · LOW |
| **OB-5** | The chat reads `setup_status`, and opens each step's form filled | A tool test per step. The chat never sends an invite itself (D89 jobs, D-PM-40) | AGENT-SAFE · MEDIUM |
| **OB-6** | `connect_calendar` joins the registry | Waits on CE-10 | AGENT-SAFE · LOW |
| **OB-7** | An admin sees which members have open steps. It joins SH-8 (task 953) | Waits on SH-8 | AGENT-SAFE · MEDIUM |

**Metorite tasks.** OB-0 is 1337, OB-0b is 1339 and OB-0c is 1340. OB-1 is
670, with OB-1a 1341, OB-1b 1342 and OB-1c 1343. OB-2 is 1344, and OB-3 is in
HLP-1 (673). OB-4 to OB-7 are 1345 to 1348. The design tasks 177 (the organization's path) and 178
(the member's path) close with §4 and §5.

OB-8 (the phone push prompt) stays in `calendar_events_reminders.md` §10.4.
The `phone_push` step reads its result and adds no second prompt.

## 9. Decisions

The owner accepted each default on 2026-11-02, recorded as **D150**.

1. **D-OB-1. The first visit skips "Choose the apps for your sidebar".** The
   answer's defaults apply, and "+ Add apps" changes them later. *Why:* a person
   who has not used the product cannot choose apps well. It is also a second
   dialog before value (P2). It reverses the step that the owner added on
   2026-10-11. "Change my layout" keeps both steps.
2. **D-OB-2. The founder's dialog loses its invite step.** The card's
   `invite_team` does the invite in place (P4). It changes the third step of
   CP-2c (owner, 2026-08-24).
3. **D-OB-3. The founder's four default steps are `invite_team`,
   `connect_email`, `first_project` and `ask_assistant`.**
4. **D-OB-4. The card leaves My Day for good when the last step is done.** It
   does not come back for a new step. A new step goes to the setup coach.

## 10. Verification

```bash
cd workbench/control_plane
npx vitest run src/lib/shell/welcomeSteps.test.ts
npx playwright test e2e/shell-presets.spec.ts --project=chromium
```

From OB-0b on, also:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_signup_provision_route.py -q -rs
```

Each later slice adds its own command here, in the same PR.

## 11. Fences

| Rule | Fence |
|---|---|
| A way out of the welcome never waits on the server | `e2e/shell-presets.spec.ts`, "a slow server never holds the founder's welcome open" |
| The founder's mark leaves the address and keeps the rest | `src/lib/shell/welcomeSteps.test.ts`, "the founder's mark leaves the address" |
| One `setup_status`, and no second status function | Advisory until OB-1a adds a source test |
| A step is done from real state, never from a click | Advisory until OB-1a adds a test per step |
