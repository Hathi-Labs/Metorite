# The shell — how a member finds an app, a job or an answer

**Status:** Specified 2026-10-05. Built so far: NS-1 slice 1, NS-2 slices 1
and 2, NS-3 slices A and B, NS-4a, NS-4b, NS-10, NS-10b and NS-11. The shell bar is ON in production
since 2026-10-08. NS-2 slice 1 is ON in production since 2026-10-09
(`NEXT_PUBLIC_SHELL_NAV=1`, owner decision). Board row **WS-44**.

NS-2 slice 2 (2026-10-09) adds three jobs and the job route fence. It rides
the shell bar flag, which is on.

**BUILT 2026-10-09, by owner direction:** the shell bar spans the full width
and carries the organization's logo (§3.1). Organisation is an app in Admin,
and it left the account menu (§3.2a item 4). Both ride the two shell flags,
which are on in production.

**BUILT 2026-10-10, by owner direction:** the shell bar is constant, and an
app never renders into it. Every app opens with its own title bar,
`AppTopBar`, under the shell bar (§3.1, §5.2 rule 3). This reverses NS-1's
merged row and the default of §13.3 Q4. It rides the shell bar flag, which
is on in production.

Review round 2 added four rules to §3.1. The bar wraps,
and a phone draws the compact bar. Chat on a phone has none, and the Settings
sub-pages take the bar. Round 3 added a fifth: only the actions and the tools
wrap, so a long name never parts the rail toggle from the name.

NS-3 slice B (2026-10-09) builds My Day at `/`. It is dark behind
`NEXT_PUBLIC_MY_DAY`, which is off by default. Only the owner turns it on in
production (§13.1).

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
Home, the command bar, the dock and the bell. It also owns the account menu,
role presets and the app manifest.

`launch_surface.md` keeps owning WHICH panes are live.
`DESIGN_SYSTEM.md` keeps owning how a surface looks. Each app's own spec keeps
owning what happens inside the app.

---

## 0. The answer in one screen

The shell gets six parts that every app shares. Each app plugs into them
through one manifest, and no app builds its own copy.

1. **One shell bar, the same on every page.** It holds the logo, the command
   bar and the shell's own controls, and nothing of an app. Each app opens
   under it with its own title bar, `AppTopBar` (owner, 2026-10-10, §3.1).
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

- The shell bar, and the title bar each app opens with under it (§3.1).
- The sidebar shape and the account menu (§3.2, §3.3).
- Home at three altitudes, and the jobs of the Personal Center apps (§4).
- The app manifest and its rules for every app (§5).
- The command bar, its three tiers and its metering (§6).
- The assistant dock and the bell (§7).
- What a member sees while Metorite updates (§7.3).
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
| Search | Three palettes. Projects has `SearchPalette` and the command list `app/projects/lib/commands.ts`. My Tasks mounts the Projects palette. Email has its own `CommandPalette`. Three pages each hold a ⌘K listener | `app/projects/page.tsx:2635` · `app/tasks/page.tsx:180` · `app/email/page.tsx:692` · the key test `app/projects/lib/search.ts:143` |
| Bell | `NotificationBell` belongs to Projects. Projects and My Tasks mount it. Approvals has no badge | `app/projects/components/NotificationBell.tsx` |
| Assistant | Three rails on the shared `AgentChat`: `task-manager`, `projects-assistant` and `email-assistant` | `app/tasks/components/AssistantRail.tsx` · `app/projects/components/AssistantRail.tsx` · `app/email/components/EmailAssistantChat.tsx` |
| Personal rollup | None. No spec defined its content before this one | — |
| Live panes | **Eleven**, in four sections. Personal Center holds My Tasks, Calendar, My Profile, My Email and My WhatsApp. My Access left the sidebar on 2026-10-05, and it is a tab of the People app | `launch_surface.md` §2 · `src/lib/nav.ts` · `nav.test.ts` |
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

One row across the top of every desktop surface, `h-11` tall.

**The bar is constant. An app never renders into it** (owner, 2026-10-10).
The owner's words: the top bar stays constant across all of Metorite, and no
element of an app goes on it. The bar holds only shell things. So it draws the same
pixels on every page, and the eye learns it once.

**Every app opens with its own title bar, `AppTopBar`, under the shell bar.**
The top bar is the product's frame. An app's name and tools belong to the app,
where the eye looks for them, so the frame no longer changes from app to app.
Left to right, the app bar holds:

1. the app's rail toggle, when the app has a left rail. Email's toggle is the
   model the owner named.
2. the app's icon and its name. The name is the app's one `<h1>`.
3. the scope line, such as "Every space you can see".
4. the app's actions, such as Capture in My Tasks.
5. the app's tools at the right end: the bell, the assistant, refresh and
   settings.

The app bar is `h-10`, in one order and one look in every app. A pane may keep
its own header under it when it titles a pane, as Email's list does with
"Inbox" and its filter. That header is an `<h2>`, and it never repeats the
bar's toggle or tools. `DESIGN_SYSTEM.md` §6a holds the shape.

Five more rules, from the review of 2026-10-10:

- **The row wraps when it runs out of room.** The tools go to a second line at
  the right end, so no control leaves the screen. The subtitle gives way
  first, then the name truncates with an ellipsis. The name never paints over
  a control. A "More" menu was the other choice, and it lost: the tools carry
  their own anchored popovers, and `src/components/ui/` has no menu to hold
  them.
- **Only the actions and the tools wrap.** The left group is one item that
  never wraps: the rail toggle or the way back, the icon and the name. So a
  long name never parts the toggle from the name (round 3).
- **On a phone the app bar is the compact one,** with the subtitle muted after
  the name. My Day, People, My Profile, WhatsApp, the Email inbox and its
  scenes all draw it.
- **Chat draws no bar on a phone.** It opens there with the chat's own row,
  the agent and Share, and the Chats tab names the app. A bar above that row
  stacks a third row on the smallest screen. It had no `<h1>` before either.
- **A Settings sub-page opens with the bar too.** Teams, Roles, Billing and a
  member's page each carry a `back` link at the left end, to Organisation.

Fences: `src/lib/shell/appBar.test.ts` (source) and
`e2e/app-title-bar.spec.ts`. The e2e counts one `<h1>` and one bar on each
page at 1440 and 390. It checks Calendar at 1024, 960 and 800, and Email and
Projects at 1024. It checks a long name in Chat and on a member page at 900
and 1024.

**This reverses NS-1's merged row.** From 2026-10-08 to 2026-10-10,
`AppTopBar` portalled its contents into slots in this bar, and a page with no
app bar printed its app's name here. Both are gone, with the slot API.

**The bar spans the full width, and it carries the logo** (owner,
2026-10-09). The sidebar and the page sit under the bar. Before, the logo sat
in the sidebar's head and went away when the sidebar folded. Now a member
sees whose workspace this is at all times (D51).

- The brand zone is as wide as the open sidebar (`w-64`). It keeps that width
  when the sidebar folds, so the logo does not move.
- The zone holds the sidebar's fold control, then the logo on one line, 24px
  tall. A logo-less organization shows our mark and its own name.
- "powered by Metorite" follows the logo from the `lg` width up. Below `lg`
  it hides, because a cut byline reads as a defect.
- The fold tip points up at the bar's control. The fold rules of §3.2 do not
  change.

This frame needs both shell flags. With the shell bar alone, the bar sits
over the page column, as NS-1 built it. `desktopFrame` in
`src/lib/shell/shellNav.ts` holds the rule. Fences: `shellNav.test.ts`,
`e2e/shell-bar.spec.ts` and `e2e/sidebar-fold.spec.ts`.

| Zone | Owner | What it holds |
|---|---|---|
| Brand | The shell | The fold control and the organization's logo, on one line |
| ~~Title~~ | — | Not in the bar. The app's name is in its own title bar (owner, 2026-10-10) |
| Scope chip | The shell | Personal, a team, or all my teams (§4). A team app shows its own team, locked. Today the command bar's "in <App>" chip holds this place |
| Command bar | The shell | Search, do or ask (§6). It sits in the centre and takes the free width |
| ~~App actions~~ | — | Not in the bar. They are in the app's own title bar |
| Activity | The shell | Every live assistant run, across apps (WS-51 S3), at the right end |
| New | The shell | The jobs of the apps the member holds, in preset order (§8). Not built |
| Bell | The shell | "Needs you", from every app (§7.2). Not built. Today each app's bell is a tool in its own title bar, and an app's bell never moves into this bar |
| Assistant | The shell | The dock toggle (§7.1). Not built. Today each app's assistant toggle is a tool in its own title bar |
| ~~Avatar~~ | — | Not in the bar. The account menu is the sidebar's foot (§3.3, NS-2 slice 1) |

**The command bar's "in <App>" chip stays.** It is the command bar's own
scope (§6.4 rule 3), not the app's chrome. It is the one place the bar names
an app.

`AppShell` is the one importer of `src/lib/shell/ShellBar.tsx`, and it mounts
`ShellFrame` only. Fence: `src/lib/shell/appBar.test.ts`. It fails on a slot
API, and on a file outside `src/lib/shell/` that reaches into the bar. It
fails on a live pane whose route does not render `AppTopBar`, and on an app
name in the bar's markup. `e2e/shell-bar.spec.ts` checks the same rule in a
browser.

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
launcher under Admin, for a member who holds them. ⚠️ The owner reversed this
for Organisation on 2026-10-09 (§3.2a item 4). Approvals' items reach every
approver through the bell and My Day. ⚠️ Until NS-6 builds the bell,
Approvals keeps its sidebar door (§3.2a item 2).
Appearance is a personal preference, so it moves to every member's avatar
menu.

**The sidebar folds while you work.** Built 2026-10-06, by owner directive.
A member opens an app from the sidebar. The first click or key press in that
app folds the sidebar to its icon rail, so the app gets the width. The rule
lives in `src/lib/sidebarFold.ts`:

- Only a plain click on a sidebar link arms the fold. A click in the app with
  no sidebar link first does nothing, so the sidebar never folds while a
  member reads it.
- The fold fires on `click`, not on the press. A fold on the press moves the
  target before the release, and the click lands on nothing.
- The width eases, and the expand button pulses three times in the accent. On
  the first three folds, a tip names the button and offers "Keep it open".
- A member who opens the sidebar again keeps it open until the next sidebar
  link. Appearance → Sidebar turns the fold off. The folded state survives a
  reload.

Fence: `sidebarFold.test.ts` and `e2e/sidebar-fold.spec.ts`. The phone layout
has no sidebar, so the fold does not apply there.

⚠️ **The live set does not change.** §2 of `launch_surface.md` lists
eleven live panes, and `nav.test.ts` counts eleven. This spec changes
WHERE a live pane renders, not WHETHER it is live. Promotion stays an owner
decision (**H-21**).

### 3.2a What NS-2 slice 1 changed from §3.2, and why

The owner asked on 2026-10-09 that every shell change be "genuinely a step
ahead" for a member who is not technical. Slice 1 was measured against that
on screen, before and after, and three parts of §3.2 changed. Each one waits
on a ticket that is not built yet. When that ticket lands, the §3.2 shape
applies. Item 4 is a later owner change, and it does not wait on a ticket.

1. **No "My apps" group, and the top item is "Home".** Pins need NS-7, so
   the group would be empty for every member. A heading over nothing costs
   attention and gives nothing. `/` is still the "Welcome back" grid, so the
   name "My Day" would promise a page that does not exist. NS-3 renames it.
   Since NS-3 slice B, the item reads "My Day" when the My Day flag is on
   (`homePane` in `shellNav.ts`). With the flag off, it still reads "Home".
2. **Chat and Approvals keep a sidebar door.** §3.2 moves Approvals out,
   because the bell and My Day carry its items. Neither is built (NS-3,
   NS-6). Until they are, Approvals is an approver's only door to the queue,
   and Chat is the assistant itself. Both stay in their team's group: AI
   Studio and Admin.
3. **One line per item.** The old sidebar printed a second line under each
   item. In slice 1 that line was the manifest's purpose, and the sidebar grew
   taller than a 900px screen. A member who opens the sidebar every day reads
   that line once. The purpose now shows on hover. All apps prints it in
   full, because a member goes there to find out what an app is.
4. **Organisation is an app in Admin, in the sidebar** (owner, 2026-10-09).
   Slice 1 put its door in the account menu, so an admin found it only by
   opening their own account. An admin works in Organisation, so it is an
   app and not a page about the member. ⚠️ This item does not wait on a
   ticket. It replaces the §3.2 shape for Organisation.

**Groups, in order:** Home, then Personal Center, Across teams, AI Studio and
Admin, each only when it has an item. Admin holds Approvals, and Organisation
for an admin. All apps and the account menu are at the foot.
`src/lib/shell/shellNav.ts` builds all of it from `visibleSections`.
`shellNav.test.ts` pins five shapes. Two are a full admin and a plain member.
The others are a member with three apps, one with none, and an unresolved
viewer.

### 3.3 The account menu

It holds My Profile, My access, Appearance and sign-out. These pages describe
the member. The member does not work in them, so they leave the sidebar.
Organisation was in this menu until 2026-10-09. It is an app in Admin now
(§3.2a item 4), so it has one door. The menu holds only the member's own
pages. Fences: `shellNav.test.ts` and `e2e/account-switcher.spec.ts`.

⚠️ **Approvals is NOT in this menu** (§3.2a item 2). It keeps its sidebar door
until NS-6 builds the bell. The colour-mode toggle sits beside the foot's
button, not inside the menu. Do not move either one from this paragraph alone.

**The account switcher is in the sidebar foot** (MT-1k slice A2,
`saas_multitenancy.md`, built 2026-10-07). It lists every account this browser
is signed in to, with its organization, and switches with one click.

✅ **NS-2 slice 1 (2026-10-09) puts this menu IN that foot, not in a new avatar
at the top.** The owner asked for the switcher at the bottom left, beside
sign-out. A second place for "you" at the top would split one idea in two.
Slack, Notion and Linear put the menu in the same place.

The foot's menu holds these rows, in order:

1. the active account
2. My Profile, My access and Appearance
3. the other accounts
4. "Add another account" and sign-out

The colour-mode toggle stays beside the foot's button. With the switcher flag
off, the foot still shows. It reads the session then, and it lists no other
accounts.

**On a phone, the menu puts each act where its frequency says** (owner review,
2026-10-09: the menu was "very crowded").

- **The header says who and where, and switches.** It shows the brand mark,
  the organization that is open, and under it the address that is signed in.
  A tap unfolds the other accounts, each one tap away with its own remove
  control, and "Add another account". Nothing else.
- **The apps come next,** because opening an app is the frequent act.
- **The foot holds the rare acts,** under "Account and settings": My Profile,
  My access, Appearance, Dark mode, Desktop view and sign-out. Organisation is
  in the drawer's Admin group, with the apps.
- The sheet is solid. A translucent sheet let the page's words read through.

The phone has no account tab in the bottom bar. `DrawerAccountHeader` and
`DrawerAccountFoot` in `AccountSwitcher.tsx` are the two parts, and
`e2e/account-switcher.spec.ts` fences both.

**My Access moved first, on 2026-10-05.** The owner moved it into the People
app, as the ungated "My access" tab at `/people/access`, beside My profile. When
NS-2 builds the account menu, the menu links to that tab. It holds no second copy
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
| Needs reply | Personal | Email | Folded into Needs you. See the note below the table. As a card it would read `getDigest`, `app/email/lib/api.ts:2554`, which reads `GET /email/digest`, the route `get_digest` in `routes/email/digest.py` |
| Needs you | Personal | Every app | `GET /shell/needs` (§7.2) |
| Team pulse | Team, all my teams | Projects | Report template T1, "Team pulse" (`projects_reports.md`) |
| At-risk work | Team, all my teams | Projects | The analytics reads under `projects_ai_chat.md` §13 |
| Out today | Team | People | `people_absences` |
| Waiting for you | Personal, for an approver | Approvals | `pending_actions` |

**Needs reply is a group of Needs you, not a card** *(NS-3 slice B,
2026-10-09)*. The needs feed already carries each mail thread that waits for
a reply. So the Needs you card shows those threads as its last group,
"Waiting for your reply". One thread in two cards teaches the eye to skip
both cards. For the same reason, Next actions leaves out each task that
Needs you already shows. Fence: `src/lib/shell/myDay.test.ts`.

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
| `door` | `"sidebar"` (the default) or `"account"` | no | The sidebar and the account menu (§3.2a, §3.3) |
| `setting` | `true` for a preference about the member | no | All apps leaves it out |

**A job opens a form.** `href` is the form's route. `fields` names the query
values the form reads, so tier 2 may fill them (§6.6). A job never writes when
it opens.

**A job may be a link to the place where the work waits,** when the app has no
form to open. "Plan my day", "Reply on WhatsApp" and "Review pending
approvals" are links of this kind (owner decision, 2026-10-09). A link job
still names its app, so the member sees it only when they hold that app.

**Jobs live in two lists that a test holds as one.** The bar reads `JOBS` in
`src/lib/shell/registry.ts`. The gateway reads its own `JOBS` in
`gateway/routes/shell/intent.py`, because the gateway cannot import the
workbench. Each job names the feature it needs, so the gateway filters jobs by
the member's own features. The gateway never trusts a job list from the
client (R5e).

**The job of an `adminOnly` pane carries an admin gate.** Such a pane has no
feature, so a feature gate alone offers its job to every member. The gateway
offers it only to a member who holds `admin:members:read`. `GET /auth/me`
reports that same test as `is_admin`, and the sidebar shows the pane on it.

`test_shell_intent.py::TestOneJobList` reads both files and fails if an id, a
label, a link or a gate drifts. NS-2 moves the jobs into each app's manifest.

### 5.2 The rules for every app, built or future

1. **An app joins the shell through its manifest.** It never edits the
   sidebar, the launcher or Home by hand.
2. **An app mounts no ⌘K handler, palette, bell or assistant dock.** It
   declares jobs, search, needs and an agent, and the shell renders them.
3. **An app opens with its own title bar, `AppTopBar`, and never renders into
   the shell bar.** The bar holds its rail toggle, name, subtitle, actions and
   tools (§3.1). The owner reversed this rule on 2026-10-10. Until then it
   said that an app renders inside the shell bar through slots.
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

**Fences.** From NS-2, `src/lib/nav.test.ts` will fail on a live pane with no
`team` or `blurb`. It will also fail on a job whose `href` is no route.
`src/lib/shell/seams.test.ts` exists since 2026-10-05, ahead of NS-1. It
sweeps `src/` and fails on these four:

- a ⌘K listener, in any of five spellings, with a modifier that is not negated
- an import of `NotificationBell`, or a read of the notification list
- an import of `AgentChat` or of a rail wrapper, except in `/chat`
- an import of `SearchPalette` or `CommandPalette`

Imports, not JSX: an import cannot hide in a ternary or a renamed tag. It is
also how the fence sees one app reuse another app's rail.

The sweep starts with a baseline of today's sites, 18 in 4 apps. The debt is
keyed by app folder, because D89's unit is the app, so a move inside one app
is free. The baseline only goes down, the same ratchet as `conformance.test.ts`.
`src/lib/shell/` is exempt, because NS-1 builds the shell there. It may hold one
file per seam, so it cannot become a place to move an app's debt.

**The spec half is R9** (`work_plan.md` §1). An app spec carries a "Shell
manifest" section, and the spec-auditor refuses one without it.

### 5.3 How each app that exists today conforms

| App | `team` | Jobs | `search` | `needs` | Cards | `agent` | Work owed |
|---|---|---|---|---|---|---|---|
| My Tasks | personal | New task | `GET /projects/search` (`routes/projects/search.py:221`). `app/tasks/lib/searchHit.ts` decides where a hit opens | due today, overdue | Next actions | `task-manager` | Stop mounting the Projects palette and bell |
| Calendar | personal | Plan my day (built). Block focus time is a target | — | — | Today | `task-manager` | Done 2026-10-10: its name, its day arrows and its tools are in its `AppTopBar` |
| Email | personal | Write an email | email search | needs reply | Needs reply | `email-assistant` | Its palette commands become jobs. Its ⌘K handler goes. Its heading drift closed on 2026-10-10, when it took `AppTopBar` |
| My WhatsApp | personal | Reply on WhatsApp (a link) | — | — | — | — | None beyond the manifest |
| Projects | across | New space | projects search | `pm_notifications` | Team pulse, At-risk work | `projects-assistant` | Its palette and bell move to the shell. `lib/chatDock.ts` becomes the dock's rule. Its tree groups by team (D22) |
| People | people | Find a colleague (built). Request leave is a target | the directory | — | Out today | — | None beyond the manifest |
| My Profile | personal | Update my profile | — | — | — | — | Move to the account menu |
| My Access | personal | — | — | — | — | — | Done 2026-10-05: a People tab at `/people/access`. NS-2's account menu links to it |
| Chat | studio | New chat | chat sessions | — | — | any | `/chat` stays. The dock shares its sessions |
| Approvals | admin | Review pending approvals (a link) | — | `pending_actions` | Waiting for you | — | Its items feed the bell |
| Organisation | admin | Invite a member | members | seat requests | — | — | An app in Admin, in the sidebar (§3.2a item 4) |
| Appearance | personal | Change how Metorite looks | — | — | — | — | Moves to every member's account menu |

A `preview` app writes its manifest in the pull request that promotes it.

**The Projects job is "New space".** A root node of the Projects tree is a
space (migration 193), so the label names the row that opens. The job id
stays `new-project`. "New task" is the label of the My Tasks job `capture`.

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

The command bar is always visible in the shell bar. `Ctrl K` and `⌘K` focus it
from any surface. The shell holds the only listener (§5.2 rule 2).

**`/` focuses the page's own filter when the page has one, and the command bar
when it does not.** Owner decision, 2026-10-07. Gmail and GitHub use `/` this
way, so a member who knows either already knows it. `⌘K` is never a filter.

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
| **1 — record search** | 300 ms typical, 1.5 s ceiling | One gateway route, `GET /shell/search`, which calls each provider in turn on the request's session | Nothing. No model |
| **2 — AI intent** | 1.5 s | `POST /shell/intent`, through the Router | Credits, metered (§6.5) |

Each tier adds results under the ones already shown. A tier never moves a
result that the member can already see. The two hand-off rows, "Show all in"
and "Ask", are not results. They stay last, so Find arrives above them and
pushes them down. The bar holds the highlight by its row, not by its place, so
a late answer never changes what `Enter` runs.

**The tier 1 ceiling.** The providers run one after another, so the route
holds one total deadline of 1.5 s (`TOTAL_BUDGET_S`) and gives each provider
what is left. The browser's abort reaches the gateway too, so a request whose
words the member changed stops at once.

**Tier 2 runs only when one of these is true:**

- the query holds two words or more, and the member paused for 900 ms

`Enter` always has a row to run, because the bar always offers "Ask the
assistant" for a sentence. So `Enter` never starts tier 2 by itself. A member
who presses it before the pause gets the hand-off to the assistant.

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

### 6.7 One bar searches. An app's bar only filters

Owner decision, 2026-10-07. A member must never meet two boxes that both say
"search", and wonder which one to use. So there are two kinds of box, and only
two:

| | The command bar (one, in the shell bar) | A list filter (an app may have one) |
|---|---|---|
| Its job | Find, go, do or ask, anywhere | Narrow the list on this page |
| Where | The shell bar, in the centre, on every page | Inside the app, above its list |
| Its words | ✦ "Search or ask anything" and `⌘K` | "Filter Inbox", "Filter tasks", with pills such as `from:alice` |
| Its results | A panel of results from every app | The list itself changes. No panel opens |
| AI | It answers, or hands off to the dock | Never |

**Five rules:**

1. **An app may have a filter. It never has a second search box, a palette or
   a `⌘K` of its own** (§5.2 rule 2, D89). `seams.test.ts` fences the palette
   and the key.
2. **A filter says "Filter", never "Search".** Its placeholder names the list,
   such as "Filter Inbox". The word "Search" belongs to the command bar.
3. **They hand off. They never compete.** Inside an app, the command bar shows
   the "in Email" token (§6.4 rule 3). Its last row is "Show all in your
   Inbox", which puts the words into the app's filter. A filter that finds
   nothing offers "Search everywhere for …", which opens the command bar with
   the same words.
4. **Asking happens in one place.** Only the command bar answers a question. A
   longer talk goes to the dock, with the question and the answer carried over.
5. **Plain words, for every member.** Each result says what it is and where it
   opens: "Task · Projects", "Email · Inbox", "Open My Tasks". A result never
   shows an id, a route or a field name.

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

**Built in NS-3 slice A (2026-10-09, dark).** `gateway/routes/shell/needs.py`
serves `GET /shell/needs` on the router of `search.py`. Three providers are
built. Each one checks its feature, then calls the app's own read:

| Source | Feature | Function |
|---|---|---|
| `tasks` | `feature:projects` | `my_due_tasks` in `routes/projects/personal.py` |
| `projects` | `feature:projects` | `list_notifications`, unread only |
| `email` | `feature:email` | `list_accounts`, then `needs_reply_threads` in `routes/email/digest.py` for each mailbox |

**Each read is one bounded query.** `my_due_tasks` reads the lens rows that
are due before the member's tomorrow, in the member's zone. It gives the
oldest deadline first, and 15 rows at most. `needs_reply_threads` gives the
thread that waited longest first, and 15 rows at most.

⚠️ **My Tasks needs `feature:projects`, not `feature:tasks`.** The lens
routes are on the Projects router, and that router demands `projects`.

**A Someday task never shows in the feed, also with a due date, but a Waiting
task does.** The lens read leaves out each task that is not the member's to
do now, and the provider checks the disposition of each row again.

**A Reference task never shows in the feed either.** It is information, not an
action. `NOT_NOW_DISPOSITIONS` in `routes/projects/personal.py` holds the two
values, and the lens read and the feed both take them from it.

**A snoozed or archived thread never shows in the feed.** The email read takes
the rows that the Needs-reply count of the email app counts. So the feed
leaves out a thread when the member snoozed its last message, or put it in
the archive, junk or trash. Archive means "dealt with", and Reply Zero hides
an archived thread too.

**Approvals is slice C, and it waits on H-201.** Until then the feed has no
`approvals` source.

**The contract.** My Day and the bell read this shape:

- `GET /shell/needs?limit=` gives 30 rows by default, and 50 at most.
- The answer is `{count, items, sources}`. `count` is the length of `items`.
- Each item has `id`, `app`, `kind`, `title`, `detail`, `href`, `at`, `act`
  and `act_ref`.
- `id` is `tasks:<task id>`, `projects:<notification id>` or
  `email:<account id>:<thread id>`.
- `kind` is `overdue`, `due_today`, `notification` or `needs_reply`.
- `act` is `done`, `read` or null. `done` runs
  `POST /projects/tasks/{act_ref}/complete`. `read` runs
  `POST /projects/notifications/read` with the id in `ids`.
- `sources` gives each source as `ok`, `failed` or `absent`. `absent` means
  that the member does not hold the feature.

**The order.** Overdue rows come first, the oldest first. Then rows due today,
then notifications with the newest first. Then needs-reply rows, with the
person who waits longest first. Each source gives 15 rows at most, so one app
cannot fill the feed.

**Two rules from the email app apply.** A mailbox that the member keeps
separate stays out of the feed (D-EM-30). The read of the mail never starts
a backfill of the reply status.

**A mailbox that fails costs only its own rows.** Each mailbox has a time
limit of one second. The `email` source reads `failed` only when every
mailbox failed.

Fences: `tests/unit/test_shell_needs.py` holds the gate, the order, the caps,
a failed source and the SQL of the two reads. `tests/unit/test_shell_needs_r8.py`
is R8, as the role with no privileges and with RLS forced. It also holds a snoozed thread, a junk thread and
a member with more work than the cap.

---

### 7.3 Updating, not broken

**Owner directive, 2026-10-05.** The owner asked for a pop-up that says "The
app is updating. Please wait a moment", in place of a 504. The owner then made
it wider: every error that a database, backend or app update causes must leave
the member calm.

**What a deploy looks like from a browser.** Measured on the box on
2026-10-05, from 04:05 to 04:08 UTC:

- The gateway is down for 10 s.
- Two minutes later, the workbench takes 7 s to stop. It is ready 140 ms after.
- Caddy holds a request for up to 30 s (H-60).
- `gatewayFetch` holds the workbench's own call to the gateway for up to 25 s
  (H-194).

Some requests still fail. A restart cuts an open stream, a hold can run out,
and a route can answer 502 on its own.
There was also no error page at all, so a crash showed React's raw
"Application error". A tab that stayed open across a deploy produced exactly
that crash the first time it loaded code that the new build had renamed.

**Three layers, one message.**

| Layer | When | What the member sees | Where |
|---|---|---|---|
| Caddy (NS-10b) | The workbench has not answered for the whole 30 s hold | A full page, "Metorite is updating". It checks `/api/health` every 3 s and reloads by itself | `deploy/hostinger/caddy/updating/index.html`, the `handle_errors` block of `app.metorite.com` |
| The shell | A request to `/api/*` gets a 502, 503 or 504, or no answer, AND `/api/health` then says the gateway is down | The shared toast: "Metorite is updating", then "Metorite is back" with a Refresh button. "Metorite was updated" with a Reload button when the build changed. "You are offline" when the browser has no network | `lib/shell/UpdateNotice.tsx`, `lib/shell/serviceHealth.ts`, `app/api/health/route.ts` |
| The error page | A page throws | "This page did not load" with Try again and Reload. A tab out of date after a deploy reloads itself once | `app/error.tsx`, `app/global-error.tsx`, `lib/shell/ErrorScreen.tsx`, `lib/shell/chunkReload.ts` |

**Rules.**

1. **One failed route is not an update.** The shell asks `/api/health` first.
   A healthy answer ends it with no message, and the route shows its own
   error.
2. **The shell never reloads a page by itself**, except a tab whose code is
   out of date. A reload can lose what the member is typing, so the member
   decides. The one exception reloads at most once a minute, so a real bug
   can never loop.
3. **The words make no promise the page cannot keep.** "If something did not
   save while it was updating, try it again." The shell does not know what
   saved.
4. **One message at a time.** Every state uses one toast key, so "updating"
   turns into "back" in place.
5. **`/api/health` is public**, so the sign-in page can say "updating" too. It
   answers up or down and a build id, and reads nothing private.
6. **An app never shows its own "server unavailable" banner.** It is the
   shell's message, under D89.

**A busy database (NS-11, 2026-10-06).** The owner saw "The server had an
error (500). Nothing was saved." in Projects during a deploy, and no pop-up.
The cause was not the restart. Supabase's session pooler allows 15 clients
for the whole database, and one gateway could ask for 27:

- the async pool of `acb_common.db`, 8 + 4.
- the sync pool of `acb_graph.db`, on SQLAlchemy's default 5 + 10.

The box logged about 590 refusals (`EMAXCONNSESSION`) in two days, during
deploys and at busy moments. Each one became a 500, and the shell ignores a
500 on purpose. NS-11 changes three things:

1. **The budget.** PR #662 landed it the same day: async 7 + 2 and sync
   2 + 1, so 12 for both pools. Its fence sums both pools. Three slots stay free. A migration run, the
   backup and an operator's `psql` use them. So do a few bare
   `psycopg.connect()` calls that are still outside both pools. H-250 moves those calls in.
2. **The answer.** A refused connection is a 503 with `Retry-After` and the
   code `db_busy` (`acb_common/db_busy.py`). Every other error keeps its 500.
3. **The words.** The gateway's `/health` says `db: "busy"` for 15 s, from
   memory. The shell shows "Metorite is busy" and then "Metorite has caught
   up". A panel shows "Metorite is busy or updating. Wait a moment and try
   again."

**What it does not cover.** An open chat stream that a restart cuts still
ends. NS-10 does not change how the chat reports that. A migration that holds a lock
can make a request slow without failing it, so no layer sees it. R6 keeps
old code working against the new schema, which is why a migration does not
produce an error of its own.

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
| Search, bell and assistant inside each app's `AppTopBar` | One constant shell bar with the one search. Each app keeps its own `AppTopBar` under it, with its name and its tools (owner, 2026-10-10) | NS-1 |
| Three palettes | One command bar. `app/projects/lib/commands.ts` seeds the job list | NS-1, NS-2 |
| The Projects `NotificationBell`, and no Approvals badge | The shell's "Needs you" | NS-6 |
| Three assistant rails | One dock | NS-6 |
| My Profile and Appearance in the sidebar | The account menu | NS-2 |
| My Access in the sidebar | ✅ The People app's "My access" tab, 2026-10-05 | — |
| Calendar's own header | ✅ Calendar's `AppTopBar`, 2026-10-10 | — |
| Projects shows "every space you can see" | Grouped by team (D22), driven by the scope chip | NS-5 |

---

## 11. Tickets

Every ticket ships dark behind its flag, default off. With a flag off, every
surface renders as it does today, because a merge to `main` is a deploy. Each
flag stays off in production until the owner turns it on (§13). To build is
AGENT-SAFE. To turn any flag on in production is OWNER-GATE.

### NS-1 · The shell bar and the one ⌘K listener — AGENT-SAFE · slice 1 BUILT 2026-10-08, dark

**Built in slice 1:**

- `lib/shell/ShellBar.tsx` holds the bar and the ONE key
  listener. Its two slots went on 2026-10-10 (owner, §3.1). `⌘K` is taken in the capture phase and stopped, so the three app
  listeners never fire while the flag is on. `/` follows §6.1.
- ~~`AppTopBar` portals its contents into the slots.~~ Reversed by the owner
  on 2026-10-10. Every app now draws its own `AppTopBar` row under the shell
  bar, and the shell bar names no app (§3.1).
- `lib/shell/CommandBar.tsx` has Do, Go to, "In this page" (§6.7 rule 3) and
  Ask. The Ask row opens `/chat?q=`, and Chat types the words in and does not
  send them. NS-4b makes Ask answer in place.
- `lib/shell/pageFilter.ts` reaches a page's filter for every page: `/`
  focuses it, and "Show all in …" types into it as a keystroke does. A closed
  filter (`data-page-filter-opener`) opens first. A page that needs more,
  such as Email's pills, takes the words itself.
- `lib/shell/registry.ts` holds the jobs and the ranking. It reads a sentence:
  "new email to priya" finds Write an email. `lib/shell/doJob.tsx` is the job
  door: a link `?do=<id>&fill.<field>=…` opens the app's form once.
- Email's box, My Tasks' two boxes and Chat's conversation box say "Filter"
  and carry `data-page-filter` (§6.7 rule 2). Email's old `⌘K` button hides.
- The phone has no row. The Menu drawer opens the same bar (§9).
- A dev or test build may turn the bar on per browser
  (`localStorage["cc-shell-bar"]`), so `e2e/shell-bar.spec.ts` runs both
  sides. A production build drops that branch.

**Closed on 2026-10-10:** Calendar took `AppTopBar`, so its own header went.
Email took `AppTopBar` too. It keeps its list header under the bar, on
purpose: it holds the folder and the list filter, which §6.7 places above
the list.

✅ **NS-4a is built (2026-10-08), so the bar's Find group now holds what the
three old searches found.** With the flag on, these searches go:

- the Projects palette and its Search button
- the My Tasks palette and its Search button, on desktop and on the phone
- the Email palette and its `⌘K` button

Find covers task titles, email subjects and senders, and colleagues by name,
title or department. The old Projects palette also ran its own commands, such
as view switches. Those live on the Projects page itself.

Flag `NEXT_PUBLIC_SHELL_BAR`. Files: `src/components/AppShell.tsx`,
`src/components/AppTopBar.tsx`, a new `src/lib/shell/`, and the pages of
Projects, My Tasks, Calendar and Email.

Done when:

1. With the flag on, `AppShell` renders the shell bar on every desktop route.
2. ~~Four apps fill the shell bar's slots and draw no bar of their own.~~ The
   owner reversed this on 2026-10-10. Every live app draws its own
   `AppTopBar` row under the shell bar (§3.1).
3. The command bar answers tier 0 from the registry and from
   `app/projects/lib/commands.ts`.
4. `src/lib/shell/seams.test.ts` already exists (2026-10-05). NS-1 puts the
   shell's parts in `src/lib/shell/`, and adds no budget to `SEAM_DEBT`.
   With the flag on, only the shell's ⌘K listener fires. With the flag off,
   the three listeners at `app/projects/page.tsx:2635`, `app/tasks/page.tsx:180`
   and `app/email/page.tsx:692` run as today. NS-9 deletes them.
   Two tests pin the My Tasks listener today, `app/tasks/lib/searchHit.test.ts`
   and `app/tasks/lib/shortcuts.test.ts`. NS-1 keeps them green with the flag
   off, and NS-9 rewrites them.
5. With the flag off, every surface renders as it does today. A test pins the
   nav order and the bar's absence.
6. The visual review of `DESIGN_SYSTEM.md` §8 ran: light mode, compact density,
   a changed accent, and the neighbouring app.

### NS-2 · The manifest, All apps, the sidebar shape and the account menu — AGENT-SAFE · slices 1 and 2 BUILT 2026-10-09. Slice 1 is ON in production since 2026-10-09. Slice 2 rides the same flag, so it is live when it merges

**Built in slice 1:**

- `NavPane` carries `team`, `blurb`, `door` and `setting` (§5.1). Every pane
  has a team. Every live pane has a purpose in job words, 60 characters or
  fewer. The command bar prints it beside "Open …", in place of the operator
  note.
- `src/lib/shell/shellNav.ts` builds the sidebar, the account menu and All
  apps from `visibleSections`. `AppLauncher.tsx` is All apps.
- The sidebar, the folded rail and the phone drawer take the shape of §3.2a.
  The account menu is the sidebar's foot (§3.3).
- Fences: `nav.test.ts` and `shellNav.test.ts`. The first fails on a live
  pane with no team or no purpose. It also fails when a page other than the
  three member and organization pages opens from the account menu. The second
  pins the shapes that four kinds of member see.

**Built in slice 2:**

- Three jobs, in `lib/shell/registry.ts` and in `intent.py`. "New space" opens
  the draft row at the root of the Projects tree. "New chat" starts a
  conversation in Chat. "Invite a member" opens the invite dialog of
  Organisation.
- The gateway's `Job` carries `admin`, and `held_jobs()` holds an admin job
  for an admin only (§5.1). `TestOneJobList` fails if a job's admin gate
  disagrees with `adminOnly` in `nav.ts`. A second test asserts that a member
  who is not an admin does not hold `invite`.
- Done-when 2's job route check. `nav.test.ts` fails on a job whose `href`
  has no `page.tsx` under `src/app`.

**Built in slice 3 (2026-10-09):** two link jobs, "Reply on WhatsApp" and
"Review pending approvals", after the owner chose links. Every live pane now
has a job, and `nav.test.ts` fails on a live pane with none.

**Still open in NS-2:** done-when 1's "`NavPane` carries the §5.1 fields". The
jobs still live in one list in `lib/shell/registry.ts`, and the next slice
moves them onto each pane's manifest (`NavPane.jobs`, §5.1).

Flag `NEXT_PUBLIC_SHELL_NAV`. Files: `src/lib/nav.ts`, `src/lib/nav.test.ts`,
`src/components/Sidebar.tsx`, and a new launcher and account menu in
`src/lib/shell/`.

Done when:

1. `NavPane` carries the §5.1 fields, and every live pane has `team`, `blurb`
   and one job or more.
2. `nav.test.ts` fails on a live pane with no `team` or `blurb`, and on a job
   whose `href` is no route.
3. The launcher renders `visibleSections(features, isAdmin)` and nothing else
   (`launch_surface.md` §8.4).
4. `nav.test.ts` counts eleven live panes, the set of `launch_surface.md` §2.
5. With the flag on, the sidebar takes the §3.2 shape, and the account menu holds
   the §3.3 items.

### NS-3 · My Day and the needs feed — AGENT-SAFE (build), OWNER-GATE (turn on) · slice A BUILT 2026-10-09, live · slice B BUILT 2026-10-09, dark

**Slice A is built.** `GET /shell/needs` serves three of the four providers
of §7.2: My Tasks, Projects and Email. §7.2 records the contract. Done-when 2
to 4 are met for those three, and the R8 run is in the pull request.

Slice C adds Approvals after H-201. My Day itself (done-when 1 and 5) is
slice B, below. It is dark, so no page that a member sees reads the feed yet.

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

**Slice B, built 2026-10-09, dark.** It builds My Day at `/`, behind
`NEXT_PUBLIC_MY_DAY`. Done-when 1 and 5 hold for the page, and
`src/lib/shell/myDayPage.test.ts` renders both branches. Done-when 2 to 4
belong to slice A, the gateway feed.

- **The cards.** `NeedsYouCard` is in `src/lib/shell/`. `TodayCard` is in
  `app/calendar/components/`, and `NextActionsCard` is in
  `app/tasks/components/`. Each card reads through `useCachedResource`.
- **The feed's door.** `src/app/api/shell/needs/route.ts` sends the feed on
  from the gateway. When the gateway is down, it answers 502. An empty feed
  would say "Nothing needs you", and that is a claim, not a silence.
- **The acts.** A one-click act runs through the code of the app that owns
  it (`src/lib/shell/needs.ts`). My Day adds no write route. A done is the
  My Tasks store's own gesture, `quickDispose`, through
  `app/tasks/lib/completeFromHome.ts`. So a parent with open subtasks asks
  the D-PM-38 question first, and Undo is the store's toast (D79). The first
  done on a visit loads the store once. Before each later done, the store
  reads that one task again (`refreshItem`). A task that the store never saw
  makes it load again. While the question is up, the row stays on the card.
  It leaves only when the answer completes the task. When the store's Undo
  puts the task back from done, the row comes back too. The watch for a failed
  write starts when the store writes, so it also sees a late answer. A
  notification marked read has no Undo, because the Projects bell has no
  route that marks it unread.
- **The cache.** A write that lands while a read of the same key is in
  flight makes that read's answer stale (`lib/dataCache.ts`). The read does
  not store it, and it reads again. Before this, an Undo during the re-read
  after a Done showed the state from before the Undo. Each such write also
  wakes the key's watchers, so a read that gave up never stays on screen.
- **Not yet in the manifest.** My Day imports its three cards directly. They
  are not declared through the `cards` field of §5.1 yet. The manifest slice,
  with NS-7, moves them there.
- **Who gets a card.** A card asks for what the server asks for. Today and
  Next actions read `/projects/my/*`, and the Projects router demands
  `feature:projects`. So both cards need `tasks` and `projects`. Needs you
  needs `projects` or `email`, the features of its sources.
- **One caveat, once.** When a source does not answer, the Needs you card
  names the gap and offers Retry. An example is "Email did not answer, so
  its replies may be missing". The summary then says nothing about needs.
- **The sidebar.** With the flag on, the first item reads "My Day".

### NS-4a · Tier 1, record search — AGENT-SAFE · BUILT 2026-10-08, dark

**Built.** `gateway/routes/shell/search.py` serves `GET /shell/search`. It has
three providers, and each calls its app's own function on the member:

| Provider | Feature | Function |
|---|---|---|
| Tasks | `projects` | `search_tasks` |
| Email | `email` | `search_messages` |
| People | `people` | `list_directory` |

⚠️ Each app checks its feature on its ROUTER, so a direct call skips that
check. Each provider therefore checks the feature itself first. The providers
run one after another. Each has a time limit of one second, and all of them
share one deadline of 1.5 s (§6.3). A provider that fails is left out.

The command bar's **Find** group shows the records under the rows already
shown. The bar holds the highlighted row by its key, so a late answer never
moves the member's choice.

Fences:

- `tests/unit/test_shell_search.py`: a negative case per provider, the member
  passed unchanged, one provider at a time, and the email call in step with
  its route.
- `tests/unit/test_shell_search_r8.py`: R8 for tasks. Org B's task never
  returns to a member of org A.
- `tests/unit/test_shell_search_email_r8.py`: R8 for email, as the
  non-privileged role with RLS forced. Another member's message and the
  member's own separate mailbox never return.
- `tests/unit/test_shell_search_people.py`: the people provider never matches
  a colleague by a skill for a member without HR read.
- Both R8 files ran on 2026-10-08 against a private ladder database, because
  the shared one hits H-172.
- `e2e/shell-bar.spec.ts`: the Find group.

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

### NS-4b · Tier 2, filled jobs and the hand-off — AGENT-SAFE (build), OWNER-GATE (turn on) · BUILT 2026-10-08, dark

**Built.** `gateway/routes/shell/intent.py` serves `POST /shell/intent`. The
owner's words, 2026-10-07: *"a high-level coordination AI that enables us to
quickly go to the appropriate app and workflow depending on our query."*

1. **Pick.** The route makes one typed choice through `acb_llm.decide`, among
   `held_jobs(user)` and "ask". The Console Router serves it and bills it.
2. **Fill.** One completion through `completion_on_router` reads the job's own
   fields. An email has `to` and `subject`, and a task has `title`. It
   runs ONLY when `routing_is_on()` (H-69). Off, the job opens with an empty
   form.
3. **The answer** is one of these:
   - `job`, with `fill.<field>` parameters for the job door (`doJob.tsx`)
   - `handoff`, which opens `/chat?q=` with the words typed in
   - `paused`, which shows the §6.5 line
   - `off`, `none` or `unavailable`
4. **Cache.** The route keeps each answer for five minutes. The key holds the
   member, the scope and the words, through `acb_common.tenant_redis`.

⚠️ **The job list is two files kept as one.** The server reads its own list,
not a `jobs.json`, because the gateway cannot import the workbench.
`test_shell_intent.py::TestOneJobList` fails if the ids, the links or the gates
drift from `lib/shell/registry.ts`.
Since NS-2 slice 2, an admin job also needs `admin:members:read` (§5.1).

The server limits the pick and the fill to three seconds each. Uvicorn does not cancel
a handler when the browser goes, so the limit must live on the server. A fill
that fails or runs out of time is no fill. The job opens empty, and the answer
is cached, so the pick is never billed twice. A caller with no address,
such as the internal service, is no member and is never billed.

The bar shows the pick as the first row of Ask: "New task · call the vendor",
marked "Suggested by AI". Compose and Capture then say "Filled by AI" beside
what the AI wrote (§6.4 rule 2). It waits for two words and a pause of 900 ms. Capture
and Compose open with the filled words. The member checks them, and nothing is
saved or sent until the member does so.

**Not built: a limit per member.** Only the organization's credit cap stops a
script that sends a new sentence on every call. Chat has the same exposure.

**Two switches, both the owner's:** `COMMAND_BAR_AI` on the gateway turns the
route on. `DECIDE_ENABLED`, which the email rules already use, serves the pick.
The fill also needs `ROUTER_SERVING_ENABLED` (H-69).

Fences: `tests/unit/test_shell_intent.py` (done-when 2 to 5), the
`DELEGATED_ROUTERS` entry, and three e2e cases.

**Done-when 3, read against the build.** A billed call writes its usage row
in the Console, not in the gateway. So the test asserts the call, not the row:
one `decide` call for a new intent, and none for a repeat.

Flag `COMMAND_BAR_AI` on the gateway, default off. Files:
`gateway/routes/shell/intent.py`.

Done when:

1. `POST /shell/intent` returns `job` or `handoff` (§6.6) through the Router.
2. The prompt holds only the jobs the member can open. The server filters
   its job list by the member's own features. One test asserts that a job
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

### NS-10 · Updating, not broken: the app (§7.3) — AGENT-SAFE · BUILT 2026-10-05

No release flag. The owner asked for this behaviour on 2026-10-05, so the
merge is its release. It has a kill switch, because it wraps `window.fetch`:
set `NEXT_PUBLIC_UPDATE_NOTICE=off` and rebuild.

Done when, all met:

1. A 502, 503 or 504 from `/api/*`, with the gateway down, shows "Metorite is
   updating". The gateway's return shows "Metorite is back". A new build
   shows "Metorite was updated". One failed route with a healthy gateway
   shows nothing. A visual rig drove all four.
2. A page that throws shows `ErrorScreen`, not React's raw error.
3. Fences: `lib/shell/serviceHealth.test.ts` and `proxy.test.ts` (the public
   probe). Each was seen to fail first. `e2e/toast.spec.ts` stubs the probe
   as healthy, because CI runs no gateway.

### NS-11 · A busy database is "busy", not a 500 (§7.3) — AGENT-SAFE · BUILT 2026-10-06

Done when, all met:

1. The budget of #662 holds: 12 for both pools, async 7 + 2 and sync 2 + 1.
   NS-11 does not change it.
2. A refused or timed-out database connection answers 503 with `Retry-After`
   and the code `db_busy`, and the body names no pooler. Any other exception
   keeps the bare 500.
3. `/health` reports `db: "busy"` for 15 s after a refusal, with no database
   call. `/api/health` passes it on as `gateway: "busy"`.
4. The shell shows "Metorite is busy", then "Metorite has caught up".
5. Fences, each seen red first. `test_db_engine_seam.py` sums both pools,
   and refuses an engine on the default pool. `tests/unit/test_db_busy.py`
   never counts a marker in the SQL or its parameters. Also:
   `lib/shell/serviceHealth.test.ts` and `lib/apiError.test.ts`.

### NS-10b · Updating, not broken: the Caddy page (§7.3) — BUILT 2026-10-06 (gate (a) approved by the owner)

The page needs a `header` line and a `rewrite` line in the
`app.metorite.com` block. The fence `tests/unit/test_caddy_auth_gate.py`
treats every such line as a sign-in line, so the change needed the owner's
approval (`work_plan.md` §6, gate (a)). The owner wrote this line in
`.claude/OWNER_GRANTS.md` by hand on 2026-10-06:

    CADDY-AUTH-APPROVED 3c8217ffb4fa3fc9d049ee170ec057c7f65fee739ba9a167573a96aaba5dce21

⚠️ An agent must not edit `_BASELINE`, and must not swap in a directive the
fence does not list, such as `try_files` for `rewrite`. Either one passes the
test and defeats the gate. A LATER change to these lines changes the hash, and
it needs a new approval line.

Done when, all met:

1. If the workbench has not answered for the 30 s hold, each path answers 503
   with the updating page. It also sends `Cache-Control: no-store` and
   `Retry-After: 10`. Measured on the box on 2026-10-05, with a throwaway
   Caddy in front of a closed port.
2. The page reloads with a plain GET, so a form is never sent again.
3. Fence: `tests/unit/test_deploy_serialize.py::TestCaddyShowsTheUpdatingPage`,
   seen to fail first.

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
npx vitest run src/components/AppTopBar.test.ts src/components/pageHeading.test.ts

# The constant shell bar and each app's title bar, in a browser
npx playwright test e2e/shell-bar.spec.ts --project=chromium

# Gateway — needs a real database (R8)
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_shell_needs.py tests/unit/test_shell_needs_r8.py tests/unit/test_shell_search.py tests/unit/test_shell_intent.py -q
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
- **Q2, answered by the owner on 2026-10-09.** Yes. `/` becomes My Day for
  every member, behind `NEXT_PUBLIC_MY_DAY`. To turn the flag on in
  production stays owner-only (§13.1).

### 13.3 Still open — each has a default an agent builds to

| # | Question | Default |
|---|---|---|
| Q2 | Does `/` become My Day for every member? | Answered on 2026-10-09. See §13.2 |
| Q4 | Does the shell bar take one shared row with each app's bar? | No. The owner reversed this on 2026-10-10. The shell bar is constant, and each app draws its own title bar under it (§3.1). Until then the default was one row, merged in NS-1 |
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
- `workbench/control_plane/DESIGN_SYSTEM.md` §6a: the shell bar and the app
  bar, and the rule between them.
- `workbench/control_plane/AGENTS.md` rule 10: an app plugs into the shell and
  never builds one. Rule 11: the shell bar is constant, and every app opens
  with `AppTopBar`.
- Root `CLAUDE.md` §2: one bullet.
