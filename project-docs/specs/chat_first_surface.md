# Chat first — the assistant is the front door

**Status: PROPOSED, 2026-10-20. CF-1 and CF-3 BUILT 2026-10-20, dark.**
Owner directive of 2026-10-20, recorded in §1. Verified against code on
2026-10-20, at `origin/main` `f629b6bab`. Board row **WS-62**.

**CF-1, the peek strip, merged as #1198.** **CF-3, `show_in_app`, merged as
#1197** after one review round (a dot segment walked past the pane check,
and the tool was not dark). Both are in production since the release of
2026-10-21, behind two flags that are off. The client flag is
`NEXT_PUBLIC_CHAT_FIRST` in `workbench/control_plane/.env.local`, which
`acb-staging-flag` refuses, so the owner writes it on staging too. The server
flag is `CHAT_FIRST_TOOLS`, which adds the tool to the orchestrator. CF-2,
CF-4, CF-5, CF-6 and CF-7 are not built.

§5 proposes **D-CF-1** to **D-CF-7**, each with a default. HANDOFF **H-369**
carries them to the owner. Every ticket in §6 is AGENT-SAFE to build dark,
behind `NEXT_PUBLIC_CHAT_FIRST`. Turning the flag on in production is an
owner act (`navigation_shell.md` §13.1).

**What this spec owns.** How a member reaches the one assistant, on every
screen size. The sizes the assistant takes. How the chat opens a page, and
how it shows a record without a page. What a first-time member sees. What the
chat says before the member says anything.

**What this spec does not own.**

- **The one assistant itself.** `navigation_shell.md` §7.1 (D99, D102) owns
  the agent, the field, the two desktop forms and the app switch. This spec
  adds a third size and a phone rule, and it changes no rule of §7.1.
- **The runs.** `chat_run_continuity.md` (WS-51) owns the activity control,
  the badges and the signals of a run outside its chat.
- **Messages Metorite sends first.** `proactive_reach.md` (WS-59) owns
  WhatsApp and web push. The nudge of §4.7 is a mark inside the app, and it
  sends nothing.
- **The cards.** `generative_ui_2.md` owns the AG-UI protocol and the
  template catalog. `projects_ai_chat.md` §29 owns the card policy. This
  spec adds one template, `recordCard`, through that catalog.
- **What each app can do in chat.** `projects_agent_parity.md` (WS-46) and
  each app's spec own the tools. This spec says only that a tool's result
  renders in the main chat, and names no new app tool except `show_in_app`.
- **The shell.** `navigation_shell.md` owns the bar, the sidebar, the bottom
  nav and the manifest (D89). An app still builds no shell of its own.

---

## 0. The answer in one screen

1. **One door, three sizes.** The assistant is one element, and it is never
   gone. On a desktop it is closed, a side panel or the full view, as today.
   On a phone it is closed, a **peek strip** above the bottom nav, or the
   full sheet. The peek strip is the phone's side panel: the chat stays in
   reach while the member looks at a page.
2. **The phone opens on the chat.** With the flag on, a phone that lands on
   `/` opens the assistant as the full sheet. The day's brief is its first
   turn. The apps are one tap away, in the Menu drawer and in the chat's own
   pills. A senior member never has to find a tab.
3. **The chat guides.** The assistant gets one tool, `show_in_app`. It opens
   an in-app path for the member and says so in the chat. On a desktop the
   panel stays open beside the page. On a phone the sheet drops to the peek
   strip. The page shows, and the chat is one tap away.
4. **The chat shows the record.** One template, `recordCard`, draws a task,
   a project, a contact or an email as a card in the chat. Its acts are
   buttons. The acts go through the card gate that exists today. A member
   who never opens an app can still finish the work.
5. **The first words are the member's own data.** The starter pills come
   from the member's My Day feed, not from a fixed list. A fresh chat on a
   new day opens with the brief that My Day already composes.
6. **A nudge once, never twice.** A member who has never sent a message sees
   one callout at the entry. It sits under the field on a desktop, and above
   the Ask tab on a phone. The first message or one tap retires it, on the
   server, for every device.

---

## 1. The owner's ask (2026-10-20)

The owner asked for an evaluation of how a member reaches the AI chat, and
for a design. The ask, in its own order:

- Evaluate the four ways in: the top bar, the panel that slides out, the
  work inside the panel, and the full view.
- Design a UI that makes a member prefer the chat over the traditional UI.
- On a phone, put the chat first, and draw the UI a member needs inside the
  chat, as the need comes up.
- Make one whole experience for a first-time member, and for a member who
  wants to avoid the full UI. They must be able to do everything through
  the chat.
- Add a proactive chat bubble or notice, in the owner's words "Click here
  to start chatting".
- When the chat must open one UI part, such as a subproject or a task, the
  chat guides the member there.
- Put the phone first, so that a senior member can finish their work in the
  chat window alone.

The owner added: on a phone, the main experience can be the chat, instead of
taps through buttons and the UI.

This follows the owner's rule of 2026-10-20 for app workflows. Every app
workflow runs end to end in the chat. A read asks nothing. A write asks once,
on one card that shows the exact payload. The card renders in the main chat.
`email_app_master_plan.md` EM-CR, PR #1191, is the first example.

---

## 2. Scope and non-goals

### 2.1 In scope

- A third size of the assistant on a phone, the peek strip (§4.1).
- The phone opens on the chat, and the welcome question fits a phone (§4.2, §4.7).
- One nudge for a member who has never chatted (§4.7).
- One tool, `show_in_app`, and the client rule that acts on it (§4.4).
- One template, `recordCard`, with the acts of a task, a project, a contact
  and an email (§4.5).
- Starter pills from the member's feed, and the brief as the first turn of a
  fresh chat (§4.6).
- One flag, `NEXT_PUBLIC_CHAT_FIRST`, and the fences of §6.

### 2.2 Non-goals — named so nobody builds them from this file

- **No second chat.** The peek strip, the sheet and the panel are one
  element, `AssistantDock.tsx`. `seams.test.ts` allows one dock, and this
  spec keeps it at one.
- **No new agent and no new seam.** `show_in_app` is a tool of
  `orchestrator`. The event it sends rides the AG-UI `custom` channel that
  `artifact_created` rides today.
- **No app rebuilt as cards.** `recordCard` shows one record and its acts.
  A board, a calendar grid and an inbox stay pages. The chat opens them with
  `show_in_app`, and the templates of `generative_ui_2.md` §3 draw a list
  when a list is the answer.
- **No push and no WhatsApp message.** The nudge is in the app. Reach owns
  the rest.
- **No change to the desktop forms.** The side panel and the full view stay
  as D102 set them. The peek strip is a phone size only, by default
  (D-CF-1).
- **No model in the nudge or the brief.** The nudge is a fixed string. The
  brief is the MD-7 composition that My Day already makes.

---

## 3. What exists today — measured 2026-10-20

The four ways the owner named, plus the phone. Each row says what works and
what does not. The anchors are the fact, and the specs are the record.

| Way in | What exists | What is missing |
|---|---|---|
| **The top bar** | `AskField` in the middle of the shell bar on every desktop page (`src/lib/shell/ChatBar.tsx`). Enter sends the words and opens the panel. Enter on an empty field opens it. ⌘J opens it. The panel button is first of the right group. The context chip names the app and the record | The field looks like a search box. Nothing says the assistant can act. The chip hides below `lg`. No callout for a member who has never used it |
| **The panel sliding out** | The side panel, 400 px, drag 320 to 720, with the fold motion (`AssistantDock.tsx`, `lib/panelMotion.ts`). Below 1280 px it floats. Every open from closed is the panel. A closed panel stays mounted, so a run survives | A member who misses the panel button and ⌘J never meets the panel. Nothing opens it for them, not even once |
| **Working in the panel** | The shared `AgentChat`, with cards, pills, files and the session picker. Each turn carries the app and its facts (`surfaceContext`). A delegation to another app moves the page, once per run (`switchTarget`) | The chat cannot open a page on purpose. The page moves only as a side effect of a delegation. A pill opens its page, but the chat cannot say "I opened it". No template draws one record with its acts, so a task detail still needs the app |
| **The full view** | The Maximize control, or `/chat`. It covers the page column. A page the member opens makes it give way to the panel (`onPageChange`) | None. The full view is right as D102 left it |
| **The phone** | No top bar. A bottom nav with Menu, the app's tabs and an Ask tab at the right end. Ask opens the full sheet with the cursor in the composer (`AppShell.tsx`, `data-ask-tab`). The sheet covers the nav | The phone lands on My Day or the app, not on the chat. The sheet has two states, covering or gone. A pill link closes the sheet, so "the chat guides" is a one-way door. A delegation moves the page under the sheet, unseen. The welcome dialog asks about the sidebar, and a phone has no sidebar |
| **The empty chat** | Three starter pills from a fixed list per agent (`AgentChat.tsx`, `AGENT_SUGGESTIONS`). Follow-ups come from `/api/chat/suggestions` after a turn | The first three say nothing about this member. "Summarize recent activity across the company" is the same for a founder and a new hire |

Three more facts the design rests on.

- **The brief exists.** My Day composes one brief a day (`my_day_feed.md`
  MD-7), and Reach sends the same brief on WhatsApp (`proactive_reach.md`
  §9.1). "One composition serves both." The chat is the third reader.
- **The card gate exists.** Every write in chat goes through
  `request_confirmation`, and the card folds to its outcome
  (`projects_ai_chat.md` §29). `recordCard` adds acts, not a gate.
- **In-app links are checked.** `src/lib/inAppLink.ts` tells an in-app path
  from a link that leaves the app, and `EntityPill` links only the first.
  `show_in_app` reuses that check.

---

## 4. The model

### 4.1 Three sizes of one element

The assistant is one element in the tree, and it changes its classes, never
its place. Today it has two forms on a desktop and one on a phone. This spec
adds one size on the phone.

| Surface | Closed | Middle | Open |
|---|---|---|---|
| Desktop | Closed, mounted after the first open | The side panel (`dock`) | The full view (`full`) |
| Phone | Closed, mounted after the first open | **The peek strip** (`peek`), new | The full sheet |

**The peek strip.** A row above the bottom nav, 56 px tall, the full width.
It holds three things: the assistant's mark, one line of text, and a
chevron. The line is the last assistant sentence while the chat is idle. It
is the run status row while a run goes on (`chat_run_continuity.md` §4.10),
so the strip and the chat say the same thing.

A tap anywhere on the strip opens the full sheet. A swipe down on the
sheet's handle drops it to the strip. The X on the sheet closes the
assistant, as today.

**Why a strip, and not a bubble.** A floating button met the Calendar, Focus
and recording buttons once (`navigation_shell.md` §7.1), and the owner chose
the Ask tab. The strip takes the place the `AgentPill` already takes above
the nav (`RunSignals.tsx`), so it meets nothing. The page keeps its scroll
and its `pb-nav` clearance grows by the strip, as it does for the pill
(`:root[data-agent-pill] .pb-nav` in `globals.css`).

**When the sheet drops to the strip, by itself.**

- The chat opened a page with `show_in_app` (§4.4).
- A pill in the chat opened its page. Today the sheet closes (`onChatLink`).
  With the flag on, it drops to the strip instead.
- A delegation moved the page to another app (`switchTarget`). Today the
  page moves unseen under the sheet.

**When the strip goes back up, by itself.** A card waits for the member
(`needs input`, WS-51 S2), and the member is on a page. The sheet opens,
because a card nobody sees is a run that never ends. Nothing else opens the
sheet without a tap.

**The desktop keeps two forms.** D-CF-1 proposes no peek on a desktop. The
side panel already keeps the chat in reach beside the page. A third desktop
form would make the one press open three different things. That is the
fault D102 removed.

### 4.2 The phone opens on the chat

With `NEXT_PUBLIC_CHAT_FIRST` on, a phone that lands on `/` opens the
assistant as the full sheet, once per browser session. The page under it is
My Day, as today. The bottom nav stays under the sheet, and the Menu drawer
and the Ask tab work as today.

- **Once per session.** `sessionStorage` holds the mark. A member who drops
  the sheet to the strip, or closes it, is not reopened on the next `/`.
- **Not on a deep link.** A phone that lands on `/projects?task=…` opened a
  link somebody sent. It shows the task. The chat stays as the Ask tab.
- **Not on a desktop.** A desktop lands on My Day with the field in the bar.
  D-CF-2 proposes no auto-open on a desktop, and §4.7 gives the desktop its
  nudge instead.
- **The first turn is the brief** (§4.6), so the sheet opens with something
  to read, not an empty room.

The member then does the day's work in the chat. The brief's lines are pills
and cards. "Reply to Priya" opens the reply card of EM-CR in the chat.
"3 tasks due today" shows the three as a `taskBoard` or a `recordCard` each.
"Open the board" calls `show_in_app`, and the sheet drops to the strip.

### 4.3 The desktop: the field says what it does

The field stays where D102 put it. Two small changes.

- **The placeholder rotates.** "Ask Metorite, or search and open anything…"
  stays the first. Every eight seconds, while the field is empty and
  unfocused, it shows one of three lines drawn from the member's apps. For
  example: "Ask me to reply to an email", "Ask me what is stuck in
  Projects", "Ask me to plan your week". The lines come from a fixed map
  keyed by the panes the member holds (`heldPanes`). No model writes them.
  A focus stops the rotation. A member who asked for less motion sees the
  first line only (AGENTS.md rule 13).
- **The chip shows from `md`,** not from `lg`. The chip is what tells the
  member the assistant sees their page, and a 1024 px window hid it.

### 4.4 The chat guides: `show_in_app`

One tool of `orchestrator`, in `apps/services/orchestrator/orchestrator/`
beside the specialist tools of `agents.py`.

```
show_in_app(path: str, why: str) -> dict
```

- **`path`** is an in-app path, such as `/projects?task=42` or
  `/email?message=…`. The server refuses a path with a scheme, a host, or a
  `//` start. It also refuses a path to a pane the member does not hold. The
  check is the Python twin of `src/lib/inAppLink.ts`, and a test holds the
  two lists equal.
- **`why`** is one short sentence the member reads in the chat, such as
  "Here is the board for Website relaunch."
- **The result** is an AG-UI `custom` event named `navigate`, with `path`
  and `why`, in the parent run's stream. The tool returns `{"opened": path}`
  to the model, and the model writes nothing more about it.
- **The client** (`useAgentChat.ts`, the `custom` case) emits `onNavigate`
  on `agentEvents`. `AssistantDock.tsx` acts on it, and only while the member
  watches that chat, as it does for a delegation. It checks the path again
  with `isInAppPath`, pushes the route, and then:
  - on a desktop, the panel stays. A full view becomes the side panel, as
    for a delegation. One toast says "Opened Projects · Go back".
  - on a phone, the sheet drops to the peek strip. The strip's line is
    `why`. A tap brings the chat back.
- **Once per turn.** A run may call `show_in_app` once per assistant turn.
  A second call in the same turn returns an error to the model and moves
  nothing. A page that jumps twice is a page the member cannot read.
- **A reconnect replays nothing.** The event is marked as the delegation
  events are, and a replay of the stream moves no page.
- **The persona says when to use it.** One line joins `ASSISTANT_PERSONA`.
  The line is: "When the member asks to see a page, a list or a record, call
  show_in_app with its path. Then say what they will see, in one sentence."
  The routing eval (`evals/routing`) gains three cases.

**Why a tool, and not a link in the text.** A link waits for a click, and a
senior member on a phone may not find it under their thumb. The owner asked
for the chat to guide. A tool moves the page and says so, and the strip
keeps the way back one tap away.

### 4.5 The chat shows the record: `recordCard`

One template joins the catalog of `generative_ui_2.md` §3, drawn by
`genUITemplates.tsx` and emitted through `emit_generative_ui`.

```json
{
  "template": "recordCard",
  "kind": "task" | "project" | "contact" | "email",
  "id": "…",
  "title": "…",
  "fields": [{ "label": "Status", "value": "In progress", "accent": "status:in_progress" }],
  "acts": [{ "label": "Mark done", "tool": "update_task", "args": { "task_id": "…", "status": "done" } }],
  "path": "/projects?task=…"
}
```

- **The look** is the task card of Projects at its narrow width, with the
  house tokens and the one status vocabulary (`statusAccent.ts`). An app-local
  colour fails `conformance.test.ts`.
- **The acts** are buttons on the card. A tap sends the act as the member's
  turn, in words: "Mark #42 done." The tool then asks its own card, through
  `request_confirmation`, as every write does. `recordCard` adds no gate and
  skips none. One tap to ask, one tap to confirm.
- **"Open"** is the last act. It calls `show_in_app` with `path`.
- **The specialists fill it.** `projects-assistant` returns a `recordCard`
  for one task or one project. `email_assistant` returns one for one email,
  with Reply and Reply all as acts, which open the EM-CR card. `crm-assistant`
  returns one for a contact or a deal. Each specialist's instructions say:
  "When the member asks about one record, show it as a recordCard."
- **Four kinds first.** A kind the card does not know draws the
  `GenUIFallback`, as every unknown template does.

### 4.6 The first words: the member's own data

**The starter pills.** The empty chat shows three pills. With the flag on,
they come from the member's My Day feed, through `/api/chat/suggestions` with
a `starter: true` flag. The route reads the top three feed cards the member
holds. It writes one pill per card, from the card's own headline. For
example: "3 tasks are due today", "Reply to Priya Nair", "The Acme deal has
not moved in 9 days".

A tap sends the headline as a question. With no feed, the pills fall back to
the fixed list. No model writes a starter pill.

**The brief as the first turn.** A fresh chat (`startsFresh`) on a phone,
with the flag on, opens with the day's brief as the first assistant message.
The brief is the MD-7 composition that My Day already holds for today, and
the chat reads it from the same route. It is drawn as a `reportCard`, with
its lines as pills. It is a message of the chat, so it is saved with it, and
the next chat of the day does not repeat it. A day with no brief opens
empty, with the pills.

**Why from the feed.** The owner asked for a member to do everything through
the chat. The first thing the chat must do is tell them what there is to do.
The feed already knows, and it already judged it (D101, D103).

### 4.7 First-time members, and the nudge

**The nudge.** A member who has never sent a message in this organization
sees one callout.

- On a desktop, a small bubble under the field: "Ask me anything. I can read
  your mail, plan your week and move your tasks." Its tail points at the
  field.
- On a phone, the same bubble above the Ask tab, with one line: "Start
  here."
- It retires on the first message sent, or on its own X. The server holds
  the fact in `shell_prefs`, as `chat_first_message_at`. So it never shows
  again on another device. The browser holds a copy, so it never flashes
  before the read.
- It never covers a control, and it never shows while the welcome dialog
  is open.
- One bubble, one string, no model. The words are the product's voice
  (`agent_writing_voice.md`).

**The welcome question on a phone.** Step 2 of the welcome dialog, "Choose
the apps for your sidebar", makes no sense on a phone, which has no sidebar.
With the flag on, a phone skips step 2 and writes the preset's default pins.
Step 1, the role question, stays, because it picks the brief's order too. A
founder's invite step stays.

### 4.8 The chat's chrome on a phone

On a phone the sheet showed three rows above the thread, and three pickers
beside the field (owner, 2026-10-21). A senior member on a phone needs one
header row, one field and one Send, so the reading space goes to the thread.
With the flag on, the sheet keeps the dock's one row: Your chats, the title,
New chat, Minimize and Close. The composer keeps the field, Attach and Send,
and one "…" control at its end holds the rest.

That control holds the status, Share, this chat's files, the effort choice
(D90 keeps it), the context window and the model picker. The context ring
joins the composer only above 75 %, where the compaction band begins. Every
control on the phone is 44 px, and the thread keeps a 16 px gutter at 390 px.
A desktop, and a phone with the flag off, draw what they drew before.

---

## 5. Decisions proposed — each with a default an agent builds to

| # | Question | Default | Why |
|---|---|---|---|
| **D-CF-1** | Does the desktop get a peek size too? | **No.** The peek strip is a phone size | The side panel already keeps the chat in reach. A third desktop form would make one press open three things, the fault D102 removed |
| **D-CF-2** | Does a desktop auto-open the assistant on the first visit? | **No.** The desktop gets the nudge under the field | A desktop member sees the field at all times. An auto-open steals the page they came for |
| **D-CF-3** | Does the phone open on the chat on every visit, or once? | **Once per browser session,** and never on a deep link | A member who closed it said so. A link somebody sent names a page |
| **D-CF-4** | Does `show_in_app` move the page when the assistant is closed? | **No.** Only while the member watches that chat | The same rule as the app switch. A page that moves under a member who writes elsewhere loses their place |
| **D-CF-5** | Does a `recordCard` act write without a card? | **No.** Every act goes through `request_confirmation` | The card policy of `projects_ai_chat.md` §29. The owner's rule of 2026-10-20: a write asks once, on one card |
| **D-CF-6** | Where does the brief come from? | **My Day's MD-7 composition,** read, never recomposed | One composition serves WhatsApp, My Day and the chat. A second composition costs a model call and can disagree |
| **D-CF-7** | Does the nudge live on the server? | **Yes,** in `shell_prefs` | A nudge that returns on every device is the "proactive bubble" that teaches people to dismiss everything |

The owner accepts, amends or refuses each in H-369. A refusal of D-CF-1 or
D-CF-2 changes CF-1 and CF-2 only.

---

## 6. Tickets

Each ticket names its files, its "done when", and its fence (R7). Each is
AGENT-SAFE to build dark. The order is the order of value to a phone member.

### CF-1 · The peek strip — AGENT-SAFE · BUILT 2026-10-20 (#1198), dark

Built as the spec says, with three additions from the review. An open that
names a chat, a draft or words raises the strip to the sheet
(`modeOnOpen`). A finished run shows the last reply in the strip, not the
status. The `RecordingDock` of Notes sits at the strip's height, and nobody
has looked at the two together. Not proven in a browser: the raise on a
waiting card, which only unit tests cover.

**Files.**

- `src/lib/shell/assistant.ts`: a third `AssistantMode`, `peek`. The rules
  `onChatLink` and `onPageChange` learn it, and a new `onNavigate` joins.
- `src/lib/shell/AssistantDock.tsx`: the strip, the swipe, the shape.
- `src/lib/panelMotion.ts`: a `peek` form.
- `src/app/globals.css`: `.assistant-panel[data-panel-form="peek"]`, and the
  `pb-nav` clearance.
- `src/lib/shell/RunSignals.tsx`: the `AgentPill` yields to the strip with
  the flag on.

**Done when.**

1. On a phone with the flag on, a swipe down on the handle shows the strip
   above the nav. The chevron does the same. The page's
   `pb-nav` grows by it. A tap on the strip opens the sheet. The chat keeps
   its scroll and its draft.
2. A pill link in the chat drops the sheet to the strip, and the page shows.
   With the flag off, the sheet closes, as today.
3. While a run goes on, the strip's line is the run status row's text. While
   the chat is idle, it is the last assistant sentence, truncated.
4. A card that waits for the member raises the strip to the sheet.
5. The X closes the assistant from the strip and from the sheet.
6. A desktop draws no strip. `effectiveMode` returns `peek` only on a phone.

**Fences.** `assistant.test.ts` holds the pure rules: `effectiveMode` with
`peek`, `onChatLink` with the flag, the raise on `needs input`.
`e2e/assistant-motion.spec.ts` gains one phone test: Ask, swipe, strip,
tap, sheet, X. `seams.test.ts` stays at one dock. Mutation, seen to fail
first: return `"close"` from `onChatLink` with the flag on, and the e2e fails
at the strip.

### CF-2 · The phone opens on the chat, and the nudge — AGENT-SAFE

**Files.**

- `src/components/AppShell.tsx`: the open on `/`.
- `src/lib/shell/assistant.ts`: `opensOnLanding`, pure.
- `src/lib/shell/ChatBar.tsx` and the bottom nav: the nudge bubble, as one
  component in `src/lib/shell/`.
- `src/lib/shell/shellPrefs.ts` and its route: `chat_first_message_at`.
- `src/components/WelcomeDialog.tsx`: step 2 skipped on a phone.

**Done when.**

1. A phone with the flag on that lands on `/` shows the sheet open, once per
   browser session. A deep link, a desktop, and the flag off open nothing.
2. A member with `chat_first_message_at` null sees the bubble. It sits under
   the field on a desktop, and above the Ask tab on a phone. The first sent
   message writes the time, and the bubble goes. The X writes it too. A
   reload on another browser shows no bubble.
3. The bubble never shows while the welcome dialog is open.
4. On a phone with the flag on, "Done" on step 1 of the welcome dialog
   writes the preset's pins. It shows no step 2.

**Fences.** `assistant.test.ts` (`opensOnLanding`: path, phone, flag, the
session mark). `shellPrefs.test.ts` (the new field reads and writes).
`welcomeSteps.test.ts` (step 2 skipped on a phone). `e2e/shell-presets.spec.ts`
gains a phone case. Python twin: the `shell_prefs` route test gains the
field, and `test_auth_me_shell.py` if the field rides `/auth/me`.

### CF-3 · `show_in_app` — AGENT-SAFE · BUILT 2026-10-20 (#1197), dark

Built as the spec says, and hardened in one review round. Both path checks
decode each segment once and refuse `.`, `..`, an empty segment and a
decoded slash, so `/projects/../crm` moves nothing. The shared table is
`src/lib/inAppPaths.cases.json`, and `tests/unit/test_in_app_path_twin.py`
holds the two checks equal. The server allowlist `PANES` holds the live
panes only, and a test holds it equal to `nav.ts`. A nested run refuses the
tool, and the WhatsApp channel withholds it. The server offers the tool only
with `CHAT_FIRST_TOOLS` on. One cost: the stricter client check also
governs chat links, so a link with a trailing slash opens as a plain link.
No server code makes such a link.

**Files.**

- `apps/services/orchestrator/orchestrator/agents.py`: the tool.
- A new `apps/services/orchestrator/orchestrator/in_app_path.py`: the check.
- `src/hooks/useAgentChat.ts`: the `custom` case emits `onNavigate`.
- `src/lib/agentEvents.ts`: `onNavigate`.
- `src/lib/shell/AssistantDock.tsx`: the act.
- `src/lib/shell/assistant.ts`: one line in `ASSISTANT_PERSONA`.
- `evals/routing`: three cases.

**Done when.**

1. The tool refuses `https://…`, `//…`, and a path with a host. It refuses
   a path to a pane the member does not hold. It returns an error to the
   model and sends no event.
2. A good path sends one `custom` event `navigate` in the parent run's
   stream, through a delegation too.
3. On a desktop with the assistant open on that chat, the page moves. A full
   view becomes the panel, and one toast offers "Go back". With the
   assistant closed, nothing moves.
4. On a phone, the sheet drops to the strip, and the strip reads `why`.
5. A second call in one turn moves nothing and returns an error.
6. A reconnect replay moves nothing.

**Fences.** `tests/unit/test_show_in_app.py`: the refusals, the one-per-turn
rule, the event through a delegation (the shape of
`test_delegated_confirmation_card.py`). `tests/unit/test_in_app_path_twin.py`:
the Python check and `inAppLink.ts` agree on a shared table of paths.
`assistant.test.ts` (`onNavigate`). `runSignals.test.ts` or `agentEvents`
test: a replay emits no `onNavigate`. Mutation, seen to fail first: let the
server accept `//evil.example`, and the twin test fails.

### CF-4 · Starter pills from the feed — AGENT-SAFE

**Files.** `src/app/api/chat/suggestions/route.ts` (the `starter` branch),
`src/components/AgentChat.tsx` (the empty state reads it with the flag on),
the My Day feed read it reuses (`src/lib/shell/feed.ts`).

**Done when.**

1. With the flag on, the empty chat's three pills are the top three feed
   headlines the member holds. A tap sends the headline.
2. With no feed, or the flag off, the fixed list shows.
3. No model call is made for a starter pill. The route's test counts zero.

**Fences.** The route's test (new or extended). `feed.test.ts` for the
pick. An e2e stub of the feed with three cards, and the pills read them.

### CF-5 · The brief as the first turn — AGENT-SAFE

**Files.**

- `src/lib/shell/AssistantDock.tsx` or `useAssistantChat`: a fresh chat on
  a phone asks for the brief.
- The brief's route in My Day (`my_day_feed.md` MD-7).
- `src/components/genUITemplates.tsx`: `reportCard` with pills.
- The server save of a system turn. D-PM-39 says only the server creates an
  agent row, so a route posts the brief, not the client.

**Done when.**

1. A fresh chat on a phone with the flag on opens with today's brief as the
   first assistant message. It is drawn as a `reportCard` with its lines as
   pills.
2. The second fresh chat of the same day does not repeat it.
3. A day with no brief opens empty, with the pills of CF-4.
4. The brief is the MD-7 text. A diff against My Day's card is empty.

**Fences.** A Python test on the route that posts the brief turn (D-PM-39
holds, so the client sends no agent row). `chatStore` test for the once-a-day
mark. An e2e with a stubbed brief.

### CF-6 · `recordCard` — AGENT-SAFE

**Files.** `src/components/genUITemplates.tsx` (the template),
`packages/acb_skills/acb_skills/write_artifact.py` (the catalog line of
`emit_generative_ui`, §25 of `projects_ai_chat.md`), the instructions of
`projects-assistant`, `email_assistant` and `crm-assistant` (one line each).

**Done when.**

1. A `recordCard` for a task draws the title, four fields, the status chip
   and the acts. It holds at the panel width and at 390 px.
2. An act sends the member's turn in words, and the tool's own card follows.
   No write happens without that card.
3. "Open" calls `show_in_app`.
4. An unknown `kind` draws `GenUIFallback`.
5. "Show me task #42" in the routing eval returns a `recordCard`.

**Fences.** `genUITemplates` test for the four kinds and the fallback.
`conformance.test.ts` unchanged and green. The routing eval case. The visual
rig (`.claude/skills/visual-review`) at 390, light, compact, and a changed
accent, with the captures in the PR.

### CF-7 · The field says what it does — AGENT-SAFE

**Files.** `src/lib/shell/ChatBar.tsx` (the rotating placeholder, the chip
from `md`).

**Done when.**

1. An empty, unfocused field cycles three lines every eight seconds, drawn
   from the panes the member holds. A focus stops it. Reduced motion shows
   the first line only.
2. The chip shows at 1024 px.

**Fences.** `oneBar.test.ts` or a new `askField.test.ts` for the line map.
`e2e/shell-bar.spec.ts` for the chip at 1024.

### CF-8 · The phone chat chrome — AGENT-SAFE · BUILT 2026-10-21 (#1205), dark

**Files.**

- `src/components/ChatMoreMenu.tsx`: the one "…" control, new. It is
  `AnchoredPanel` and `Button` rows from `src/components/ui/`, at 44 px.
- `src/components/AgentChat.tsx`: the `phoneChrome` and `moreActs` props.
  The agent row and the Share row go, the pickers leave the composer, and
  the ring waits for 75 %.
- `src/components/ChatSendButton.tsx`: the `large` size.
- `src/lib/shell/AssistantDock.tsx`: the prop with the flag, the header's
  tap size, and Files into the control.

**Done when.**

1. On a phone with the flag on, the sheet has one header row above the
   thread. The agent row and the Share row are gone. Share, the status and
   this chat's files sit in the "…" control, so Share stays reachable.
2. The composer shows the field, Attach, the "…" control and Send. The
   effort choice, the context ring and the model picker sit in the control.
   The ring joins the composer at 75 %.
3. Every control on the phone is 44 px. The thread's gutter is 16 px at
   390 px, and a turn takes the full width between.
4. The sheet passes `phoneChrome` only with the flag on. `compact` stays
   `form === "dock"`, so the room stays on.
5. A desktop, and a phone with the flag off, draw what they drew before.

**Fences.** `assistant.test.ts` ("the chat's chrome on a phone") reads the
source: the prop with the flag, `compact` as it was, the two rows, the one
control, the ring's floor and the tap size. `e2e/assistant-motion.spec.ts`
gains one phone test with the flag on, and the flag-off test asserts the two
rows still show. `seams.test.ts` stays at one dock. Mutation, seen to fail
first: pass `compact={form === "dock" || phone}` in the dock, and the fence
fails.

### Order

CF-1, then CF-2 and CF-3 in parallel, then CF-4 and CF-5, then CF-6, then
CF-7. CF-8 follows CF-1, because it builds on the sheet's one row. CF-1 to
CF-3 give a phone member the whole loop: open on the chat, work in it, see
a page, come back. CF-4 to CF-6 make the chat worth opening.

---

## 7. Flags

| Flag | Where | Default | Who flips it |
|---|---|---|---|
| `NEXT_PUBLIC_CHAT_FIRST` | Build time, `workbench/control_plane/.env.local` on the box | off | **Owner** in production (`navigation_shell.md` §13.1 names every `NEXT_PUBLIC_SHELL_*` flag, and this one joins that rule). An agent may turn it on in staging |

It reads through one function, `chatFirstOn()` in `src/lib/shell/chatFlag.ts`
beside `shellChatOn`, with the same dev override. It needs `NEXT_PUBLIC_SHELL_CHAT`
on, because every piece of it hangs on the one assistant.

---

## 8. Verification commands

```sh
cd workbench/control_plane
npx vitest run src/lib/shell/assistant.test.ts src/lib/shell/shellPrefs.test.ts src/lib/shell/welcomeSteps.test.ts src/lib/shell/seams.test.ts
npx tsc --noEmit
npx playwright test e2e/assistant-motion.spec.ts --project=chromium
cd ../..
uv run pytest tests/unit/test_show_in_app.py tests/unit/test_in_app_path_twin.py tests/unit/test_delegated_confirmation_card.py
python -m evals.routing --scripted
```

Then, by eye, with the visual rig: the strip at 390 px in light mode and at
compact density. The nudge under the field at 1440 px, and above the Ask tab
at 390 px. A `recordCard` at 390 px and in the panel at 400 px.

---

## 9. Owner gates and open questions

### 9.1 Gates — refuse these by name

| Act | Why | Who |
|---|---|---|
| Turn `NEXT_PUBLIC_CHAT_FIRST` on in production | A build-time flag reaches every organization at once, and it changes the first screen a phone member sees | Owner only |
| Accept or refuse D-CF-1 to D-CF-7 | Product shape | Owner, in H-369 |

### 9.2 Open — each has a default

| # | Question | Default |
|---|---|---|
| Q1 | Does the brief open the chat on a desktop too, as the first turn of a fresh chat? | No. The desktop member sees the brief on My Day. Add it when a desktop member asks |
| Q2 | Does the strip show on `/chat` itself? | No. `/chat` is the full view's address, and it draws no strip |
| Q3 | May a specialist call `show_in_app` directly? | No. Only `orchestrator` holds the tool, so the page moves once and from one place |
| Q4 | Does the nudge return after 30 days of silence? | No. One nudge, ever, per member per organization |

---

## 10. File paths

| What | Path |
|---|---|
| The one assistant store and rules | `workbench/control_plane/src/lib/shell/assistant.ts` |
| The one dock | `workbench/control_plane/src/lib/shell/AssistantDock.tsx` |
| The field and the work area | `workbench/control_plane/src/lib/shell/ChatBar.tsx` |
| The shell and the bottom nav | `workbench/control_plane/src/components/AppShell.tsx` |
| The run signals and the agent pill | `workbench/control_plane/src/lib/shell/RunSignals.tsx` |
| The event bus | `workbench/control_plane/src/lib/agentEvents.ts` |
| The stream parser | `workbench/control_plane/src/hooks/useAgentChat.ts` |
| The in-app link check | `workbench/control_plane/src/lib/inAppLink.ts` |
| The templates | `workbench/control_plane/src/components/genUITemplates.tsx` |
| The starter pills | `workbench/control_plane/src/components/AgentChat.tsx`, `src/components/SuggestionPills.tsx` |
| The phone's "…" control (CF-8) | `workbench/control_plane/src/components/ChatMoreMenu.tsx` |
| The suggestions route | `workbench/control_plane/src/app/api/chat/suggestions/route.ts` |
| The welcome dialog | `workbench/control_plane/src/components/WelcomeDialog.tsx`, `src/lib/shell/welcomeSteps.ts` |
| The shell preferences | `workbench/control_plane/src/lib/shell/shellPrefs.ts` |
| The orchestrator's tools | `apps/services/orchestrator/orchestrator/agents.py` |
| The GenUI catalog | `packages/acb_skills/acb_skills/write_artifact.py` |
| The phone e2e | `workbench/control_plane/e2e/assistant-motion.spec.ts` |
