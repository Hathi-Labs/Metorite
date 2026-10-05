# The shell — how a member finds an app, a job or an answer

**Status:** Specified 2026-10-05. Nothing is built. Board row **WS-44**.
Decisions **D87**, **D88** and **D89** (`work_plan.md` §3).
**Verified against code on 2026-10-05** at `origin/main` `10ef419d6`.
**Owner:** vjvarada.

**Repair round 1, 2026-10-05.** The review found five P1 defects and six P2
defects, and this version fixes them. Round 2 fixes what the verifier
found: a wrong cap citation, three listener sites and five stale anchors.

**Design record:** the concept page "Metorite Navigation Concepts", version 3,
2026-10-05, private to the owner. It holds a clickable prototype for four
people. Where the page and this spec disagree, this spec wins.

**Single owner.** This spec owns the shell. That is the bar, the sidebar shape,
Home, the command bar, the dock and the bell. It also owns the avatar menu,
role presets and the app manifest.

`launch_surface.md` keeps owning WHICH panes are live.
`DESIGN_SYSTEM.md` keeps owning how a surface looks. Each app's own spec keeps
owning what happens inside the app.

---

## 0. The answer in one screen

The shell gets six parts that every app shares. Each app plugs into them
through one manifest, and no app builds its own copy.

1. **One shell bar.** It holds the app title, the scope chip and the command
   bar. It also holds the app's own buttons, the bell, the dock toggle and the
   avatar. It takes one row, the height of today's app bar.
2. **My Day at `/`.** The personal rollup that Personal Center lacks today. It
   replaces the "Welcome back" grid, which repeats the sidebar with no data.
3. **One Home at three altitudes (D87).** Personal, a team, and all my teams.
   One scope chip moves between them. A team view is a filter of one page. It
   is never a Center destination.
4. **One command bar (D88).** It is always visible. It answers in three tiers:
   the registry at once, then record search, then AI. Tiers 0 and 1 are free.
   Tier 2 is metered at the cheapest tier and shows no price.
5. **One assistant dock and one bell.** The three per-app assistant rails
   become one dock. The Projects bell becomes the shell's "Needs you".
6. **One app manifest (D89).** Every app, built or future, declares its team,
   purpose, jobs, search, "needs you" items, Home cards and agent. The shell
   reads the manifest. An app that builds its own palette, bell, dock or ⌘K
   handler fails a fence.

---

## 1. Scope and non-goals

### 1.1 In scope

- The shell bar and the slots an app fills in it (§3.1).
- The sidebar shape and the avatar menu (§3.2, §3.3).
- Home at three altitudes, and the jobs of the Personal Center apps (§4).
- The app manifest and its rules for every app (§5).
- The command bar, its three tiers and its metering (§6).
- The assistant dock and the bell (§7).
- Role presets, pins, Desk mode and the first sign-in question (§8).
- The phone shell (§9).

### 1.2 Non-goals — named so nobody builds them from this file

- **No new app.** CRM, Finance and the other team apps keep their own specs.
  This spec gives them a place to plug in.
- **No change to grants or features.** `preview` is still not a permission
  (`launch_surface.md` §3). A preset, a pin or an altitude never grants or
  denies anything.
- **No Center destination.** D49 stands. §4.2 lists the rules that keep a team
  view from turning into one.
- **No second look.** New parts use `src/components/ui/` and the tokens.
  `DESIGN_SYSTEM.md` binds them.
- **No second task store.** My Day reads the one lens (D53).
- **No model picker.** The customer never chooses a model (D32.7).
- **No second chat seam.** The dock mounts the shared `AgentChat`.
- **No change to an app's search routes.** Only the palettes in the UI merge.
- **No `/dashboard` build.** That surface is WS-15, parked by D49.

---

## 2. What exists today — measured 2026-10-05

| Surface | Today | Anchor |
|---|---|---|
| Root page `/` | "Welcome back". It shows the nav panes as cards with no data. It already reads `visibleSections` | `src/app/page.tsx:20-29` |
| `/dashboard` | A `ComingSoon` placeholder, and a `preview` pane | `src/app/dashboard/page.tsx` |
| Desktop top bar | None in the shell. `AppShell` renders the sidebar and the page | `src/components/AppShell.tsx:158` |
| App bar | `AppTopBar`, used by Projects and My Tasks. It holds "Search every project (⌘K)" | `src/components/AppTopBar.tsx` |
| Search | Three palettes. Projects has `SearchPalette` and the command list `app/projects/lib/commands.ts`. My Tasks mounts the Projects palette. Email has its own `CommandPalette`. Three pages each hold a ⌘K listener | `app/projects/page.tsx:2635` · `app/tasks/page.tsx:180` · `app/email/page.tsx:678` · the key test `app/projects/lib/search.ts:143` |
| Bell | `NotificationBell` belongs to Projects. Projects and My Tasks mount it. Approvals has no badge | `app/projects/components/NotificationBell.tsx` |
| Assistant | Three rails on the shared `AgentChat`: `task-manager`, `projects-assistant` and `email-assistant` | `app/tasks/components/AssistantRail.tsx` · `app/projects/components/AssistantRail.tsx` · `app/email/components/EmailAssistantChat.tsx` |
| Personal rollup | None. No spec defined its content before this one | — |
| Live panes | **Ten**, in four sections. Personal Center holds My Tasks, Calendar, My Profile and Email. My Access left the sidebar on 2026-10-05, and it is a tab of the People app | `launch_surface.md` §2 · `src/lib/nav.ts` · `nav.test.ts` |
| Who may see a team | `/auth/me` returns no groups. `reader_scope` and `subject_choices` already answer which people and teams a member may see, for the Reports app (`projects_reports.md` §7.1) | `routes/admin/me.py:197-228` · `routes/projects/report_scope.py:148`, `:332` |
| Per-member settings | `user_settings`, read and written by `GET` and `PUT /tasks/settings`. That router requires `feature:tasks` | `infra/postgres/51_gtd_settings.sql` · `routes/tasks/settings.py:355` · `routes/tasks/core.py:41-44` |
| First run | `WelcomeDialog`, mounted by `AppShell` | `AppShell.tsx:167` |
| Calendar header | Its own `h1`, outside both shapes of `DESIGN_SYSTEM.md` §6a | `app/calendar/CalendarView.tsx:400` |
| Projects by team | D22's grouping is not built. Only the `?center=` filter exists | `app/projects/lib/tree.ts` `filterByCenter` |

**The pattern behind the gaps.** In each gap, one app solved a shell problem
alone. Projects built a palette and a bell, and Email built a second palette.
Three apps built three assistant rails. No app was wrong, because the shell
had no seam to offer them.

---

## 3. The shell

### 3.1 The shell bar

One row across the top of every desktop surface. Its height is `h-10`, the
height of today's app bar, so no app loses height.

| Slot | Owner | What it holds |
|---|---|---|
| Title | The app | The rail toggle and the app's name. The app keeps its one `h1` |
| Scope chip | The shell | Personal, a team, or all my teams (§4). A team app shows its own team, locked |
| Command bar | The shell | Search, do or ask (§6). It sits in the centre and takes the free width |
| App actions | The app | The app's own buttons, such as Capture in My Tasks or the view switch in Projects |
| New | The shell | The jobs of the apps the member holds, in preset order (§8) |
| Bell | The shell | "Needs you", from every app (§7.2) |
| Assistant | The shell | The dock toggle (§7.1) |
| Avatar | The shell | The avatar menu (§3.3) |

An app fills its slots through one React context in `src/lib/shell/`. The
ticket names it. An app does not render a bar of its own. A page body may
still open with a `PageHeader`, because the bar is app scope and the header
is page scope (`DESIGN_SYSTEM.md` §6a).

### 3.2 The sidebar

Five groups, in this order:

1. **My Day.** Home, at whichever altitude the member last chose.
2. **Personal Center.** My Tasks, Calendar and Email. Notes and WhatsApp join
   when they go live.
3. **My apps.** The member's pins. A preset sets the first pins (§8).
4. **Across teams.** Projects, and Dashboards when it goes live.
5. **All apps.** One button at the foot. It opens the launcher.

The launcher lists every app the member holds, grouped by team. Each tile
shows the name and the manifest's one-line purpose. A star pins or unpins.

**Admin panes leave the sidebar.** Approvals and Organisation appear in the
launcher under Admin, and in the avatar menu, for a member who holds them.
Approvals' items reach every approver through the bell and My Day.
Appearance is a personal preference, so it moves to every member's avatar
menu.

⚠️ **The live set does not change.** §2 of `launch_surface.md` still lists
ten live panes, and `nav.test.ts` still counts ten. This spec changes
WHERE a live pane renders, not WHETHER it is live. Promotion stays an owner
decision (**H-21**).

### 3.3 The avatar menu

My Profile, My access, Appearance, the colour-mode toggle and sign-out. For
an admin it adds Organisation and Approvals. My Profile and My access describe
the member. The member does not work in them, so they leave the sidebar.

**My Access moved first, on 2026-10-05.** The owner moved it into the People
app, as the ungated "My access" tab at `/people/access`, beside My profile. When
NS-2 builds the avatar menu, the menu links to that tab. It holds no second copy
of the page.

---

## 4. Home — one page at three altitudes (D87)

### 4.1 The three altitudes

| Altitude | Page title | Who sees it | Default for |
|---|---|---|---|
| **Personal** | My Day | Every member | Most members |
| **A team** | The team's name | A team that `reader_scope` allows: a team the member leads, or every team for a reader of HR fields | A team lead |
| **All my teams** | "Company" when `reader_scope` returns `everyone`, otherwise "All my teams" | A member whom `reader_scope` allows two teams or more | The founder |

**One rule decides who sees a team, and it already exists.** `reader_scope`
in `routes/projects/report_scope.py` is the rule the Reports app uses, and the
shell reads it. The chip never offers a team and then refuses it
(`projects_reports.md` §7.1 rule 1). D14 says that nothing may rely on
`data:org:read`, and this spec does not use it.

**A plain member of a team gets the personal altitude only.** That is the
same rule as Team pulse. To widen it is a change to `projects_reports.md`
§7.1, never a second rule in the shell.

**One chip, one meaning.** The first value of the chip is "Personal", the same
word as the sidebar section. Personal Center is the section of apps about the
member, and it is the personal altitude of Home.

### 4.2 Five rules that stop a team view growing back into a Center

D87 keeps the team view. These five rules are the price of keeping it.

1. **No route and no nav entry per team.** The scope rides on `/` as the query
   value `scope=<slug>`, so Back and a shared link work.
2. **No page built for one team.** A team view renders only the cards that apps
   declare. No team gets its own layout or its own apps.
3. **No rule of its own.** The team view reads `reader_scope`. It never
   checks a `center.*` feature.
4. **Nothing the member could not already open.** A card never shows a row
   that its owning app would refuse the member. A private task stays private.
5. **`/centers/<slug>` stays unlinked.** A later release may redirect it to
   `/?scope=<slug>`. That change follows R6: add the redirect first, retire the
   route later.

**Fence (NS-5).** `src/lib/nav.test.ts` fails if a `live` pane's `href`
starts with `/centers/`. The six `preview` Center panes stay (D49). A new `src/lib/shell/home.test.ts` fails if a
Home file calls a `center.` feature check. A gateway test drives rule 4 with a
private task of another member and asserts that no card returns it.

### 4.3 Personal Center — three apps, three jobs

My Tasks and Calendar already read the one task store. My Day joins them at
the top. The risk is a third view of the same tasks, so each app gets one job.

| App | Its job | The question | What it may change | What it hands on |
|---|---|---|---|---|
| **My Day** | Notice | "What needs me, across every app?" | One-click acts only: done, approve, snooze. A longer act opens the owning app | Planning goes to Calendar. Lists go to My Tasks |
| **My Tasks** | Commit | "What have I promised, and what is next?" | Every task field, every list, capture | Time goes to Calendar |
| **Calendar** | Place | "When will I do it?" | The schedule, focus blocks, the start and the end of the day | What to do goes to My Tasks |
| **Email** | Reply | "Who waits for my answer?" | Its own threads | It feeds My Day through its manifest |

**No second ritual.** Calendar has `StartupRitual`. The first open of My Day
each day offers that ritual. My Day does not copy it.

### 4.4 Cards

An app declares its cards in its manifest (§5.1). Each card names the
altitudes it supports.

- **A card is a client component the app exports.** It reads the app's own
  existing route and passes the scope value. A card adds a new route only
  when its app has no read that answers the question.
- **A card reads through `useCachedResource`** and shows `Skeleton` on a true
  miss (`AGENTS.md` rule 9).
- **A card names its source app** in its header, and links to that app at the
  same scope.
- **The member may hide and reorder cards.** The order is stored with the pins
  (§8.2).

### 4.5 The first cards — built from reads that exist today

| Card | Altitudes | Source | Read |
|---|---|---|---|
| Today | Personal | Calendar | The lens, `scheduledStart` and `scheduledEnd` |
| Next actions | Personal | My Tasks | The lens, `/projects/my/*` |
| Needs reply | Personal | Email | `getDigest`, `app/email/lib/api.ts:2443` |
| Needs you | Personal | Every app | `GET /shell/needs` (§7.2) |
| Team pulse | Team, all my teams | Projects | Report template T1, "Team pulse" (`projects_reports.md`) |
| At-risk work | Team, all my teams | Projects | The analytics reads under `projects_ai_chat.md` §13 |
| Out today | Team | People | `people_absences` |
| Waiting for you | Personal, for an approver | Approvals | `pending_actions` |

**No second arithmetic.** A team card draws a number that a Projects read or a
report section already computes. The shell computes nothing.

**The all-teams view is honest about what exists.** Until Finance, CRM and the
other team apps go live, it shows Projects, People and Approvals only. A
placeholder card for an app that is not live is a defect.

---

## 5. The app manifest — the contract every app follows (D89)

### 5.1 The fields

The manifest extends `NavPane` in `src/lib/nav.ts`. One registry stays one
registry.

| Field | Type | Required | Who reads it |
|---|---|---|---|
| `href`, `label`, `launch`, `feature` | as today | yes | Every surface, through `visibleSections` |
| `team` | `"personal"`, `"across"`, `"admin"`, `"studio"`, or a group slug | yes | The launcher, the scope chip |
| `blurb` | one sentence, 60 characters at most, in job words | yes | The launcher, the command bar |
| `keywords` | a list of words | no | The command bar, tier 0 |
| `jobs` | a list of `{ id, label, keywords, href, fields }` | yes for a live app | The command bar, New, the dock |
| `search` | the name of a server search provider | no | The command bar, tier 1 |
| `needs` | the name of a server needs provider | no | The bell, My Day |
| `cards` | a list of `{ id, title, altitudes, component }` | no | Home |
| `agent` | an agent slug | no | The dock |

**A job opens a form.** `href` is the form's route. `fields` names the query
values the form reads, so tier 2 may fill them (§6.6). A job never writes when
it opens.

**Jobs live in one JSON file**, `src/lib/shell/jobs.json`. `nav.ts` imports it,
and the gateway reads the same file. Each job names the feature it needs, so
the gateway filters jobs by the member's own features. The gateway never
trusts a job list from the client (R5e). A test reads the file from both
sides, so no mirror can drift.

### 5.2 The rules for every app, built or future

1. **An app joins the shell through its manifest.** It never edits the
   sidebar, the launcher or Home by hand.
2. **An app mounts no ⌘K handler, palette, bell or assistant dock.** It
   declares jobs, search, needs and an agent, and the shell renders them.
3. **An app renders inside the shell bar through slots.** It draws no top bar
   of its own.
4. **A team app declares its team. A cross-team app reads the scope chip** and
   passes the value to its own routes. The server checks the grant on every
   read. The chip's value is never trusted (R5e).
5. **A job opens a form. A person saves it.** Tier 2 may fill the form. It
   never saves.
6. **A server provider runs on the request's session at the `get_db()` seam.**
   No provider opens its own connection (R5b).
7. **A provider calls its app's own authorized function.** It never writes a
   visibility predicate of its own. The app already decides who may see a
   row, and a second copy of that rule is a defect.
8. **A `preview` app may carry a manifest.** Nothing renders it until the
   owner promotes the app (**H-21**).

**Fence (NS-1, NS-2).** `src/lib/nav.test.ts` fails on a live pane with no
`team` or `blurb`, and on a job whose `href` is no route in the tree. A new
`src/lib/shell/seams.test.ts` sweeps `src/` and fails on these four:

- a `metaKey` or `ctrlKey` handler for `k` outside `src/lib/shell/`
- an import of `NotificationBell` outside the shell
- a mount of `AgentChat` as a rail outside the dock and `/chat`
- a `SearchPalette` or `CommandPalette` mount outside the shell

The sweep starts with a baseline of today's sites. The baseline only goes down,
the same ratchet as `conformance.test.ts`.

### 5.3 How each app that exists today conforms

| App | `team` | Jobs | `search` | `needs` | Cards | `agent` | Work owed |
|---|---|---|---|---|---|---|---|
| My Tasks | personal | New task, Capture | `GET /projects/search` (`routes/projects/search.py:221`). `app/tasks/lib/searchHit.ts` decides where a hit opens | due today, overdue | Next actions | `task-manager` | Stop mounting the Projects palette and bell |
| Calendar | personal | Block focus time | — | — | Today | `task-manager` | Move its `h1` into the title slot |
| Email | personal | Write an email | email search | needs reply | Needs reply | `email-assistant` | Its palette commands become jobs. Its ⌘K handler goes. Its `text-sm` heading drift (`app/email/page.tsx:963`) closes when it adopts the bar |
| Projects | across | New task, New project | projects search | `pm_notifications` | Team pulse, At-risk work | `projects-assistant` | Its palette and bell move to the shell. `lib/chatDock.ts` becomes the dock's rule. Its tree groups by team (D22) |
| People | people | Request leave | the directory | — | Out today | — | None beyond the manifest |
| My Profile | personal | — | — | — | — | — | Move to the avatar menu |
| My Access | personal | — | — | — | — | — | Done 2026-10-05: a People tab at `/people/access`. NS-2's avatar menu links to it |
| Chat | studio | New chat | chat sessions | — | — | any | `/chat` stays. The dock shares its sessions |
| Approvals | admin | — | — | `pending_actions` | Waiting for you | — | Its items feed the bell |
| Organisation | admin | Invite a member | members | seat requests | — | — | Moves to the avatar menu |
| Appearance | personal | — | — | — | — | — | Moves to every member's avatar menu |

A `preview` app writes its manifest in the pull request that promotes it.

### 5.4 What a future app gets for free

A new team app, such as Invoices in a Finance team, writes one manifest. Then:

- it appears in All apps under its team, with its purpose
- its jobs answer in the command bar and in New
- its records answer in tier 1, if it names a search provider
- its approvals and its waiting items reach the bell and My Day
- its cards appear on Home at the altitudes it names
- the dock opens its agent inside the app

Nobody edits navigation, and no team gets a separate home.

---

## 6. The command bar (D88)

### 6.1 Place and keys

The command bar is always visible in the shell bar. `Ctrl K`, `⌘K` and `/`
focus it from any surface. The shell holds the only listener (§5.2 rule 2).

### 6.2 What it returns

Four groups, in this order. A group with no result does not show.

| Group | What it holds | Tier |
|---|---|---|
| **Do** | Jobs that match the words | 0 |
| **Go to** | Apps and views the member holds | 0 |
| **Find** | Records from each app's search provider | 1 |
| **Ask** | An AI answer, a filled job, or a hand-off to the dock | 2 |

With an empty query, the bar shows recent items, the preset's first jobs and
six example questions.

### 6.3 Three tiers

| Tier | Budget | Where it runs | What it costs |
|---|---|---|---|
| **0 — the registry** | 50 ms | In the browser | Nothing. No server call |
| **1 — record search** | 300 ms | One gateway route, `GET /shell/search`, which calls each provider in turn on the request's session | Nothing. No model |
| **2 — AI intent** | 1.5 s | `POST /shell/intent`, through the Router | Credits, metered (§6.5) |

Each tier adds results under the ones already shown. A tier never moves a
result that the member can already see.

**Tier 2 runs only when one of these is true:**

- the query holds two words or more, and the member paused for 900 ms
- the member pressed `Enter` and no tier 0 or tier 1 result was selected

### 6.4 Six rules

1. **The model sees only what the member can open.** The shell filters the
   registry by grants first, then sends that list to the model.
2. **A suggestion is not an act.** A job opens its form, filled in, and each
   filled field says "filled by AI". The member saves. A larger act goes to the
   dock, as the `confirmation` card of `generative_ui_2.md` §4.
3. **Context is a visible token.** Inside an app, the bar shows "in CRM", and
   that app's results rank first. `Backspace` on an empty query removes it.
4. **Every answer has a way out.** "Continue in assistant" opens the dock with
   the question and the answer. The member never retypes.
5. **It learns the member, not the company.** Recent and frequent jobs rank
   higher for each member. The ranking lives in the shell column of
   `user_settings` (§8.2).
6. **Scope is part of the question.** At the Sales altitude, "overdue
   invoices" means Sales customers. The answer names the scope it used.

### 6.5 Metering (D88)

- **Tiers 0 and 1 are free.** They call no model.
- **Tier 2 is metered** at the cheapest chat tier the operator sets. Today
  that tier is `tier-fast`. The shell declares it as its default tier, the same
  way an app does under D-AI-4. The customer never picks it (D32.7).
- **The bar shows no price and no counter.** A member never weighs the cost of
  one search.
- **When the balance runs out, tier 2 stops.** The stop is the CP-6 balance gate, under `CUSTOMER_CONSOLE_SPEND_GATE`.
  Tiers 0 and 1 keep working. The bar shows one line: "AI suggestions are
  paused. Ask an admin to add credits."
- **A repeat is free.** The same query, at the same scope, by the same member,
  within 5 minutes returns the cached answer. The cache key holds the member,
  because two members hold different grants. The key goes through the
  tenant-prefix wrapper (R5c).

⚠️ **Tier 2 waits on two things outside this spec.** **H-171**: the product's
own AI is not metered today. **H-69**: `ROUTER_SERVING_ENABLED` is an owner
flip. NS-4b builds tier 2 dark, and it stays dark until both are done.

### 6.6 What tier 2 may return

| Kind | Shape | What the bar does |
|---|---|---|
| `job` | the job id and the filled fields | Shows the fields, and opens the form on a click |
| `answer` | a short text and its sources | Shows the text, names each source app, offers "Continue in assistant" |
| `workflow_draft` | a trigger, a step list and the recipients | Opens Workflows with a draft, which runs nothing until a person turns it on |
| `handoff` | nothing | Opens the dock with the query |

An `answer` reads data only through the read tools that the app's agent
already holds, such as the `skill-projects` reads. Tier 2 adds no data path.
NS-4b ships `job` and `handoff`. NS-4c adds `answer` and `workflow_draft`.

---

## 7. The assistant dock and the bell

### 7.1 One dock

- **One panel on the right**, `w-[380px]`, the side-panel width of
  `DESIGN_SYSTEM.md` §6a. Below 1280 px there is no dock, and the toggle opens
  the chat in the full slot. That is the rule `app/projects/lib/chatDock.ts`
  holds today.
- **The agent follows the app.** The dock opens the manifest's `agent`. On
  Home, or in an app with no agent, it opens the agent that `/chat` opens by
  default.
- **Sessions stay shared with `/chat`.** The dock mounts the shared
  `AgentChat`. It adds no chat seam.
- **The confirm cards do not change.** Projects chat's cards work in the dock
  as they work in the rail today.

### 7.2 One bell

- **One feed, `GET /shell/needs`.** It merges each app's needs provider and
  returns one count and one list. My Day's "Needs you" card reads the same
  feed.
- **Each row names its app.** A one-click act runs in place. Any other act
  opens the owning app.
- **The stores stay.** `pm_notifications` keeps its read and unread rules.
  `pending_actions` keeps its own state. The bell reads both and owns neither.
- **The first providers, and the check each one reuses:**
  - Projects: `pm_notifications`, through the Projects notification read.
  - Approvals: `pending_actions`, only for a member who passes the gate of
    `routes/actions.py`. The table has no approver column, so any other
    member gets no row.
  - Email: needs reply, only for the accounts the member owns, through the
    owner check of the email routes.
  - My Tasks: due today and overdue, through the lens.

---

## 8. Presets, pins, Desk mode and the first sign-in

### 8.1 Presets

A preset is data. It sets the starting altitude, the first pins, the card
order, the order of New and the dock's agent on Home. **A preset never grants
or denies.** Every item in a preset passes through `visibleSections` before it
renders.

The first version ships eight fixed presets in `src/lib/shell/presets.ts`.
That is the default answer to Q5. The member's preset comes from the first
sign-in question. Without an answer, the role picks it:

| Role | Default preset |
|---|---|
| `owner` | Founder: all my teams, pins from the teams the member holds |
| `admin` | Founder |
| `manager` | Team lead: the member's first group |
| `member` | Personal |
| `guest` | Personal, with no pins |

The eight presets: Founder, Sales manager, Marketing lead, Finance manager,
Operations manager, Engineer, Accounts assistant (Desk mode) and New hire. The
design record holds the table of what each one pins.

**Fence (NS-7).** `src/lib/shell/presets.test.ts` takes every preset and
several sets of grants, from none to partial to full. For each pair, it
asserts that what renders is a subset of `visibleSections` for those grants.

### 8.2 Pins

- One new nullable column on `user_settings`, `shell_prefs`. It holds the
  preset, the pins, the card order, the hidden cards and the job ranking.
  Take the migration number at build time (R1).
- It is nullable with no default value. A null means "use the preset" (R6).
- **Its own route, `GET` and `PUT /auth/me/shell`.** `/tasks/settings`
  requires `feature:tasks`, and a guest does not hold it. The shell route
  needs only a signed-in member, the same as `/auth/me`. It reads and writes
  the one `user_settings` row, so it adds no store. A `PUT` of null resets
  the member to the preset.
- **If the read fails, the shell uses the preset.** It never blanks the
  sidebar (`launch_surface.md` §8.2).
- `user_settings` is already tenant-scoped, so `test_tenant_coverage.py`
  already covers it (R5a).
- The ticket runs the migration and the route against a real Postgres (R8).

### 8.3 Desk mode

A preset flag for data-entry staff. Home becomes one app's queue and its form.
The sidebar hides All apps, which is the default answer to Q6. The command bar
stays, so "Request leave" is still one search away.

⚠️ **Desk mode waits for a live data-entry app.** None is live today. NS-8 is
blocked until one is.

### 8.4 The first sign-in question

`WelcomeDialog` already mounts in `AppShell`. It asks one question, "What will
you do most here?", with six answers. The answer picks a preset. The member may
skip it. The question changes the starting layout and nothing else.

---

## 9. The phone

Today the phone shell has no top bar. A bottom nav changes its tabs per app,
and a Menu tab opens a drawer (`AppShell.tsx:186-236`).

- **Keep the per-app bottom tabs.** They work, and each app tuned them.
- **Add one row at the top of every screen**: the command bar and the scope
  chip.
- **The drawer takes the sidebar shape of §3.2.** My Day comes first.
- **The dock opens as a full-screen sheet.**

---

## 10. From today to the target

| Today | Target | Ticket |
|---|---|---|
| `/` "Welcome back", a grid with no data | My Day at the personal altitude | NS-3 |
| `/dashboard` "coming soon" | The all-teams altitude of Home. `/dashboard` stays for dashboards people build (WS-15) | NS-5 |
| Search, bell and assistant inside each app's `AppTopBar` | One shell bar. `AppTopBar` becomes the title and actions slots | NS-1 |
| Three palettes | One command bar. `app/projects/lib/commands.ts` seeds the job list | NS-1, NS-2 |
| The Projects `NotificationBell`, and no Approvals badge | The shell's "Needs you" | NS-6 |
| Three assistant rails | One dock | NS-6 |
| My Profile and Appearance in the sidebar | The avatar menu | NS-2 |
| My Access in the sidebar | ✅ The People app's "My access" tab, 2026-10-05 | — |
| Calendar's own header | The shell bar's title slot | NS-1 |
| Projects shows "every space you can see" | Grouped by team (D22), driven by the scope chip | NS-5 |

---

## 11. Tickets

Every ticket ships dark behind its flag, default off. With a flag off, every
surface renders as it does today, because a merge to `main` is a deploy. Each
flag stays off in production until the owner turns it on (§13). To build is
AGENT-SAFE. To turn any flag on in production is OWNER-GATE.

### NS-1 · The shell bar and the one ⌘K listener — AGENT-SAFE

Flag `NEXT_PUBLIC_SHELL_BAR`. Files: `src/components/AppShell.tsx`,
`src/components/AppTopBar.tsx`, a new `src/lib/shell/`, and the pages of
Projects, My Tasks, Calendar and Email.

Done when:

1. With the flag on, `AppShell` renders the shell bar on every desktop route.
2. Projects, My Tasks, Calendar and Email fill the title and actions slots, and
   draw no bar of their own.
3. The command bar answers tier 0 from the registry and from
   `app/projects/lib/commands.ts`.
4. `src/lib/shell/seams.test.ts` exists, with a baseline of today's sites.
   With the flag on, only the shell's ⌘K listener fires. With the flag off,
   the three listeners at `app/projects/page.tsx:2635`, `app/tasks/page.tsx:180`
   and `app/email/page.tsx:678` run as today. NS-9 deletes them.
   Two tests pin the My Tasks listener today, `app/tasks/lib/searchHit.test.ts`
   and `app/tasks/lib/shortcuts.test.ts`. NS-1 keeps them green with the flag
   off, and NS-9 rewrites them.
5. With the flag off, every surface renders as it does today. A test pins the
   nav order and the bar's absence.
6. The visual review of `DESIGN_SYSTEM.md` §8 ran: light mode, compact density,
   a changed accent, and the neighbouring app.

### NS-2 · The manifest, All apps, the sidebar shape and the avatar menu — AGENT-SAFE

Flag `NEXT_PUBLIC_SHELL_NAV`. Files: `src/lib/nav.ts`, `src/lib/nav.test.ts`,
`src/components/Sidebar.tsx`, and a new launcher and avatar menu in
`src/lib/shell/`.

Done when:

1. `NavPane` carries the §5.1 fields, and every live pane has `team`, `blurb`
   and one job or more.
2. `nav.test.ts` fails on a live pane with no `team` or `blurb`, and on a job
   whose `href` is no route.
3. The launcher renders `visibleSections(features, isAdmin)` and nothing else
   (`launch_surface.md` §8.4).
4. The live count in `nav.test.ts` is still ten.
5. With the flag on, the sidebar takes the §3.2 shape, and the avatar menu holds
   the §3.3 items.

### NS-3 · My Day and the needs feed — AGENT-SAFE (build), OWNER-GATE (turn on)

Flag `NEXT_PUBLIC_MY_DAY`. Files: `src/app/page.tsx`, the card components each
app exports, and `gateway/routes/shell/needs.py` with its providers.

Done when:

1. With the flag on, `/` renders My Day with the personal cards of §4.5.
2. `GET /shell/needs` merges the four providers of §7.2 on the request's
   session.
3. Each of the four providers has its own negative test. Another member's
   private task, notification, pending action and mail thread reach no card
   and no needs row. A member without the approvals gate gets no pending
   action.
4. The needs SQL ran against a real Postgres (R8), and the run is in the pull
   request.
5. With the flag off, `/` renders "Welcome back" as it does today.

### NS-4a · Tier 1, record search — AGENT-SAFE

Flag `NEXT_PUBLIC_SHELL_BAR`. Files: `gateway/routes/shell/search.py` and one
provider per app that has search today.

Done when:

1. `GET /shell/search?q=&scope=` returns grouped records from each provider.
2. Every provider runs on the request's session, and calls its app's
   existing search function. No provider opens a connection, and no
   provider writes its own visibility predicate.
3. Each provider has its own negative test: a record the member cannot read
   never returns.
4. The route ran against a real Postgres (R8).

### NS-4b · Tier 2, filled jobs and the hand-off — AGENT-SAFE (build), OWNER-GATE (turn on)

Flag `COMMAND_BAR_AI` on the gateway, default off. Files:
`gateway/routes/shell/intent.py`.

Done when:

1. `POST /shell/intent` returns `job` or `handoff` (§6.6) through the Router.
2. The prompt holds only the jobs the member can open. The server filters
   `jobs.json` by the member's own features. One test asserts that a job
   behind a missing feature is absent. A second test asserts that the server
   ignores a job list from the client.
3. Each call writes one usage row, and a test asserts it.
4. At the hard cap, the route returns the paused state, and tiers 0 and 1 keep
   working.
5. The cache key holds the member and the scope, and goes through the
   tenant-prefix wrapper.

### NS-4c · Tier 2 answers and workflow drafts — AGENT-SAFE (build)

Flag `COMMAND_BAR_AI`, the same flag as NS-4b. It waits on NS-4b.

Done when:

1. `POST /shell/intent` also returns `answer` and `workflow_draft` (§6.6).
2. An `answer` reads data only through read tools that the app's agent
   already holds. A test asserts that the route imports no SQL of its own.
3. Each `answer` names its source apps, and a test asserts that a source
   the member cannot open never appears.
4. A `workflow_draft` opens Workflows with a draft that is off. A test
   asserts that no workflow runs.

### NS-5 · The scope chip and the team altitudes — AGENT-SAFE

Flag `NEXT_PUBLIC_SHELL_SCOPE`. Files: a new `GET /shell/scopes` in
`gateway/routes/shell/`, `routes/projects/report_scope.py` (read only), the
chip in `src/lib/shell/`, the team cards, and the Projects tree.

Done when:

1. `GET /shell/scopes` returns the teams that `reader_scope` allows, and
   whether it returns `everyone`. It adds no rule of its own. `/auth/me` does
   not change.
2. The chip lists Personal, each of those teams, and the top altitude when
   `reader_scope` allows two teams or more. If the read fails, the chip shows
   Personal only, and the page still renders.
3. The route ran against a real Postgres (R8).
4. The five rules of §4.2 hold, and their fences exist.
5. Projects groups its tree by team, from the chip's value (D22).
6. The server checks the grant on every scoped read. A test sends a scope the
   member does not hold and gets nothing back.

### NS-6 · One dock and one bell — AGENT-SAFE

Flag `NEXT_PUBLIC_SHELL_DOCK`. Files: a new dock in `src/lib/shell/`, the three
rails, `AssistantToggle`, and `NotificationBell`.

Done when:

1. The dock opens the manifest's agent, and shares sessions with `/chat`.
2. With the flag on, the dock replaces the three rails, and the bell reads
   `GET /shell/needs`.
3. With the flag off, the three rails and `NotificationBell` render as today.
   NS-9 deletes them.
4. The Projects confirm cards pass their tests inside the dock.

### NS-7 · Presets, pins and the first sign-in — AGENT-SAFE

Flag `NEXT_PUBLIC_SHELL_NAV`. Files: `src/lib/shell/presets.ts`, one migration
(number taken at build time), `GET` and `PUT /auth/me/shell` in
`routes/admin/me.py`, and `WelcomeDialog`.

Done when:

1. The eight presets exist, and `presets.test.ts` proves the subset rule of
   §8.1.
2. `shell_prefs` is nullable with no default, and `/auth/me/shell` reads and
   writes it. A guest with no `feature:tasks` can save a pin.
3. The migration and the route ran against a real Postgres (R8).
4. `WelcomeDialog` asks the question, and the answer sets the preset.

### NS-9 · Retire what the shell replaced — AGENT-SAFE, after the owner flip

It starts when the NS-1 and NS-6 flags have been on in production for 7 days.

Done when:

1. The two app ⌘K handlers, the three rails, the `NotificationBell` mounts and
   the per-app palette mounts are gone.
2. Every row of the `seams.test.ts` baseline is zero.
3. The flags of NS-1 and NS-6 are deleted from the code.

### NS-8 · Desk mode — BLOCKED (AGENT-SAFE when unblocked)

It waits for a live data-entry app. When one exists, its spec names the queue,
and this ticket gains acceptance.

### Order

NS-1 → NS-2 → NS-3 → NS-4a → NS-5 → NS-6 → NS-4b → NS-4c → NS-7 → NS-9 →
NS-8.
NS-1 to NS-3 change nothing about AI or cost. They give the product its shell
first.

---

## 12. Verification commands

```bash
# Frontend — the whole gate
cd workbench/control_plane && npx tsc --noEmit && npx vitest run

# The fences this spec adds, by name
npx vitest run src/lib/nav.test.ts src/lib/shell/ src/lib/theme/

# Gateway — needs a real database (R8)
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_shell_needs.py tests/unit/test_shell_search.py tests/unit/test_shell_intent.py -q
uv run pytest tests/unit/test_tenant_coverage.py -q

# This spec's prose
node .claude/hooks/ste-lint.mjs project-docs/specs/navigation_shell.md
```

The visual check runs through the `visual-review` skill, in light mode, at
compact density, under a changed accent, and beside the neighbouring app.

---

## 13. Owner gates and open questions

### 13.1 Gates — refuse these by name

| Act | Why it is the owner's | Gate id |
|---|---|---|
| Turn on any `NEXT_PUBLIC_SHELL_*` flag or `NEXT_PUBLIC_MY_DAY` in production | It changes what every customer sees on day one. The flags are build-time, so one flip reaches every organization at once, and it answers Q2 for all of them | Owner-only. The dev-phase window does NOT open it, and `enforcement-flip` does not cover it |
| Turn on `COMMAND_BAR_AI` in production | It spends customer credits | Owner-only. The window does NOT open it (CLAUDE.md §3a rule 3) |
| Promote a `preview` app so its manifest renders | It is the decision "this is finished enough to sell" | **H-21** |

⚠️ **These two rows bind by prose only.** The `enforcement-flip` rule of
`.claude/hooks/plan-guard.mjs` names eight fixed flags and none of these.
A guard rule needs the `guard-write` grant. Until somebody adds one, this
table is the only fence (R7, advisory).

### 13.2 Taken on 2026-10-05

- **Q1 → D87.** Keep the team view, as the middle altitude of one Home, with
  the five rules of §4.2. The owner gave this call to the design.
- **Q3 → D88.** Tiers 0 and 1 are free. Tier 2 is metered at the cheapest tier
  and shows no price. The owner accepted the recommendation.

### 13.3 Still open — each has a default an agent builds to

| # | Question | Default |
|---|---|---|
| Q2 | Does `/` become My Day for every member? | Yes, in NS-3, behind its flag |
| Q4 | Does the shell bar take one shared row with each app's bar? | Yes. One row, merged in NS-1 |
| Q5 | Fixed presets, or an editor for each organization? | Eight fixed presets first. Add the editor when a second customer asks |
| Q6 | Does Desk mode hide All apps? | Yes. The command bar stays |

HANDOFF holds one owner entry for these four.

---

## 14. Where the rest of the documentation points here

Each document below links here and adds nothing of its own.

- `work_plan.md` §2 row **WS-44**, §3 **D87** to **D89**, and §4's registry row.
- `INDEX.md`, ACTIVE.
- `launch_surface.md` §2 and §8.4: the live set is unchanged, and every shell
  surface reads the one filter.
- `department_centers.md`, its D49 banner: D22's three surfaces return as the
  three altitudes of Home.
- `workbench/control_plane/DESIGN_SYSTEM.md` §6a: the shell bar.
- `workbench/control_plane/AGENTS.md` rule 10: an app plugs into the shell and
  never builds one.
- Root `CLAUDE.md` §2: one bullet.
