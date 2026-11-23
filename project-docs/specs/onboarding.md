# Onboarding — the first hour of a founder and of a member

**Status: ACTIVE, 2026-11-02.** Verified against code on 2026-11-02, at
`origin/main` `a5cbbdee2`. Board row: **WS-OB** (`work_plan.md` §2). Metorite:
the Onboarding project, epics 669 and 1351. The owner accepted §9 on 2026-11-02,
recorded as **D150**. The owner accepted §12.10 the same day, recorded as
**D153**.

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
- A product tour with coach marks. §3 P2 says why. ⚠️ D167 amends this:
  `assistant_guide.md` adds a tour that the member starts, run by the chat.

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
  needed, so this spec has none. The hello of §13 (D177) is not a second
  interruption. It sits beside the page, blocks nothing, and closes in one tap.
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

The founder's default steps of D150. ⚠️ D153 sets a new order, and §12.3 holds
it:

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
| **OB-4** | One welcome dialog (F2, M2). The invite step leaves it, because the card does that now | A founder sees one dialog, then My Day with the card. The welcome e2e cases change in the same PR | AGENT-SAFE · LOW |
| **HLP-1** | Help → Get started opens the card again. It is part of task 673 | Clears `hidden_at`. One e2e test | AGENT-SAFE · LOW |
| **OB-2** | One useful answer after the first mail sync (§12.4). Task 671 | A new member who finishes the first sync sees one answer built from that mail | AGENT-SAFE · MEDIUM |
| **OB-3** | The empty state of each live app teaches (P8), through `components/EmptyState.tsx`. Task 672 | Each live pane has one first act and one "Ask the assistant how". A source test lists the panes | AGENT-SAFE · LOW |
| **OB-5** | The chat reads `setup_status`, and opens each step's form filled | A tool test per step. The chat never sends an invite itself (D89 jobs, D-PM-40) | AGENT-SAFE · MEDIUM |
| **OB-6** | `connect_calendar` joins the registry | Waits on CE-10 | AGENT-SAFE · LOW |
| **OB-7** | An admin sees which members have open steps. It joins SH-8 (task 953) | Waits on SH-8 | AGENT-SAFE · MEDIUM |
| **OB-10 to OB-26** | The AI-led layer. §12.7 holds the order | Each task holds its own test | Mixed. OB-26 is OWNER-GATE |

**Metorite tasks.** OB-0 is 1337, OB-0b is 1339, OB-0c is 1340 and OB-0d is
1350. OB-1 is 670, with OB-1a 1341, OB-1b 1342 and OB-1c 1343. OB-2 is 671 and
OB-3 is 672, and both predate this spec. OB-4 is 1344. OB-5 to OB-7 are 1346 to
1348. OB-10 is 1345. OB-11 to OB-19 are 1352 to 1360. OB-20 to OB-26 are 1361
to 1367. Epic 1351 holds the AI-led layer. The design tasks 177 (the organization's path) and 178
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

## 12. The AI-led layer

**Status: ACTIVE, 2026-11-02 (D153).** The owner gave this direction on 2026-11-02.
Metorite urges a new admin to set up context and connect work surfaces. A person
who joins an existing organization gets a different path.

Metorite: epic 1351, OB-AI. §12.10 holds the decisions for the owner. The record
is `research/ai_native_onboarding_2026-11.md`.

**The rule of this section.** The card of §6 stays the base. Every step works
with no AI. The assistant does the typing: it drafts, fills a form and explains.
A person confirms. The layer sits behind the org flag `SETUP_ASSISTANT_ORGS`,
default OFF.

### 12.1 What other apps do

The evidence is thin. Almost every number is the seller's own. No test was run
on a B2B work operating system.

| Source | What it does | Grade | What we take |
|---|---|---|---|
| Amplitude, A/B tests on its own AI chat | An empty chat box lost to clickable starts. One job-title question that routed to role-tailored clicks doubled new-user conversion. A chat-first Home raised engagement 55%. A chat-first chart empty state lost to an entry inside the workflow | Measured, no sample sizes | OB-10. Chat-first is a test per surface, not a rule |
| Attio | A connected mailbox creates people you emailed or met, and a company per non-public domain. It shows a tracker, then "In sync". Bodies stay private | Vendor docs | OB-2, OB-17 |
| Fyxer | It works inside the member's own Gmail. It says that early suggestions may be off. It has a scheduled briefing | Vendor docs | OB-2, and the daily return |
| Stripe | Test mode works at once. A checklist of eight items gates going live | Vendor docs | Value before setup. We tick from server state, not the browser |
| Linear | Separate admin and joiner checklists. Import stays an admin act | Vendor docs | §12.8. Import is never forced |
| Notion, Slack, Canva, Miro | A person joins by email domain, under an admin's setting. Notion's Free plan makes domain joiners owners | Vendor docs | OB-22, with least privilege |
| Claude, ChatGPT | An owner enables a connector, then each member signs in. Member memory stays off until the member turns it on | Vendor docs | The two gates of OB-15 |
| Claude, Gemini | Memory import since about March 2026 | Vendor docs | OB-23 |
| Microsoft Learning Agent | A guided journey of 5 to 10 minutes that asks questions. In preview, with no outcome data | Vendor notice | OB-21 |
| Glean | First index takes 2 to 14 days, and users stay out until it ends | Vendor docs | The trap. OB-2 gives a first output from a small source |
| Chameleon, Userpilot | User-launched tours complete at 64%, against 33.5% overall. Checklist completion has a median of 10.1% | Vendor data | Pull beats push. Checklist completion is not the metric |

**What we do not take.** Perspective AI's "3.2x activation" claim carries
selection bias. A forced import has no precedent. A setup score or streak grades
the admin.

**Not verified.** No first-run screen was captured for any product, and no
joiner screen. No product documents an AI interview that sets up a company, so
OB-12 and OB-13 are a bet. OB-25 closes what it can.

### 12.2 The one measured win

The welcome question already exists. OB-10 uses its answer. My Day and the empty
chat show three clickable asks for that answer. An empty box never greets a new
member. The hello of §13 (D177) is the one greeting, and it carries its own
three asks.

### 12.3 The new organization: company context

No company profile exists today (`external_agent_access.md` §4, item 8). The
assistant therefore knows almost nothing about a new company. This is the
context that the admin is urged to give.

1. **The draft (OB-12).** The assistant reads the admin's public website through
   the one fetch guard. It drafts up to eight fields, and each field cites its
   page. The admin accepts, edits or removes each field, then presses Save.
   Nothing is written before Save (D110).
2. **The gaps (OB-13).** The setup chat asks only what the site and the signup
   did not answer. It asks three questions at most, one at a time. Each states
   why it asks and what the answer changes.
3. **The store (OB-11).** Confirmed facts are org-audience facts in Knowledge.
   The profile is one read over them. A second store of company facts is a
   defect.
4. **The rest of the setup chat (OB-13).** It takes the open steps in order, and
   it resumes from `setup_status` on any device.
5. **Documents (OB-14).** A dropped deck gives the same proposals.
6. **The team (OB-17).** The first sync proposes same-domain people from mail
   headers. It fills `InviteForm`, and a person presses Send.
7. **A first project (OB-18).** The chat proposes one, shows the plan card, and
   writes only on a yes.

The founder's list becomes `company_context`, `connect_email`,
`connect_surfaces`, `invite_team` and `first_project` (D-OB-5). The setup chat
ticks `ask_assistant` as a by-product, so that step moves to "More steps".

### 12.4 Connect your work, then a first output

**One panel (OB-15).** "Connect your work" ranks the surfaces by the welcome
answer. Each row says what it unlocks, who connects it and its live state. The
state comes from the one connection registry (D96). A row opens the app's own
connect flow.

**Two gates.** An admin enables an org-level connection. Each member connects
their own mail and calendar. A member never sees an org-level act that they
cannot do.

**A first output in the same session (OB-2).** While the first sync runs,
`OnboardingPanel.tsx` shows live counts of what it found. The sync reads the
last 30 days first. The first answer is a prepared day with clickable items, and
each item names its source. Older mail fills in behind it.

**The daily return.** My Day is the return. The brief (WS-BRIEF) is the scheduled
reason to come back, at a time that the member picks.

### 12.5 Consent and trust (OB-16)

- A plain panel comes before each provider screen: what Metorite reads, what it
  never does without asking, and how to disconnect.
- The notice of D-PZ-10 comes before any read of sent mail.
- A write on the member's own work acts at once with Undo. An act that touches
  another person asks first. An output to a non-member has a preview and an off
  switch.
- Website text and pasted text are untrusted data.

**The limits of the providers.** Gmail runs in Testing mode: 100 users at most,
7-day refresh tokens, and no CASA yet (tasks 601 and 648). So Connect breaks
weekly, and OB-16 shows one-click Reconnect. A Microsoft tenant that needs admin
consent gets a ready consent link that the member sends. OB-26 removes the
"Unverified" label. Each failure is logged by cause, because no public data on
consent drop-off exists.

### 12.6 The urge (OB-19)

The urge never blocks (P2). It shows an honest gap.

- The card lists what the assistant can do today, and what the next connection
  adds. It reads `setup_status`, so it stays true.
- Each open step says what stays weak without it.
- The setup coach nudges inside D110: one question a day, 14 days after a
  dismiss, and "Don't ask again" stops it.
- No score and no streak.

### 12.7 The order

| Wave | Slices | Why this order |
|---|---|---|
| 1. Plain and measured | OB-0c, OB-0d, OB-1a, OB-1b, OB-4, OB-10, OB-24 | The card, one dialog, the measured win, and the numbers |
| 2. Connections | OB-1c, OB-15, OB-16, OB-2 | Value from the member's own data |
| 3. The joiner | OB-20, OB-3, OB-5 | A member who joins sees their own work |
| 4. The bet | OB-11, OB-12, OB-13, OB-19 | Company context, behind the flag |
| 5. Depth | OB-17, OB-22, OB-14, OB-18, OB-21, OB-23 | After the first orgs are read by hand |
| Owner | OB-26, tasks 601 and 648. OB-25 for legal advice | These cap one-click Connect, so start them now |

### 12.8 The person who joins an existing organization

This design is ours. No source documents a joiner screen that shows the team's
work.

**What differs.** The org already holds colleagues, work and context. So the
joiner gets no company interview, and never sees `company_context`,
`invite_team` or `first_project` unless their role does that act.

- **The first day (OB-20).** The top of My Day shows who invited them, the
  admin's note and the company in one paragraph. It shows their team, their
  projects and the tasks assigned to them. It offers three asks grounded on what
  they can read. Each row reads under the member's own session, so RLS and grants
  bind it.
- **Their steps.** `connect_email`, `ask_assistant`, `know_me` and `phone_push`
  (§5). `know_me` is the interview of PZ-32.
- **The admin's welcome (OB-21).** One note and up to three links.
- **Join before create (OB-22).** A person who proved an address at a domain of
  an opted-in org sees "Ask to join" before the org form. The request goes to the
  queue of `colleague_onboarding.md` §6. The joiner gets the member role.
  Its row joins the one signup screen of `seat_plans.md` §8a.2, below the orgs
  and the invites of the person's own address.
- **Their other assistant (OB-23).** A pasted memory export arrives as proposals.
  The member confirms each line. Only the member reads the profile (D-PZ-3).

### 12.9 The measure (OB-24)

These metrics are our own proposal, not benchmarks.

| Metric | Definition |
|---|---|
| Activation | One accepted AI output on the member's own data in session one, plus a second on another day within 7 days |
| Team | A second member active within 14 days of signup |
| Connect rate | By surface and by failure cause |
| Output outcome | Accepted, edited or rejected |
| Guardrails | Undo rate, wrong-fact corrections, memory deletions |
| Retention, later | Month-3 retention and the M12/M3 ratio (a16z) |

Checklist completion and message count are not success metrics. With few
signups a formal split has little power. So we read the first 20 orgs by hand,
and we run a split only with enough volume.

### 12.10 Decisions

The owner accepted each default on 2026-11-02, recorded as **D153**.

1. **D-OB-5. `company_context` is the founder's first step.** The list of §12.3
   replaces D-OB-3. *Why:* the owner asked that a new admin set up context first.
2. **D-OB-6. Metorite pays for the AI of setup, up to a cap per org.** No credits
   leave a new org for it. *Why:* a founder must not meet a credit limit before
   the first value. This is a commercial call.
3. **D-OB-7. Join before create is OFF until an org admin turns it on.** *Why:*
   it reveals that an org exists, and only the org should choose that.
4. **D-OB-8. The AI layer ships behind `SETUP_ASSISTANT_ORGS`, and we read the
   first 20 orgs by hand.** *Why:* it is a bet with no outside evidence.

## 13. The buddy's hello (D177)

**Owner, 2026-11-05.** A new member must feel supported from the first minute.
The chat says that it is here, and it offers to show the way. D177 records the
directive. The messages that the chat starts later belong to
`proactive_reach.md` §19.

### 13.1 What the member sees

1. The member signs in for the first time. The welcome dialog asks its one
   question and closes.
2. The assistant opens beside the page: the dock on a computer, the full sheet
   on a phone. It opens a new chat named "Welcome".
3. One message waits there. It names the member, and it reads like this:

   > Hi Asha, welcome to Metorite. There's a lot here, so the first look can
   > feel like much. You don't have to learn it all today. I'm here whenever
   > you need me. Ask me anything, and I'll show you where it is.

4. Three chips sit under the message:
   - **Show me around** starts the tour `home.tour` (`assistant_guide.md`
     §4.1).
   - **What can you do here?** sends that question.
   - **I'll look around** closes the panel. The chat stays in the history.

The page stays usable the whole time. Nothing blocks it.

### 13.2 The rules

- **Once per member.** A key-only stamp `buddy_hello_at` in `shell_prefs`
  keeps the hello to one time on every device. The stamp joins `KEPT_KEYS` on
  the path of `tour_seen_at`. A layout save never writes it.
- **After the dialog.** The hello waits while the welcome dialog is open
  (`welcomeOpen`). A joiner with no dialog meets it at once.
- **Never on a deep link, and never during the tour.** A member on a deep link
  came for one thing. The hello waits for the next visit to `/`.
- **No model and no cost.** Code builds the message from one constant and the
  member's first name. It is a sealed assistant row through
  `chat_brief._post_row`, on the precedent of the CF-5 brief and RM-4. So the
  model sees the hello when the member replies.
- **It replaces the desktop nudge** of CF-2 (`ChatNudge`) on that visit. A new
  member has no brief, so the phone shows the hello in its place.
- **One switch.** `GUIDE_ORGS`, the guide's switch, holds it. It is off in
  every org.
- **The words.** The copy is plain, warm, and in the second person. It never
  names a model or an inner service.

### 13.3 What it amends

D177.1 amends three rules for this one open:

- **D-CF-2** in `chat_first_surface.md`: a computer may open the assistant
  once, for the hello.
- **P2** (§3): the hello is not a second interruption.
- **§12.2**: the hello is the one greeting, and it carries three asks.

D-GD-1 stands. The tour starts only when the member taps "Show me around".

### 13.4 The slice

| Slice | What | Task |
|---|---|---|
| **BUD-1** | The hello: the stamp, the route that posts the sealed row, the open after the welcome dialog, the three chips, and the e2e test at 1280 px and 390 px | Metorite 1695 |

The other BUD slices are in `proactive_reach.md` §19.6.
