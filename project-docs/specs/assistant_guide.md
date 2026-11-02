# The guide — the assistant answers how, and shows how

**Status: ACTIVE, 2026-11-02 (D167).** Verified against code on 2026-11-02, at
`origin/main` `ac15809b1`. Board row: **WS-OB** (`work_plan.md` §2). Metorite:
the Onboarding project, epic 1451.

The owner answered the four questions of §8 on 2026-11-02, recorded as
**D167**. The owner changed D-GD-3: practice records live only in the browser.

**Source.** Owner direction, 2026-11-02, in chat:

```text
"Would it be possible to have an onboarding tour when you open the app for the
first time so that a user gets familiar with the entire user interface quickly?
The side chat actually walks somebody through the entire user flow. And this is
not just programmatic. This is actually something that users can chat and ask
questions about: what to do and how to do what.

And the chat AI should be able to:
- Open any app.
- Open any function.
- Highlight any button somehow.
- Open any subpage of the app.
- Create and do some functions.
- Possibly even create dummy entries in CRM and delete them later on, or dummy
  projects, subprojects, or tasks, depending on what the user asks, so that
  we're able to show exactly how any particular thing is being done in
  Metorite.

What I'm trying to get at is that the chat also then becomes a place where they
can learn how to use the entire interface. And the interface should not just
give an answer but also, on request, be able to show how it's done."
```

The owner asked for the plan first. The build starts with wave 1 of §7.

## 1. Scope and non-goals

**In scope.**

- A short tour of the whole interface, offered when a member opens Metorite for
  the first time. The side chat runs it.
- "How do I …" answers in the chat, from one corpus of guides.
- **Show me.** The chat opens the page, points at the button, and steps the
  member through. The member presses each button.
- **Do it for me.** The chat does the act with its own tools, then shows the
  result.
- **Practice.** Sample records that live only in the browser tab. They never
  reach the database, and End removes them at once (D-GD-3).
- A chapter per app, offered on the first visit to that app.

**Not in scope.**

- A second chat or a second assistant. `navigation_shell.md` §7.1 keeps one
  assistant, and the guide runs inside it.
- A second write path. "Do it for me" uses the chat tools of
  `chat_parity.md`. Outside practice mode, the guide never clicks a button or
  submits a form in the page (§3 G3).
- A tour that starts by itself and blocks the page (§3 G1).
- A guide to a `preview` pane, or to a pane that the member does not hold.
- Videos, screen recordings and a public help website. HLP-3 (task 675) owns
  the help pages, and they render from the same guides (§5.2).
- Setup steps. `onboarding.md` owns the Get started card. The tour points at
  the card, and the card keeps its own steps.

## 2. What exists today

| Part | Where | What it gives the guide |
|---|---|---|
| `show_in_app(path, why)` | `apps/services/orchestrator/orchestrator/agents.py:630`. Spec `chat_first_surface.md` §4.4 (CF-3) | The chat opens an in-app page. The server refuses a path with a host or a scheme, and a pane that the member does not hold (`in_app_path.py`). One page move per turn. LIVE in production, `CHAT_FIRST_TOOLS` on |
| The page-move rule | `runMovesPage` and `onChatLink` in `workbench/control_plane/src/lib/shell/assistant.ts:181` | A run moves the page only beside the desktop side panel. A link that the member taps opens its page on a phone, and the sheet drops to the peek strip |
| The jobs of each app | `packages/acb_common/acb_common/app_manifests/shell/*.json`, generated to `src/lib/shell/jobs.generated.ts` by `scripts/gen_shell_manifests.py` | 23 jobs with a `?do=` link, such as `/crm?do=new-deal`. A job opens its form and never saves (D89) |
| The live panes | `PANES` in `src/lib/nav.ts`, fenced by `nav.test.ts` | Each pane has a `label`, a `blurb`, a `feature` and a `launch` status |
| Chat parity | `packages/acb_skills/acb_skills/chat_parity.py`, fenced by `tests/unit/test_chat_parity.py` | Each UI action has a chat tool (COVERED), an exemption, or a debt row with its wave |
| Anchors | None. No component carries a guide attribute | Nothing to point at yet (GD-1) |
| A tour library | None in `workbench/control_plane/package.json` | GD-2 builds the spotlight on the design system |
| A help corpus | None. HLP-3 says the docs folder holds no end-user docs | Nothing to answer "how do I" from (GD-3) |
| First-use cards in Projects | PC-2, task 971 (epic 958): "Show me" in at most 3 steps | A consumer of this runner, not a second runner |
| A practice or sample flag | None, in any table. STU-G4's preview store is specified only, and it holds Studio records (`ai_studio.md` §G4) | Practice needs none, because it stays in the browser (§5.6) |
| One API client | None. The control plane makes 252 raw `fetch("/api/...")` calls in 67 files. `public/sw.js` serves web push | The practice guard wraps `fetch` in the tab (§5.6) |
| A chat write today | Every Projects and CRM chat write asks on a card (`skill_projects/writes.py:164-181`). "Act at once with Undo" (D-CF-5) waits for CF-6, which is not built | "Do it for me" shows a card until CF-6 lands |
| The CRM chat tools | `create_lead` and `convert_lead` only. No chat tool makes a deal, a contact or a company, and none deletes (`chat_parity.py`, P-3 debt) | "Do it for me" covers less in the CRM than in Projects |

**What a write sets off.** §5.6 lists what fires when a task, a project or a
CRM record is written. That list is why practice never writes.

## 3. Principles

- **G1. Offer, never push.** The tour starts when the member asks. A tour that
  a user starts completes at 64% to 69%. A tour that starts by itself completes
  at 23% to 31% (`research/assistant_guide_2026-11.md`).
- **G2. Short.** The first tour has five stops at most. An app chapter has five
  at most. More comes when the member asks for it.
- **G3. Point, do not click.** The guide rings the button, and the member
  presses it. Microsoft's Copilot "Highlights" works the same way. It matches
  `onboarding.md` P9: the chat fills the form, and the person presses the
  button. The one exception is practice mode, where no write leaves the tab
  (§5.6). There the guide may play the clicks.
- **G4. One write path.** "Do it for me" calls the chat tool that chat parity
  names. That tool keeps its card, its Undo and its refusals.
- **G5. Ask at any time.** The tour lives in the chat. A question pauses it,
  and one chip continues it.
- **G6. Practice never leaves the browser.** A practice record is never sent
  to the server. So it sends no mail, no notification and no event, and only
  the learner sees it. End removes it at once.
- **G7. Never point at what the member cannot use.** A guide checks the
  member's features, the admin flag and the live set before it opens a page.
- **G8. One corpus.** A guide is data. The tour, the chat, the Help pages and
  PC-2 read the same guide.
- **G9. A guide that rots fails a test.** Each path and each anchor of each
  guide must resolve (§9).
- **G10. A way out works at once.** Escape and "End tour" close the guide in
  the same frame, as `onboarding.md` P3 requires.

## 4. What the member sees

### 4.1 The first open

The one welcome dialog of D150 (OB-4) gets a second button: **"Show me
around"**. "Explore on my own" stays. The dialog stays the one interruption of
`onboarding.md` P2. The tour starts only from that button, from Help → "Take
the tour", or from the chat ("give me a tour").

**Before OB-4 and HLP-1 (GD-5a).** The button sits in the founder's welcome
step of the dialog that exists today. Every member finds "Take the tour" in the
account menu. Stop 4 rings My Day alone until OB-1b builds the Get started
card. GD-5b adds the later entries when their slices land.

The tour runs in the side chat. Each stop rings one part of the page, and the
chat says one or two sentences about it. The chat shows "2 of 5", with Back,
Next and End tour. The message field stays open the whole time.

| Stop | Points at | The chat says (draft) |
|---|---|---|
| 1 | The app list in the sidebar | "Your apps live here. You see only the ones your company turned on." |
| 2 | The Ask field in the top bar | "Type here to find any app, any job, or to ask a question." |
| 3 | The assistant panel | "Ask me how to do anything, or ask me to do it." |
| 4 | My Day and the Get started card | "Your day starts here. The card lists the setup steps left." |
| 5 | The message field | "Try it: ask 'How do I add a deal?'" |

The last stop offers one chip per live app that the member holds: "Show me
around CRM", and so on. A chip starts that app's chapter.

### 4.2 "How do I …"

The member asks, for example, "How do I add a deal?". The chat answers in
three short steps from the guide. Under the answer, it offers up to three
buttons:

- **Show me.** It runs the guide on the real page (§4.3).
- **Do it for me.** It shows only when a chat tool covers the act (§4.4).
- **Open CRM.** It opens the page, the same as `show_in_app`.

When no guide matches, the chat says so in one line and offers to open the
app. It never invents a button that does not exist. The miss counts as a guide
gap (GD-14).

### 4.3 Show me

1. The page opens. The spotlight rings the first button, and the chat says
   what to press.
2. The member presses it. The guide sees the next part appear, and moves on by
   itself. Next also moves on.
3. At a form, the chat offers **"Fill with sample values"**. The form fills,
   and each filled field shows a "Sample" mark. The spotlight rings Save.
4. The member decides. Save makes a real record. Cancel leaves nothing.

On a phone, the member's tap on Show me drops the sheet to the peek strip, as
a tapped link does now (`onChatLink`). The strip shows the step line, Next and
End.

### 4.4 Do it for me

The chat calls the tool that chat parity names for the act. The tool's own rule
applies. Today every Projects and CRM chat write shows a card first. When CF-6
lands, a reversible write on the member's own work acts at once and offers
Undo (D109, D-CF-5). Then the chat opens the result with `show_in_app` and
rings it.

### 4.5 Practice

The member says "make a dummy deal and show me", or presses "Practice it" under
an answer. The tab enters **practice mode**. A bar across the top says
"Practice: nothing is saved", with an End button.

1. The guide fills the form with sample values. Then the member presses Save,
   or says "play it for me" and the guide presses it.
2. The new record shows in the list, the board or the pipeline, marked
   **Practice**. The server never sees it.
3. The guide goes on with the record. It moves a deal to the next stage, or
   adds a task to the practice project.
4. End removes every practice record at once. A reload does the same.

The member may say "add it for real". The guide then leaves practice mode,
opens the same form with the same values, and the member presses Save. "Do it
for me" also works, through the chat tool and its card (§4.4).

### 4.6 An app chapter

The first visit to an app shows one chip in that app's title bar: "New to CRM?
Show me around · 4 steps". It shows once. A dismissal stays on every device.
In Projects, the PC-2 cards (task 971) are these chapters.

## 5. The design

### 5.1 Anchors

Each app's shell manifest (`app_manifests/shell/<app>.json`, D89) gains an
`anchors` list. Each anchor has an `id`, a `label`, the `route` where it lives,
and one line on what it does. The shell has its own anchors in `home.json` and
`chat.json`.

The component carries `data-guide="<app>.<id>"`, for example
`data-guide="crm.new-deal"`. `scripts/gen_shell_manifests.py` writes
`src/lib/shell/anchors.generated.ts`, as it writes the jobs now. Fence F1
(§9) holds the manifest and the source equal in both directions.

An anchor is a contract with the guide. A redesign that moves a button keeps
its `data-guide`, or the fence fails.

### 5.2 The guides

A guide is one JSON object in `app_manifests/guides/<app>.json`. The generator
writes `src/lib/shell/guides.generated.ts`, and the orchestrator reads the JSON
itself.

| Field | Meaning |
|---|---|
| `id` | Unique, such as `crm.add-deal` |
| `app`, `feature`, `admin_only` | Who may run it (G7) |
| `title` | "Add a deal" |
| `asks` | Three to six ways a member asks for it |
| `steps` | An ordered list. Each step is `open` (a path), `point` (an anchor), `fill` (a form and its sample values) or `say` (text only). Each has one short `say` line |
| `do_tool` | The chat tool that does the act, or `null`. Fence F2 checks that chat parity marks it COVERED |

HLP-3 (task 675) renders one Help page per app from these guides. PC-2 reads
them for its first-use cards. No second copy of the steps exists. Guide text is
copy that a member reads, so it follows the user-facing copy rules and never
names an internal agent.

### 5.3 The runner

One state machine in `src/lib/shell/guide.ts`, and one shell component,
`GuideSpotlight.tsx`. No app builds its own (D89).

- **Start.** A button (the welcome dialog, Help, a chip, an answer's Show me)
  or an AG-UI `custom` event named `guide` from the assistant.
- **Open.** The runner pushes the route. It checks the path with
  `isInAppPath` first, as `show_in_app` does.
- **Point.** It waits up to 3 seconds for the anchor. Then it rings it with a
  popover of the design system, in glass tokens (D149). It scrolls the anchor
  into view.
- **Move on.** Next, a click on the ringed part, or the route of the next step.
- **A missing anchor.** The step shows its text, and the chat says "I can't
  point at it on this screen". The runner logs `guide_anchor_missing`. It never
  fails in silence.
- **End.** Escape, End tour, or the last step. The page stays where it is.
- **State.** The current guide and step live in `sessionStorage` for the tab,
  so a reload resumes. `tour_seen_at` joins the shell prefs, with no new table.
- **Access.** One polite live region in `GuideHost` announces each step, at
  every width. The popover, the step card and the strip hold none. Focus stays
  on the page, so the member can press the ringed button. The ring follows its
  anchor with no transition, so it never trails a scroll. The step line shows
  once: the popover shows only when neither the step card nor the phone strip
  is on screen. The ring stays inside the window. It works in light mode, at compact density, under a changed accent
  and at 390 px.

### 5.4 The assistant's tools

Two tools of `orchestrator`, beside `show_in_app` in `agents.py`:

- **`find_guide(question)`** returns the three best guides that the member may
  run, with their steps as text and whether "Do it for me" is open. The chat
  answers from these steps.
- **`show_guide(guide_id)`** emits the `guide` event. It counts as the one page
  move of the turn, and it shares that flag with `show_in_app`. It moves the
  page only by the rule of `runMovesPage`, or on the member's own tap.

One line joins the persona: "When the member asks how to do something, call
find_guide. Answer from its steps, and offer Show me." The routing eval gains
cases for "how do I", "show me" and "do it for me".

### 5.5 Sample values

A `fill` step names a form and its sample values. A `?do=` form opts in through
one hook, `useGuideFill(formId)`. It fills the fields and marks each one
"Sample". It never presses Save. A form without the hook gets a `say` step
instead. Fence F3 lists the forms that opt in.

### 5.6 Practice records

**The owner's rule (D-GD-3).** Practice records live only in the frontend. They
never touch the database, and they go away at once.

**Why the database stays out.** The audit of 2026-11-02 found these facts.

- No table has a practice or sample flag.
- **The CRM is org-wide.** Every CRM record is visible to every member who
  holds the CRM (D-CRM-3). Lead conversion matches a company by its exact name
  and a contact by email (`crm/pipeline.py:794-851`).
- **A Projects write sets off many things.**
  - The event bus: `core.emit`, with 55 call sites.
  - The bell: `notify`.
  - Links to real tasks: `link_from_text`.
  - The workload that colleagues see: `compute_load`.
- **Deletes are hard.** A task tombstone keeps its number.

A row in the database would need a check at each of these places. A record that
never leaves the tab needs none of them.

**The practice guard.** One module, `src/lib/shell/practice.ts`, and no other
place.

1. **Per tab.** Practice mode starts when the member starts a practice guide.
   It ends on End, at the last step, or on a reload. No other tab sees it.
2. **The guard wraps `fetch`** in that tab while practice mode is on. The
   control plane has no shared API client (§2), so `fetch` is the one point that
   every request passes.
3. **A known write answers in the tab.** Each practice template names its
   routes. A POST to such a route returns a record whose id starts with
   `practice-`. The guard keeps it in memory. A PATCH or DELETE of a
   `practice-` id changes that record.
4. **Every other write is blocked.** A POST, PATCH, PUT or DELETE to `/api/`
   that no template handles never leaves the tab. It returns an error, and the
   bar says "End practice to make real changes". The guard fails closed.
5. **A known read merges.** A GET of a template's list route goes to the
   server as normal. Then the guard adds the practice records to the answer.
6. **The record shows its mark.** Each practice record carries
   `practice: true`. The list, the board, the pipeline and the panel show the
   "Practice" badge, in the one status vocabulary (`statusAccent.ts`).
7. **The chat stays out of it.** The chat's write tools run on the server and
   never see a practice record. In practice mode, the chat offers "play it for
   me", and the runner does the step in the page.
8. **Memory.** A practice run marks its turns, and the end-of-run memory pass
   (`routes/agent.py:2742-2760`) skips them. No sample company becomes a fact
   about the member (GD-13).

**The limit.** A list that the server renders (a React Server Component) does
not pass through the tab's `fetch`, so a practice record cannot show in it. A
template is allowed only where its list loads in the browser. Its e2e test
proves that the record shows (F5).

**The first templates.** CRM: a lead, a deal, a contact, a stage move and a
note. Projects: a project, a subproject and three tasks.

### 5.7 Safety

- Outside practice mode, the guide only points, opens and fills. It cannot
  click, submit, delete or send. In practice mode, it may press a button,
  because the guard blocks every write that it does not answer itself.
- A `point` step takes an anchor id from the generated list. A guide cannot
  name a CSS selector, so text in an email or a CRM note cannot steer the ring.
- A path passes `isInAppPath` and the member's pane check, on the server and
  in the client.
- "Do it for me" adds no right. The tool refuses what the member cannot do, as
  it does now.
- A practice record never leaves the tab (G6). §5.6 holds the guard, and F5
  is its fence.

### 5.8 What we measure

The runner and the tools write one event for each act. The acts are: tour
offered, started, stop reached, ended (and where), Show me, Do it for me, anchor
missing, and no guide found. GD-14 shows the weekly list of questions with no
guide, and of anchors that went missing.

We read the first 20 organizations by hand, as D153 does for the setup
assistant. The test of value is a real act within 7 days of its guide. It joins
the activation measure of `onboarding.md` §12.9.

## 6. The slices

Epic: **GD**, Metorite task 1451. Each slice is a task under it. GD-0 to GD-9
are tasks 1452 to 1461. GD-10a, GD-10b and GD-10c are 1462 to 1464. GD-11,
GD-12 and GD-13 are 1466 to 1468, and GD-14 is 1465. GD-5 became GD-5a
(task 1457) and GD-5b (task 1493) after the audit of 2026-11-02.

The design task 178 closes with this spec.

| Slice | What | Done when | Gate · tier |
|---|---|---|---|
| **GD-0** | This spec, its decision, the INDEX row and the board row | spec-auditor returns GO. The INDEX lists it | AGENT-SAFE · LOW |
| **GD-1** | Anchors in the shell manifest, the generator, and `data-guide` on the shell's own parts (§4.1) | F1 passes, and fails on a removed attribute | AGENT-SAFE · LOW |
| **GD-2** | `GuideSpotlight`: the ring, the popover, and its access rules (§5.3) | A vitest test per rule. Four captures. An e2e test rings an anchor | AGENT-SAFE · MEDIUM |
| **GD-3** | The guide format, its loader, F2, and the tour guide `home.tour`. Its five steps are the five stops of §4.1 | F2 passes, and fails on a bad path, anchor or tool | AGENT-SAFE · LOW |
| **GD-4** | The runner (§5.3), with the phone strip | vitest per state. An e2e test runs a guide, reloads, and resumes | AGENT-SAFE · MEDIUM |
| **GD-5a** | 🔨 **BUILT, dark (2026-11-02).** The first-run tour (§4.1) before OB-4 and HLP-1: "Show me around" in the founder's welcome step, "Take the tour" in the account menu, `tour_seen_at` in the shell prefs, and `GUIDE_ORGS` on `GET /auth/me/shell`. **Built:** `gateway/guide/flags.py` is the one reader of `GUIDE_ORGS`, and the org comes from the session. `shellPrefs.ts` passes `guide` to `guide.ts`, the ONE check: off refuses each start, resume and offer, and a start waits until the read answers. "Take the tour" is a row of `accountMenu` and of the phone drawer. Each row goes to `/?guide=home.tour`, because stop 4 rings My Day, which lives on `/` only. `tour_seen_at` is a key-only stamp with the server's time, and a layout save keeps it. It keeps the first start, in SQL. The last stop shows up to six chips of the held live apps. A chip ends the tour and opens its app (GD-10 adds the chapter). On the last stop the card carries the line, so no popover covers the chips | An e2e test: a founder and a member each run it. It never starts alone. With the flag off, no entry shows. Verify: `uv run pytest tests/unit/test_guide_flags.py tests/unit/test_auth_me_shell.py tests/unit/test_auth_me_shell_r8.py -q` and `npx playwright test e2e/guide-first-run.spec.ts e2e/guide-tour.spec.ts` | AGENT-SAFE · MEDIUM |
| **GD-5b** | The later entries: the OB-4 dialog button, the HLP-1 Help row, the Get started card at stop 4 (OB-1b), and "give me a tour" through `show_guide` (GD-7) | Each entry lands when its slice lands. The GD-5a e2e test runs from each entry | AGENT-SAFE · LOW |
| **GD-6** | Ask during the tour: a question pauses it, and a chip continues it | An e2e test asks at stop 3 and continues to stop 4 | AGENT-SAFE · MEDIUM |
| **GD-7** | `find_guide` and `show_guide` (§5.4), the persona line, the eval cases | A tool test per refusal. The eval cases pass. An eval over USD 0.50 waits for the owner | AGENT-SAFE · MEDIUM |
| **GD-8** | Sample values (§5.5), first in the CRM and Projects quick-create forms | A test per form. Save is never pressed. F3 lists the forms | AGENT-SAFE · MEDIUM |
| **GD-9** | Do it for me (§4.4) | Offered only for a COVERED tool. An e2e test does an act and rings its result | AGENT-SAFE · MEDIUM |
| **GD-10a, GD-10b, GD-10c** | One chapter per app: anchors, 5 to 10 guides, and the first-visit chip. GD-10a is Projects, with PC-2. GD-10b is CRM. GD-10c is the rest, one PR per app: Email, Calendar, Chat with Knowledge and Notes, WhatsApp, Admin | F1 and F2 pass for the app. Four captures of its chip | AGENT-SAFE · LOW |
| **GD-11** | The practice guard (§5.6 items 1 to 7): practice mode, the bar, the `fetch` guard, "play it for me", and "add it for real" | F5 passes, and fails when a write reaches the network. Opus review, because it wraps every request of the tab | AGENT-SAFE · MEDIUM |
| **GD-12** | The first practice templates (§5.6): CRM lead, deal, contact, stage move and note. Projects project, subproject and tasks | An e2e test per template: the record shows with its badge, and no request reaches the server | AGENT-SAFE · MEDIUM |
| **GD-13** | A practice run teaches the memory nothing (§5.6 item 8) | A test: a practice run's turns reach no memory write | AGENT-SAFE · MEDIUM |
| **GD-14** | The events of §5.8 and the weekly gap list | A test per event. The list shows on the admin's page | AGENT-SAFE · LOW |

## 7. The order

| Wave | Slices | Why |
|---|---|---|
| 1. The tour | GD-1, GD-2, GD-3, GD-4, GD-5a | The owner's first ask. It needs no AI, and it waits on no other workstream |
| 2. Ask and show | GD-5b, GD-6, GD-7, GD-8, GD-10a, GD-10b, GD-14 | "How do I" with Show me, in the two apps that new members open first. GD-5b adds each entry when its slice lands |
| 3. Do and practice | GD-9, GD-11, GD-12, GD-13, then GD-10c | Practice needs the runner and the fill hook first |

The guide ships dark behind `GUIDE_ORGS`, a per-organization list like
`SETUP_ASSISTANT_ORGS`. The scripted tour costs no AI. A question costs one
chat turn.

## 8. Decisions (D167)

The owner answered on 2026-11-02, in chat. Three defaults stand. The owner
changed D-GD-3.

| Id | Question | The owner's answer |
|---|---|---|
| **D-GD-1** | Does the tour start by itself at the first open? | **No.** The welcome dialog offers "Show me around", and the member presses it. Help and the chat start it too |
| **D-GD-2** | Does "Do it for me" press the buttons on screen? | **No.** The chat acts with its own tool and card, then opens and rings the result. Practice mode is the one place where the guide presses (§5.6) |
| **D-GD-3** | Where do practice records live? | **Only in the browser.** They never touch the database, and they go away at once. A real record is made only when the member asks for one (§4.5, §5.6) |
| **D-GD-4** | Who pays for a guide question? | **Metorite, inside the D-OB-6 cap, for the first 14 days.** After that, a normal chat turn |

The owner's words on D-GD-3:

```text
"Figure out a system where dummy data can be added and immediately be removed.
Possibly have this done only in the frontend for the trial and showing, and
don't even need to touch the database. I'm just testing over here the auto
system, which doesn't actually add any record but just shows for demo. Unless
the user actually wants to add in series data"
```

"Series data" reads as "real data". The audit of §5.6 explains why this rule
is also the safe one.

This spec amends `onboarding.md` §1 and P2. A pushed tour of coach marks stays
out. A tour that the member starts, run by the chat, is in.

## 9. Fences

| Fence | Test | It fails when |
|---|---|---|
| **F1** | `tests/unit/test_guide_anchors.py` | A manifest anchor has no `data-guide` in the source, or a `data-guide` has no manifest row |
| **F2** | `tests/unit/test_guides.py` | A step's path is not a live in-app path, its anchor is unknown, its `do_tool` is not COVERED, or its feature does not match its app |
| **F3** | `src/lib/shell/guideFill.test.ts` | A `fill` step names a form without the hook, or the hook presses Save |
| **F4** | `src/lib/shell/guide.test.ts` | The tour starts with no member act, or Escape leaves the ring on the page |
| **F5** | `src/lib/shell/practice.test.ts` and `e2e/guide-practice.spec.ts` (GD-11, GD-12) | A write in practice mode reaches the network, a write that no template handles is not blocked, the guide presses outside practice mode, or End or a reload leaves a practice record |

## 10. Verification

```bash
uv run pytest tests/unit/test_guide_anchors.py tests/unit/test_guides.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/shell/guide.test.ts src/lib/shell/guideFill.test.ts src/lib/shell/practice.test.ts
cd workbench/control_plane && npx playwright test e2e/guide-tour.spec.ts e2e/guide-practice.spec.ts
```

Each UI slice also runs the visual review: light mode, compact density, a
changed accent, beside its neighbour, and 390 px.
