# The glass look — Aroh's design language across Metorite

**Status: ACTIVE, 2026-10-31. Verified against code on 2026-10-31.** Owner
directive of 2026-10-31. Decision **D143**. Board row **WS-UX** (phase GL).
Metorite task #1125, under epic 492.

**GL-0 to GL-4 are BUILT and GL-9 is DONE, 2026-10-31.** The gate and the
tokens (GL-1), the primitives (GL-2), the shell (GL-3) and the chat (GL-4)
are LIVE in production since 05:38 UTC (deploy run 41170905098, commit
`997ea6e0a`). The owner said in chat: "staging doesn't exist anymore. You
can push to production directly." Next: GL-5 (the tint) and GL-6 (one pass
per app). §9 holds three questions for the owner.

**GL-10b is BUILT on its branch, 2026-10-31, not yet merged** (task #1233). It
takes the rest of the chat into glass: the session list, the empty state, the
hint row, the action bar, the artifact panels, and the colour edge. The edge
is a glow element that always draws round the composer and round the top-bar
Ask field. Its `data-glow` is `rest`, `live` or `waiting`.

---

## 0. The answer in one screen

The owner withdrew the plan to run Aroh as a separate app (2026-10-31). Aroh's
interface comes into Metorite instead. Aroh follows Apple's current interface
language: content fills the window, and the controls that steer it float above
it as translucent glass. The owner calls this cleaner and more modern than the
current Metorite look.

The owner set three conditions on the same day:

1. **Do it systematically.** Every app and every sub-app shows the new look.
2. **Make it revertable.** If we do not like it, we go back.
3. **Staging first.** Production does not change until the owner says so.

A merge reaches production every night at 21:30 UTC (WS-38). So condition 3
cannot hold by "do not merge". It holds by a **gate**:

- The new look lives beside the current one. Its tokens and classes apply
  only under `<html data-look="glass">`.
- A build-time gate, `NEXT_PUBLIC_UI_LOOK=glass`, lets the server render
  that attribute. Staging carries the line. Production carries no line,
  so production renders the current look, byte for byte.
- On staging, Settings → Appearance shows a **Design** switch (New or
  Classic). The owner compares both looks on the same data, in one click.
- When the owner approves, production gets the line (GL-9, OWNER-GATE).
  After a settling period, one contract slice deletes the classic look
  (GL-Z). Then there is one look again, as `DESIGN_SYSTEM.md` §0 requires.

The names in this spec:

| Name | Meaning |
|---|---|
| **classic** | The look in production today. `globals.css` `:root` and `.light` |
| **glass** | The new look, lifted from Aroh (`../Aroh/apps/web/src/index.css`, `../Aroh/docs/design.md`) |
| **the gate** | `NEXT_PUBLIC_UI_LOOK`. Unset or any value other than `glass` means classic |
| **the attribute** | `data-look="glass"` on `<html>`. The server renders it when the gate is on. The boot script and the store remove it for a member who chose Classic |
| **the variant** | The Tailwind variant `glass:`. A class behind it applies only under the attribute |

---

## 1. Scope and non-goals

**In scope.**

- The tokens: colour, surfaces, type, corners, shadows, materials, motion.
- The primitives in `src/components/ui/`.
- The shell: sidebar, top bar, `AppTopBar`, the page frame, the assistant
  dock and the phone layout.
- The chat: composer, messages, tool rows, thinking, cards, streaming.
- The tint: how the organization's highlight colour shows.
- One pass over each live app, so that no app looks foreign in glass.

**Not in scope. Each has its own owner.**

| Item | Owner |
|---|---|
| The artifact library: its data, scopes, retention | `artifact_management.md` (WS-60). GL-7 only sets its look |
| Aroh's tools and skills in Metorite chat | A separate spec under WS-STU and WS-67 (GL-8 names it) |
| The Operator Console | It is exempt from the look (`work_plan.md` D35.4). It keeps its own |
| The generated-app sandbox (`--cc-*`) | `DESIGN_SYSTEM.md` §5. GL-1 may map glass values into it, and changes no contract name |
| The brand mark | The Metorite comet stays (`DESIGN_SYSTEM.md` §1, exception 4). Aroh's "A" mark does not come across |
| Product copy | `DESIGN_SYSTEM.md` §10. GL changes no word a customer reads, except labels that the new controls need |

**Non-goals.**

- No second theming engine. There are two looks for a bounded time, and then
  one. No third look, and no per-org look.
- No new runtime dependency. The Inter face comes through `next/font`, as
  Geist does (§4.4).
- No change to what a page does. GL changes how it looks and how it moves.
- No rewrite of an app's layout beyond what the glass frame needs. An app
  that needs more goes on the board as its own row.

---

## 2. Decision D143 (owner, 2026-10-31)

`work_plan.md` §3 holds the record. In short:

1. **Aroh does not ship as a separate app.** Its interface, its chat
   patterns and later its tools come into Metorite. The Console seats for
   Aroh (tasks 1105 and 1109) stop.
2. **Glass becomes the one look**, after the owner approves it on staging.
   Until then, classic is the default and the gate picks.
3. **Production changes only on the owner's word.** An agent never sets the
   gate on production (OWNER-GATE, GL-9).
4. **Colour means something.** The tint is the organization's highlight
   colour. It marks only what a member can act on, what is selected, and the
   member's own chat bubble. State takes the status colours. An icon tile
   takes a system colour. Nothing is coloured for decoration.
5. **The artifact library takes Aroh's Files design** (WS-60 AM-2).
6. **Aroh's tools come in through the tool-pack seam** (WS-67), in their own
   spec.

---

## 3. What Aroh's design is (read 2026-10-31)

The source of record is `../Aroh/docs/design.md` and
`../Aroh/apps/web/src/index.css`. This section names what GL lifts. Do not
copy Aroh's code blind: its app is Vite and single-user, ours is Next.js and
multi-tenant.

| Part | Aroh | Classic Metorite |
|---|---|---|
| Surfaces, light | White chat, grouped grey `#f2f2f7` pages, white cards | White page, white cards, borders |
| Surfaces, dark | Black page, `#1c1c1e` cards | Blue-grey `hsl(220 13% 8%)` page |
| Default mode | Light (the OS decides) | Dark |
| Tint | Apple blue: `#0068d6` light, `#0a84ff` dark | Sky `hsl(198 89% 50%)`, and orange `--accent` |
| Fills | Translucent grey, as Apple's are | Opaque blue-grey |
| Status | Apple's higher-contrast red, orange, green | Own set |
| Icon tiles | Solid Apple system colours, white glyph | None |
| Type | SF Pro on Apple, Inter elsewhere. Named iOS sizes, 12 px minimum | Geist. Tailwind sizes, 10 and 11 px exist |
| Corners | 6 · 8 · 12 · 18 · 22 · 28, capsule controls, concentric | One `--radius` of 12 px |
| Cards | Opaque, a hairline and a soft lift, no border | A border |
| Chrome | Floating glass: blur 28 px, saturate 180 %, lit top edge | Opaque bars with borders |
| Motion | Apple spring curve `cubic-bezier(0.32, 0.72, 0, 1)`, a press dips to 0.96 | 200 ms ease |
| Chat | Owner bubble in the tint, answer full width at 17 px, glass capsule composer, a spectrum glow while a run is live | See GL-4 |

---

## 4. The mechanism

### 4.1 The gate

`NEXT_PUBLIC_UI_LOOK` is read at build time, so the server render and the
browser agree. Values:

| Value | Effect |
|---|---|
| unset, or anything but `glass` | Classic. The boot script never sets the attribute. The Design switch does not show |
| `glass` | Glass is the default. The Design switch shows in Settings → Appearance |

**Why build time, not a per-org flag.** A per-org flag reaches the browser
after the first paint, so every load would flash classic first. The boot
script runs before paint and reads only build-time values and local storage.
A per-org rollout is not needed: the owner asked for staging, then
production.

**A new staging box.** `deploy/hostinger/bootstrap_staging.sh` does not
write the gate. A rebuilt staging box renders classic until someone adds the
line to `workbench/control_plane/.env.local` and rebuilds.

**Why staging keeps its line.** `scripts/staging_refresh.sh` §6 copies each
`NEXT_PUBLIC_*` key that production HAS onto staging's `.env.local`. It never
deletes a line. Production has no `NEXT_PUBLIC_UI_LOOK` line, so staging keeps
its own. ⚠️ Never write `NEXT_PUBLIC_UI_LOOK=classic` on production. The
nightly refresh would copy it onto staging and turn glass off there.

### 4.2 The attribute and the member's switch

`src/app/layout.tsx` renders `data-look="glass"` on `<html>` when the gate is
`glass`, so the default never flashes. `src/lib/theme/boot.ts` removes it
before paint when the member has chosen Classic, and the store
(`store.ts`, `applyLook`) sets or removes it when the member switches. The member's choice lives
under the key `cc-look`, per account, like the other appearance keys
(`storage.ts`, `scope.ts`). The React store owns the switch, and the boot
script reads the same key before paint.

With the gate off, the script never reads `cc-look` and never sets the
attribute. A browser that holds `cc-look=glass` from staging still gets
classic in production.

### 4.3 The CSS

All in `src/app/globals.css`. The classic blocks do not change.

1. **Token blocks.** `html[data-look="glass"]` holds the dark values and
   `html[data-look="glass"].light` the light values. Both selectors are more
   specific than `:root` and `.light`, so glass wins only under the attribute.
   The density and accent inline styles still win over both.
2. **New tokens** that classic does not have: `--background-grouped`,
   `--field`, `--segment`, `--system-*`, `--glass-*`, `--card-shadow`,
   `--glow-*`. Each colour token among them has a classic fallback in
   `@theme inline`, so a classic page that meets one by accident renders as
   classic. The material tokens (`--glass-*`, `--glow-*`, `--card-shadow`)
   have no fallback: only the glass-only utilities read them, and those sit
   behind `glass:`.
3. **The variant.** `@custom-variant glass (&:where([data-look="glass"],
   [data-look="glass"] *));` A component writes its glass difference as
   `glass:<class>`. Under classic the class matches nothing.
4. **Utilities.** GL-1 adds `glass-chrome` (floating chrome), `glass-menu`
   (menus and popovers), `glass-surface` (content cards), `glass-press` and
   `glass-scroll-edge`. GL-4 adds the working glow, the stream caret, the
   typing dots and the shimmer, each with the `glass-` prefix. A component
   uses each one only behind `glass:`, or inside a component that renders
   only in glass. §6 fences this. GL-1 also restyles the existing
   `tech-glass` and `tech-glass-subtle` under the attribute, so a surface
   that wears them needs no `glass-menu` on top.
5. **Named type styles.** `text-caption` (12), `text-footnote` (13),
   `text-label` (14), `text-subhead` (15), `text-callout` (16), `text-body`
   (17), `text-headline` (19), `text-title` (22), `text-title-lg` (28) and
   `text-largetitle` (34). Each in `rem`, so density still works.

### 4.4 The font

Glass uses `-apple-system` first, so an Apple device shows SF Pro. Every other
device gets Inter, which is the closest open relative. Inter comes through
`next/font/google`, as Geist does, so it adds no package. `layout.tsx` loads
it with `preload: false` and adds its variable only when the gate is on. A
browser downloads a font only when text uses it, so classic does not pay for
Inter.

### 4.5 The revert paths

| Who | How | Time |
|---|---|---|
| One member on staging | Settings → Appearance → Design → Classic | At once |
| All of staging | Delete the gate line from staging `.env.local`, then rebuild | One build, about 10 minutes |
| Production, after GL-9 | Delete the gate line from production `.env.local`, then rebuild | One build |
| The code | `git revert` of any GL pull request. Each one is additive, so classic stays whole | One merge |

Until GL-Z merges, classic stays complete and fenced. GL-Z runs only after the
owner approves production and the look holds there.

---

## 5. The slices

Each slice is a pull request, or a few. Each one is additive until GL-Z. Each
UI slice passes the UI gate of `DESIGN_SYSTEM.md` §11, with the captures
taken in glass, plus one capture in classic that shows no change.

### GL-0 · This spec, D143, the board row — AGENT-SAFE

**Done when** this file is in `INDEX.md`, D143 is in `work_plan.md` §3, and
the WS-UX row names phase GL.

### GL-1 · The gate and the token layer — AGENT-SAFE

The gate (§4.1), the attribute and the switch (§4.2), the CSS (§4.3), the
font (§4.4), and the fences (§6). A `glass` axis in the visual rig
(`e2e/visual/harness.ts`).

**Done when:**

- With the gate unset, `themes.test.ts` passes unchanged, and a capture of
  `/projects` in classic matches the one from before the slice.
- With the gate set, every live app shows glass colours, surfaces, corners
  and type, in light and dark, with no page error.
- The Design switch flips the look with no reload and no flash, and the choice
  survives a reload.
- The contrast gate measures the glass pairs and passes AA.

### GL-2 · The primitives — AGENT-SAFE

Each primitive in `src/components/ui/` takes Aroh's shape behind `glass:`.
`Button` is a capsule with the press dip. Fields have the `--field` fill and a
tinted focus halo. `Badge` is a pill. `Modal` is a 22 px sheet. Menus and
popovers (`AnchoredPanel`, `OptionList`, `SelectButton`) are `glass-menu`.
Toasts are glass. Aroh's `ui.tsx` is the reference for each.

Three controls have no primitive today (survey of 2026-10-31):

- **The segmented control** is `components/Tabs.tsx` with
  `variant="segmented"`. Glass gives it a raised segment on a `bg-secondary`
  track (Aroh's `TabBar`).
- **The switch** has local copies: `Toggle` in
  `app/email/components/automation/ui.tsx`, `Toggle` and an inline switch in
  `app/tasks/components/TaskSettingsModal.tsx`, and an inline `role="switch"`
  in `app/notes/components/MeetingPrep.tsx`. GL-2 adds
  `components/ui/Switch.tsx`, green when on, and moves the copies onto it.
  The classic shape of each copy stays the classic shape.
- **The card** has no component. `glass-surface` on a card's class string is
  the glass card.

**Done when** each primitive renders the Aroh shape in glass and is unchanged
in classic, and `rawControls.test.ts` and the other fences pass.

### GL-3 · The shell — AGENT-SAFE

The files are `components/Sidebar.tsx`, `components/AppShell.tsx` (the frame
and the phone layout), `components/AppTopBar.tsx` and `lib/shell/ShellBar.tsx`.
The sidebar floats as glass, with an 8 px inset and a 22 px corner, and its
rows have 14 px corners. The top bar and each `AppTopBar` lose their border
and use glass buttons. A page sits on the grouped surface. The scroll edge
blurs content as it passes under a bar. The phone drawer and the safe areas
follow `../Aroh/docs/design.md` "Phones and touch".

**Done when** the shell renders glass at 1440 and 390 px in both modes, and
`src/lib/shell/seams.test.ts` passes.

### GL-4 · The chat — AGENT-SAFE

For the one assistant. With `NEXT_PUBLIC_SHELL_CHAT` on (production), `/chat`
opens the assistant's full view, so the live chat is `lib/shell/AssistantDock.tsx`
around `components/AgentChat.tsx`. The parts are `MessageBubble.tsx`,
`MarkdownMessage.tsx` (with the stream caret of `lib/streamCaret.ts`),
`ThinkingContainer.tsx`, `ToolCardShell.tsx`, `ConfirmationCard.tsx`,
`ElicitationCard.tsx`, `ArtifactCard.tsx` and `ChatSendButton.tsx`. In glass:

- the composer is a glass capsule, and the spectrum glow turns round it while
  a run is live;
- the member's bubble takes the tint, and the answer runs full width at body
  size;
- tool calls are compact rows, and thinking folds under "Thinking";
- a card has a solid icon tile. A card that wants something from the member
  is tinted, and a settled card is a plain surface;
- the stream caret sits in the last line, typing dots show before the first
  word, and the "Scroll to bottom" button (`AgentChat.tsx`) is glass. Its
  words do not change.

GL-4 is the only slice that edits `globals.css` and `glassLook.test.ts`
while GL-2, GL-3 and GL-4 run in parallel.

⚠️ WS-CHAT has a parallel session. Read its open pull requests first, and
keep each change behind `glass:`, so the two do not collide.

**Done when** a scripted chat in the visual rig shows each part in glass, and
the chat e2e specs pass with the gate on and off.

### GL-5 · The tint — AGENT-SAFE

The organization's accent is the tint. Today Settings → Appearance has a
personal accent and no organisation accent (the gateway's `org_settings`
holds mode, density and the lock only). GL-5 adds the organisation accent
beside the organisation mode, with the same lock. GL-5 also adds a text shade of the tint
that passes 4.5:1 on the card and the grouped surface, as Aroh's `#0068d6`
does for `#007aff`. It then finds each use of `primary` or `accent` that only
decorates, and moves it to a neutral or a system colour behind `glass:`.

**Done when** the visual rig, under three accents (Apple blue, an org orange,
a pale yellow), shows the tint only on actions, selection and the member's
bubble, and every text in the tint passes AA.

### GL-6 · One pass per app — AGENT-SAFE

One pull request per app group, in the order of the live set
(`launch_surface.md` §2). Each one captures its app in glass at the four
contexts, fixes each P0 and P1 behind `glass:`, and removes palette-class debt
where it touches.

**Done when** each live app has its pass merged, and the owner has looked at
staging.

### GL-7 · Files — AGENT-SAFE, owned by WS-60

`artifact_management.md` AM-2 builds the library to Aroh's Files design:
groups by day ("Today", "This week"), kind filters (Documents, Slides,
Sheets, Images, Pages), a "Made by AI or Uploaded" control, list and grid, and
a side canvas with Preview and Files tabs. GL changes no data rule of WS-60.

**Done when** AM-2's own "done when" holds, with the Aroh design.

### GL-8 · Aroh's tools — a separate spec

The inventory of Aroh's skills, tools and connections, and what Metorite
lacks, goes into AI Studio: WS-STU, `specs/ai_studio.md` and D141 (a skill
creator replaces the agent creator), with the tool-pack seam of WS-67. GL
does not build it.

The survey of 2026-10-31 ranked these by their effect on the chat. Each
needs tenant scope, because Aroh trusts one owner:

1. Office deliverables (pptx, docx, xlsx, pdf) with Aroh's `render-check`,
   so the agent sees what it made. Metorite has no LibreOffice in its images,
   and its shell is limited to the orgs in `MAF_CODING_SCOPE`.
2. Office previews in the canvas: a slide strip and a CSV table (with GL-7).
3. Settings cards in the chat (form, list, status), with server-registered
   buttons.
4. Secret and sign-in cards, so a member connects a system from the chat.
5. API connections: research, connect, and a call tool that adds the key on
   the server and asks before a write.
6. Search and install from the MCP Registry, remote servers only (D85).
7. The design skills: `design-taste`, `design-systems`, HTML `decks`,
   `design-concepting`, `brand-guidelines`, `brand-assets`.
8. Playbooks with a dry run, commitments, and the "noted" card.
9. The tool factory: tools the agent builds, tests and versions.
10. Voice in the composer, and image generation as a tool.

### GL-9 · Production — OWNER-GATE · DONE 2026-10-31

The owner writes `NEXT_PUBLIC_UI_LOOK=glass` into production's `.env.local`,
or tells an agent to in chat. The next build serves it.

**Done when** `app.metorite.com` serves glass, and the owner says so.

**Done.** The owner said so in chat on 2026-10-31. An agent wrote the line
on srv1914284 (backup `.env.local.bak-20261031T050134Z`) and dispatched
`deploy.yml`. `api.metorite.com/version` serves `997ea6e0a` as `applied_sha`,
and `app.metorite.com/signin` renders `data-look="glass"`.

### GL-Z · One look again — AGENT-SAFE after the owner's word

Glass values become the `:root` and `.light` values. Each `glass:` class folds
into a plain class, and each classic class it replaced goes. The attribute,
the switch, the gate and the classic blocks go. `DESIGN_SYSTEM.md` §0 is
rewritten for the one glass look.

**Done when** no `glass:` variant, no `data-look` and no classic token block
remains, and every fence passes.

---

## 6. Fences

| Rule | Fence |
|---|---|
| The classic token values do not change | `src/lib/theme/themes.test.ts` (exists). The manifest mirrors classic |
| Only a gated build carries look code: the boot script holds none without the gate | `src/lib/theme/glassLook.test.ts` (GL-1) |
| A glass-only utility appears only behind `glass:`, or in a glass-only file | `glassLook.test.ts` (GL-1) |
| The glass block defines no token that classic lacks, except the §4.3 list | `glassLook.test.ts` (GL-1) |
| Each glass text pair passes AA, on the page, the card, a muted panel and its 10 % chip | `src/lib/theme/glassLook.test.ts` (GL-1), which reads the glass blocks of `globals.css` |
| Reduced motion stops glass motion too | `glassLook.test.ts` (GL-1) |
| Each glass keyframe answers reduced motion | `src/lib/theme/motion.test.ts` (exists) |
| Production does not carry the gate | ADVISORY. No test reads a box. GL-9 is OWNER-GATE |

---

## 7. Current file paths (re-verify at dispatch)

| What | Where |
|---|---|
| The one look | `workbench/control_plane/src/app/globals.css` |
| The classic mirror | `workbench/control_plane/src/lib/theme/themes.ts` |
| The boot script | `workbench/control_plane/src/lib/theme/boot.ts` |
| Appearance keys | `workbench/control_plane/src/lib/theme/storage.ts`, `scope.ts`, `store.ts` |
| Contrast and motion gates | `workbench/control_plane/src/lib/theme/contrast.ts`, `contrast.test.ts`, `motion.test.ts` |
| Primitives | `workbench/control_plane/src/components/ui/` |
| The visual rig | `workbench/control_plane/e2e/visual/harness.ts` |
| Aroh's tokens | `../Aroh/apps/web/src/index.css` |
| Aroh's primitives | `../Aroh/apps/web/src/components/ui.tsx` |
| Aroh's chat | `../Aroh/apps/web/src/components/` `Composer.tsx`, `Message.tsx`, `ToolCallRow.tsx`, `ThinkingBlock.tsx`, `cards/CardShell.tsx` |
| Aroh's files | `../Aroh/apps/web/src/components/files/`, `canvas/` |

---

## 8. Verification commands

From `workbench/control_plane`:

```bash
npx tsc --noEmit
npx vitest run src/lib/theme src/components/ui src/lib/shell
NEXT_PUBLIC_UI_LOOK=glass npx vitest run src/lib/theme
npx playwright test e2e/visual/<rig>.visual.ts --project=visual
```

On staging, after a merge:

```bash
curl -s https://api.dev.metorite.com/version      # the merge SHA
curl -s https://dev.metorite.com/signin | grep -c 'data-look'
```

The second line counts the attribute on the sign-in page. `/` answers a
signed-out caller with a redirect, so it counts 0 even with the gate on. The
count is 0 when the gate is off.

---

## 9. Questions for the owner

Each has a default, and the build goes on with the default until the owner
answers.

| # | Question | Default |
|---|---|---|
| Q1 | "The company's highlight colour": is it each customer organisation's own brand colour, set once by its admin? | Yes. GL-5 adds an organisation accent, and glass uses it as the one tint |
| Q2 | With no organisation accent, which blue is the tint? | Aroh's Apple blue (`#0068d6` light, `#0a84ff` dark). Classic's sky blue stays in classic |
| Q3 | In dark mode, white on Apple's `#0a84ff` is 3.65 : 1, under AA for a button label. Accept Apple's white, or keep AA? | Keep AA: black ink on the tint in dark, as `accentInk()` already picks for a light accent. GL-5's text shade of the tint may let a deeper fill take white |
