<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# This UI is themed — read DESIGN_SYSTEM.md first

`DESIGN_SYSTEM.md` in this directory is the contract, not a style suggestion.
The short version, because these are the three mistakes that actually happen:

⚠️ **The theming engine is RETIRED (owner directive 2026-08-31).** There is one
look, `src/app/globals.css` is the source, and every app renders in it. Four
themes, the `data-theme` attribute and the per-theme icon packs are deleted. A
member may still change **colour mode, density and accent** — those adjust the
one look rather than replacing it, and every rule below still binds because of
them. `DESIGN_SYSTEM.md` §0 is the detail.

1. **Never write a colour.** `bg-primary`, `text-muted-foreground`,
   `var(--success)` — never `#0ea5e9`, `hsl(…)`, or `bg-[#1a1b1e]`. On a
   coloured fill use the `-foreground` partner, not `text-white`.
   **`bg-sky-500` counts** — Tailwind's own palette is a hardcoded colour with a
   friendly name. See rule 7 for what to use instead.
2. **Never `import … from "lucide-react"`.** Use `<Icon name="Plus" />`, which
   owns the default size, the class contract and the unknown-name fallback.
   Only `lib/icons.tsx` may name the library.
3. **Never hand-roll a control.** `Button` / `Input` / `Select` / `Textarea` /
   `Badge` from `src/components/ui/`. The `--control-*` tokens carry the
   focus ring, the state layer and the label treatment, and no class string can
   express those. An accent override moves `--primary` under every one of them.
   **`Select` exists since S5** — a bare `<select>` wears the OS's own
   disclosure triangle, and 38 files had each copied their own class string
   instead. A **file input must be hidden** (`className="hidden"`) behind a
   `<Button>` that raises it, with the chosen filenames listed by the app:
   "Choose Files / No file chosen" is the browser's string in the browser's
   font, and the app cannot reach it.

All three are enforced by `src/lib/theme/conformance.test.ts` (**eight** rules:
literals, `lucide-react`, bracket classes, solid-button chrome, raw palette
classes, the `bg-accent text-accent-foreground` active pair rule 6 below forbids
— since S4 — since S5, raw `<select>`s and visible file inputs, and since
WS-27ak, an `@base-ui/react` import outside `src/components/ui/`), which
carries a frozen baseline for existing debt: a file with no
budget must be clean, a baselined file may not get worse, and a baselined file
that got *better* fails until you lower its number — so the debt figures never
quietly become fiction. **If your change improves a baselined file, lowering
its number is part of your change**, not a follow-up.

Type scale: `text-sm` / `text-xs` / `text-[11px]` / `text-[10px]`. Do not invent
an off-grid size — `text-[12px]` is `text-xs` written the long way, and it also
opts out of the user's density preference, because `--ui-scale` reaches rem and
not px. In a `style` object, take the size from `TYPE` in `src/lib/typeScale.ts`,
which holds the same scale in rem. A generative-UI template never sets a font
size in px (fence: `src/lib/typeScale.test.ts`).

`npx vitest run src/lib/theme/` before you push.

## Every app renders through the theming engine — there are no app-local looks

*(Owner directive, 2026-08-10: "I want the UI for the projects to match the
theming configuration used in the Metorite… ensuring future development
considers it." It applies to every surface, not only Projects.)*

An app inside Metorite is a **projection of one product**, not a product
with its own visual identity. `/projects`, `/tasks`, `/email`, `/notes`, `/crm`
and everything after them draw from the same tokens, so a change to
`globals.css` repaints all of them together. The moment one app carries its own
palette, that app is the one that looks broken on the day somebody edits a
token, switches to light mode, or sets an accent — and nobody notices until
then, because a hardcoded value renders *fine*.

⚠️ Retiring the four themes (2026-08-31) did **not** relax this rule. It removed
the thing that used to EXPOSE a breach for free: one theme switch and every
hardcoded value announced itself. The breach is still a breach. Nothing shows
it to you now.

Seven rules on top of the three above. Each one exists because it was broken:

4. **One vocabulary per concept, in `src/lib/` or `src/components/`, consumed by
   every app.** Status and lane colour is `src/lib/statusAccent.ts` — the single
   place a status, tag, board column, group header or pill becomes a hue. Before
   it there were three vocabularies plus a colour column
   (`pm_task_statuses.color`) that was stored and drawn nowhere, so every
   Projects board column rendered the same grey while the Tasks board next door
   was colour-coded. **Do not add a second palette.** If you need a hue a shared
   module does not express, extend the shared module.
   The **card chip** vocabulary is the same rule one level up: `src/lib/taskCard.ts`
   decides which chips a task earns and names their tone (`muted` · `danger` ·
   `accent` · `warning`), and `src/components/TaskMeta.tsx` is the ONLY file that
   turns a tone into a class. A chip may also carry `hue?: AccentHue` — still a
   name, resolved through `statusAccent` — which makes it a filled pill instead of
   tinted text: a hue is an **identity** (which tag), a tone is a **measurement**
   (how late, how blocked). Chip keys may be namespaced `<kind>:<discriminator>`
   (`tags:ops`); anything classifying a chip reads `chipKind(key)`, never the whole
   key. Fences: `sharedTaskUi.test.ts`'s "the chip tone→class table" SEAM row, and
   `app/projects/lib/card.test.ts`'s assertion that every chip kind `cardChips` can
   emit maps onto a real `shownFields` key (S6).
   The same rule outside colour: **`src/lib/export.ts` is the one CSV-download seam**
   (`filenameFromDisposition`, `saveCsv`), consumed by Projects and the CRM. Its two
   traps are why it is shared rather than copied — the UTF-8 BOM, and the filename,
   which is the SERVER's, read back off `Content-Disposition` rather than composed here.
   Each app keeps only its own `exportQuery`/`exportPath`.
   `saveBlob` is the same save for any file, and `saveCsv` calls it (WS-27bm
   S8). **`src/lib/reportEmail.ts` is the one report formatter.** It builds one
   layout and draws it as the email, as a Markdown file and as the HTML the
   gateway lays out as a PDF. `app/projects/lib/reportFiles.ts` is the one
   download path. Fences: `reportEmail.test.ts` and `reportFiles.test.ts`.
   **A report section draws with its Analytics panel** (WS-27bn R2b).
   `app/projects/lib/reportPanels.ts` maps a section to the panel's props
   and computes nothing. `Stat` in `AnalyticsPanels.tsx` is the one tile.
   Fence: `app/projects/components/reportVisuals.test.ts`.
   Since WS-27bn R5f round 1, `app/projects/lib/sectionIcons.ts` is the one
   icon map of the report sections. `app/projects/lib/sectionEmpty.ts` holds
   the one friendly line of a clear section, and the app, the email and the
   download read it. Fence: `app/projects/components/reportsRedesign.test.ts`.
   **`src/lib/autoOpenArtifact.ts` is the one rule** for a file a chat agent
   writes: `/chat` and the Projects rail both call it. Fence:
   `autoOpenArtifact.test.ts`.
   **`src/lib/assistantCheckpoint.ts` is the one row shape** the chat
   translator (`api/agent/chat/route.ts`) saves (WS-27bm S10). The live
   stream names the requested agent in `author_email`. An `@name` turn names
   none, and the reconnect stream names none. Fence: `assistantCheckpoint.test.ts`.
   **Prose is `cc-prose prose`, never `prose-invert`** (WS-27bm S8). The
   typography plugin's invert variant hard-codes dark text, and this app
   turns light with the `.light` class. `.cc-prose` in `globals.css` points
   every prose colour at a token. **Chat Markdown has one renderer,**
   `MarkdownBody` in `MarkdownMessage.tsx`. Fence:
   `src/components/chatVisualReview.test.ts`.
   **A remote image in agent Markdown loads only on a click.** Every
   renderer of agent text uses `MarkdownImage` as its `img`, and a renderer
   with `rehype-raw` also runs `rehypeGateRemoteMedia` after it. Both live in
   `src/lib/markdownMedia.ts` and `src/components/MarkdownImage.tsx`. Fence:
   `src/components/markdownImage.test.ts`.
   **Untrusted HTML has one DOMPurify policy,** `src/lib/untrustedHtml.ts`.
   The email pane and the `.docx` viewer use it. A new raw-HTML sink
   (`dangerouslySetInnerHTML`, an `innerHTML` write, a `srcdoc`) fails
   `src/lib/htmlSinks.test.ts` until it is on that list with its gate.
   **An entity in chat text is one pill,** `src/components/ui/EntityPill.tsx`
   (WS-27bm S9). Do not draw a task, a project or a person as bold text or
   as a second chip. Fence: `src/components/ui/EntityPill.test.ts`.
   **A «mark» never reaches the member** (owner report, 2026-10-07). The
   server keeps its data fence, and `src/lib/fencedText.ts` is the one
   parser of it for display. Draw server text with `FencedText.tsx`, and a
   generative-UI string with `GenUiText.tsx`. That is `MarkdownBody` in its
   `inline` mode, never a second renderer. Fences: `fencedText.test.ts` and
   `genUiInlineText.test.ts`.
   **Each element of a chat turn has one place** (owner, 2026-10-08).
   `src/lib/chatPlacement.ts` is the one map from a tool to its kind. Do not
   guess a kind from a tool name in a card file. Add the name to the map.
   The rule of record is `projects_ai_chat.md` §24.
   - An element that needs the member stays in the flow, and `AskPin` keeps
     it in view.
   - A read draws inside its step in the trail, closed, and never after the
     answer.
   - A write's receipt draws compact after the text. An answer card is one
     at most, after the text.
   - Fences: `chatPlacement.test.ts`, `askPin.test.ts`,
     `datasetTable.test.ts` and `tests/unit/test_chat_placement_classes.py`.
   - A card keeps its place in the stream (`projects_ai_chat.md` §24.9).
     Each `generative_ui` event carries `segmentCutoff`, and `genUiFlow`
     draws the text that came after a card below it. The live hook, the
     chat proxy and `chat_fold.py` stamp it the same way.
   - `components/RecommendedBadge.tsx` is the one mark of a recommended
     option. Do not draw a star for it. Fences: `genUITemplates.test.ts`
     and `elicitationCard.test.ts`.
   - `src/lib/askAnswers.ts` is the one reader of the answer a blocking card
     already got. A card that has one draws as sent after any remount.
   - `src/lib/inAppLink.ts` holds the two rules of a chat link. Each in-app
     click sends `IN_APP_LINK_EVENT`, and a link to another site shows its
     host. Fence: `inAppLink.test.ts`.
   **A long chat card rolls up through one seam,** `components/RollupCard.tsx`
   (owner, 2026-10-10). `src/lib/cardRollup.ts` holds the rules. The newest
   card stays open. A card that waits on the member never folds. A toggle by
   hand wins. A long card folds when it is not the newest. Wrap a new flow card in it, and do
   not add a second fold. The transcript follows its bottom on each change
   of size (`src/lib/stickToBottom.ts`). Fences: `cardRollup.test.ts`,
   `stickToBottom.test.ts` and `e2e/chat-card-rollup.spec.ts`.
   The body of a card has no hanging indent. It starts at the content
   padding, in line with the chevron (owner, 2026-10-10). A listed row is
   one line: number, title, status pill and open icon. Fence:
   `src/lib/cardDenseLayout.test.ts`.
   **A card key has one label,** in `CARD_FIELDS` in `src/lib/cardFields.ts`,
   and its kind draws its value. The Python fakes read that map and fail a
   card test that prints a key with no label (`tests/unit/_card_words.py`).
   A read result draws through `components/Readout.tsx`, with no
   id and no `[key]`. That includes the email and CRM reads. Fences:
   `cardFields.test.ts`, `readout.test.ts` and `readoutEmailCrm.test.ts`.
   **A table draws by its column kinds.** `src/lib/dataGridLayout.ts` holds
   the rules of the `dataGrid` template. A status is its chip, and the rows
   stack in a narrow box. A category column that the data names hides beside
   a status. Fence: `dataGridLayout.test.ts`.
   ⚠️ **The BOM trap binds at every hop, and "keep it a `Blob` in the client" is only
   half of it.** `Response.text()` is a UTF-8 *decode* and a UTF-8 decode strips a
   leading byte order mark, so **a BFF proxy that does `await res.text()` and rebuilds
   the response deletes the BOM before the client ever sees it** — which is what both
   `api/projects/[...path]` and `api/crm/[...path]` did (measured on node v22: upstream
   `EF BB BF 4E 61 6D`, relayed `4E 61 6D 65`), and Excel on Windows then reads "Café"
   as "CafÃ©". A proxy fronting a binary-ish route reads `res.arrayBuffer()` and passes
   the bytes; it also forwards `Content-Disposition` and `X-Export-Rows`, because this
   proxy is the only route to them.
   Fence: `src/lib/export.test.ts`, which **runs** every proxy in `EXPORT_PROXIES` over
   a BOM'd `text/csv` body and compares bytes (a decoded compare cannot see a BOM at
   all), checks a 422 refusal from the same endpoint still arrives as readable JSON, and
   statically sweeps for the `NextResponse.json` content-type stamp that turns a
   `text/csv` download into `{}` with a 200. Add a proxy to that list when it grows a
   non-JSON route. *(The previous version of that fence asserted
   `toContain("await res.text()")` and claimed `res.text()` "keeps the bytes" — it
   pinned the defect in place. A fence that holds a bug still is worse than none.)*
   The same rule again, off the visual axis: **`src/lib/emailOtp.ts` is the one
   outbound-email seam** — `resendSender` (the transport, and the only place in
   this app that builds a Resend bearer) and `emailOtpFrom` (the one verified
   sender). WS-30 SC-2c's invite notification (`src/lib/inviteEmail.ts`,
   2026-08-24) **imports** both rather than moving or copying them: that file sits
   on the live auth path, so refactoring it is a sign-in outage, and a second
   transport would put a second `Authorization: Bearer` mint into the route tree —
   which `src/lib/gateway.test.ts`'s three-name allow-list refuses by name. A
   third consumer imports them too. Email bodies carry **no colour**: an email
   renders outside the theme system, so a hex value there can never follow the
   org's theme (fenced in `inviteEmail.test.ts`).
5. **A category and a name must resolve to the same colour.** Some apps know
   what a lane *means* (Projects has `STATUS_CATEGORIES`); some can only read
   what it is *called* (Tasks' stages are user-typed). Those two routes must
   agree, or the same lane draws two colours in two apps. Fences:
   `test_category_and_keyword_agree` and, on the gateway side,
   `test_seed_status_colours_match_the_shared_vocabulary` — which reads
   `CATEGORY_HUES` out of the TypeScript rather than mirroring it, because a
   mirror goes stale and then lies. **Seeded data counts as a UI decision**: a
   stored colour outranks a derived one, so a seed that disagrees silently
   overrides the shared vocabulary on every uncustomised project.
6. **Use the house tokens, not a synonym.** Active/selected is
   `bg-primary/10 text-primary` (the measured norm across `/tasks`, `/email` and
   `src/components`), not `bg-accent`.
   Radius: **the whole named scale is derived from `--radius`** in `globals.css`'s
   `@theme` block — `sm`/`md` step down, `lg` and `xl` both *equal* `--radius`,
   `2xl`/`3xl` step up. So every `rounded-<name>` utility is themed and none of
   them is a violation; only an arbitrary value (`rounded-[14px]`) escapes the
   theme. What still matters is **consistency between surfaces**: two boards
   drawing their columns at different radii look like two products even when both
   are themed.
   *(Corrected 2026-08-10. This rule previously claimed `rounded-xl` was a fixed
   12px that ignored Graphite and Material. It is not — `--radius-xl:
   var(--radius)`, i.e. identical to `rounded-lg`. The claim was mine and it was
   wrong; acting on it would have baselined ~274 correctly-themed occurrences
   across ~70 files as debt, which is a fence against a non-violation and worse
   than no fence at all.)*
   **Fence (S4):** conformance rule 6 matches the PAIR
   `bg-accent text-accent-foreground` — a file with no budget must be clean, the
   four remaining sites are baselined per file and can only go down, and
   `lib/statusAccent.ts` is excepted with its argument. `hover:bg-accent` and
   `bg-accent/10` are deliberately not matched. The radius half is **advisory**: nothing tests it, and
   nothing should — see the correction above.
7. **Categorical hues are a design-system decision too.** A set of colours that
   only has to be *mutually distinguishable* (contexts, tags, labels) still
   belongs to the tokens. **The ramp**: `--cat-1` … `--cat-12` in both modes
   (values in `src/app/globals.css`, mirrored in `src/lib/theme/themes.ts`,
   class strings in **`src/lib/categorical.ts`** — `categoricalAccent(name)`,
   never a hand-written `bg-cat-*` table). Pick the slot by hashing the item's
   NAME, never by array index. Never reorder the slots, which silently repaints
   everything already assigned.
   ⚠️ **A hash may only land on the first EIGHT.** `hashSlot`'s modulus is
   frozen at `HASH_SLOTS = 8`. Slots 9–12 (2026-08-31) exist for an explicit
   pick — a space's marker in Space Settings — and widening the modulus would
   recolour every context and tag in the product at once. `app/tasks/lib/contextColors.ts` is the worked
   adapter — it keeps only the hand-assigned @context slots and delegates the
   rest, the same shape `stageColors.ts` has over `statusAccent.ts`.
   This does **not** compete with rule 4, it completes it: a status resolves to
   a **semantic** tone (its hue is information), a category resolves to a **ramp
   slot** (its hue is only an identity). Two concepts, two mechanisms, no third.
   ⚠️ `bg-sky-500/10` used to pass every conformance regex — it is a named class,
   not a bracket class — which is how ~950 of them accumulated. **CI catches it
   now** (conformance rule 5, per-file baselines that only go down), but the
   baseline is large: a file already in it can still get worse up to its budget.
8. **A headless primitive is imported from `src/components/ui/`, never from the
   library.** D-PM-15 chose **Base UI** (`@base-ui/react`) as the one substrate
   for the primitive layer, on two conditions: every primitive arrives as a
   Metorite wrapper carrying `.cc-control`, `<Icon name>` and semantic
   tokens, and there is exactly one substrate. `src/components/ui/Modal.tsx`
   (WS-27ak) is the worked example and the only file in the tree allowed to name
   the library. **A dialog is not a `fixed inset-0` div** — before that wrapper,
   70 files carried one and **zero** trapped focus or set `inert`; that is not
   seventy bugs, it is a primitive nobody had written. Fences: conformance rule
   8 — nothing outside `components/ui/` imports the substrate; no second
   substrate in `package.json` (a vendored shadcn/`cva` registry pulling in
   `radix-ui` is the observed vector, not a hypothetical one); and none of the
   six converted `/projects` dialogs may contain `fixed inset-0`. ⚠️ **The
   import rule does NOT catch a hand-rolled dialog** — a `fixed inset-0` div
   imports nothing, which is how the 70 got there — so "a new surface uses
   `Modal`" is **advisory**, review-only. ⚠️ Base UI marks the background
   `aria-hidden` + `data-base-ui-inert`, never real `inert`: Ctrl+F still finds
   the page behind the scrim.
   ⚠️ `src/lib/outsideClick.ts` is **not** consumed by Modal, despite its
   docstring naming "Wave 2's Modal": Base UI brings its own outside-press
   handling with the start-and-end-outside rule the hand-rolled walker does not
   express. It stays the answer for a popover we do not build on the substrate.
9. **One read cache, and one loading vocabulary.** `src/lib/dataCache.ts` is the
   only stale-while-revalidate store, and `useCachedResource` is its React face.
   Do not add a second one, and do not add a data-fetching library beside it.
   Two rules bind every read through it. The cache gives back what it holds
   **immediately**, at any age. A revalidation **always** runs behind it.
   ⚠️ **The cache is memory-only, and bound to the signed-in member.** Keys are
   request paths, and a path says nothing about who asked — so two members on
   one browser share every key. `bindIdentity` (called in `AppShell`) empties it
   when the member changes. Never persist it to `localStorage`,
   `sessionStorage` or IndexedDB: that puts one member's tenant rows on the
   device for whoever opens the browser next, below the gateway, where no
   row-level security reaches.
   The loading half is the same rule: **`loading` means NOTHING TO SHOW**, not
   "a request is in flight". A revisit that already holds the answer must never
   blank. Only a true miss earns a skeleton, and a skeleton comes from
   `src/components/ui/Skeleton.tsx` — never a hand-rolled `animate-pulse` bar,
   which is rule 3 one level down and had accumulated in twenty files.
   Fences: `dataCache.test.ts` (including a source grep for the storage APIs)
   and `useCachedResource.test.ts`. **Advisory:** nothing tests that a NEW
   surface adopts the cache instead of a cold fetch.
   **The chat caches in `localStorage` live in one namespace per account**
   (PR #652). Build every chat key with `chatKey` in `lib/sessions.ts`. A
   switch of accounts deletes nothing, and a sign-out clears that account
   only. The design note is `project-docs/specs/projects_ai_chat.md` §23,
   "Chat cache namespaces and multi-account". Fence:
   `src/lib/railSessions.test.ts`, which greps for a raw chat key.
   **The appearance keys are per account too** (owner bug, 2026-10-11).
   Mode, density and accent live at `<key>:<email>|<orgId>`, and
   `lib/theme/scope.ts` moves the pointer that the boot script reads. Read
   and write them through `themeStorage`, never by a bare key. Fence:
   `src/lib/theme/scope.test.ts`.
   **The shell layout copy is per account too** (NS-7). It lives at
   `cc-shell-prefs:<email>|<orgId>` and holds pin hrefs and preset names,
   never tenant rows. Only a layout that the server confirmed goes in it, and
   a sign-out clears that account's copy. Read and write it through
   `lib/shell/shellCache.ts`. Fence: `src/lib/shell/shellCache.test.ts`.
10. **An app plugs into the shell. It never builds one.** *(D89, owner
   directive 2026-10-05.)* The owner said: "Future applications … should also
   follow the same UI/UX rules." The shell owns the top bar, the command bar,
   the bell, the assistant dock, the launcher and Home.
   An app declares a manifest on its `NavPane` in `src/lib/nav.ts`. The
   manifest names its team, a one-line purpose and its jobs. It also names
   its search, its "needs you" items, its Home cards and its agent.
   Do not mount a ⌘K handler, a palette, a `NotificationBell` or an assistant
   rail in an app. Declare a job or a provider instead. A job opens a form,
   and a person saves it. The AI tier may fill the form, and it never saves.
   `project-docs/specs/navigation_shell.md` §5 is the contract, and §5.3 maps
   every live app onto it.
   **Fence: `src/lib/shell/seams.test.ts`.** It fails on a ⌘K listener, and on
   an import of a bell, an assistant rail or a palette. That includes an
   import of another app's rail or palette. It reads every file outside
   `src/lib/shell/`, and it allows one file per seam inside it. The debt sits
   in `SEAM_DEBT` per app folder, and each number only goes down. A move
   inside one app is free.
   ⚠️ **Do not add a key to `SEAM_DEBT` to make a new app pass.** If the app
   needs a missing shell part, build it once in `src/lib/shell/` (NS-1),
   for every app.
   From NS-2, `src/lib/nav.test.ts` will also fail on a live pane with no
   team or purpose. Until then, the manifest half is advisory. R9 in
   `work_plan.md` §1 binds the app's spec to declare it.
11. **The shell bar is constant. An app never renders into it. Every app
   opens with `AppTopBar`, its one title bar: rail toggle, name, subtitle,
   actions, tools.** *(Owner direction, 2026-10-10.)* The owner said: "I
   don't want to have any of the individual apps' UI/UX elements be on the
   top bar." The shell bar holds the fold control, the logo, the command bar
   and the activity control, and nothing else.
   **Amended by the owner, 2026-10-10:** the shell's one bell and its one
   assistant toggle sit at the right end of the shell bar, because "they
   belong to the whole product, not to an app." The bell shows only while
   `NEXT_PUBLIC_SHELL_DOCK` is on (NS-6 slice 6a, `src/lib/shell/ShellBell.tsx`).
   The toggle follows in slice 6b. An app's own bell or tool never goes there.
   Fences: `src/lib/shell/shellBell.test.ts` and `e2e/shell-bell.spec.ts`.
   The app's bar sits under it, with the app's name as its one `<h1>`. The
   rail toggle and the tools live in that bar, never in the rail and never
   in the shell bar. A page under the bar titles itself with an `<h2>`.
   When the row runs out of room, it wraps its tools to a second line.
   `DESIGN_SYSTEM.md` §6a holds the shape. `navigation_shell.md` §5.2 rule 3
   holds the rule.
   **Fences.** `src/lib/shell/appBar.test.ts` reads source. It fails on a
   slot API, and on a file outside `src/lib/shell/` that reaches into the
   shell bar. It fails on a live pane or a Settings sub-page with no
   `AppTopBar`. It fails on an app name in the shell bar's markup.
   `e2e/app-title-bar.spec.ts` opens each live pane, My Day and the four
   Settings sub-pages at 1440 and at 390. It fails unless each page shows one
   `<h1>` and one app bar. Chat on a phone is the one named exception, with
   its reason in the spec. The same spec fails on a bar control out of the
   window or under the name. It checks Calendar at 1024, 960 and 800, and
   Email and Projects at 1024. At 900 and 1024, a long name must not part
   the rail toggle or the way back from the name.
   `src/components/pageHeading.test.ts` holds `PageHeader` and
   `SettingsHeader` to an `<h2>`. Add a new live pane to `APP_BAR_HOME` in
   `appBar.test.ts` and to `PAGES` in the e2e, in the same PR.
   `appBar.test.ts` fails when the two lists differ.
12. **A rail of named items uses `RailRow`. A truncated label gets
   `OverflowTip`.** *(Owner direction, 2026-10-10, with ClickUp's sidebar as
   the reference.)* Both live in `src/components/ui/`.
   - A row shows its actions on hover and on keyboard focus only. At rest
     the actions take no width, and the row shows its muted count.
   - The count gives way only on a row with actions, and only from sight.
     It stays in the row's accessible name.
   - A row that opens shows its chevron in the icon's slot on hover and on
     keyboard focus. The toggle stays a labelled button, so Tab reaches it.
   - On a phone the chevron has its own column, and the icon stays. Each
     row shows its `phoneActions`, one "···" that opens the row's menu.
   - A cut name shows whole in a dark tip after 400 ms. A name that fits
     shows no tip, and no row adds a native `title` to its name.
   - The Projects tree, the My Tasks rail, the Email folders and the
     WhatsApp triage rail use it. Do not draw a rail row by hand.

   **Fences.** `src/components/ui/RailRow.test.ts`,
   `RailRow.phone.test.ts` and `OverflowTip.test.ts` hold the markup and
   the tip's rules. `src/lib/railRows.test.ts` fails when a rail on its list
   stops drawing `RailRow`.

   Add a new rail to that test's `RAILS` list in the same PR. That step is
   review, and no test holds it. `e2e/rail-rows.spec.ts` measures the hover,
   the tip, the width and the phone drawer. `DESIGN_SYSTEM.md` §6b holds the
   shape.

**What CI cannot catch, and you must.** There is no structural or layout test in
this tree: nothing asserts panel counts, shell adoption, mobile branches, or that
two apps draw a card the same way. The conformance suite checks eight regexes.
(`src/lib/sharedTaskUi.test.ts` is the nearest thing to a structural test and is
narrower than it sounds: it pins that a shared module is declared **once** and
that each app still imports it — never that a surface actually uses it.)
So the real check is `DESIGN_SYSTEM.md` §8: **look at the surface you changed
in a state you did not build it in** — light mode first, then compact density,
then a changed accent — and look at the neighbouring app too, because
continuity between two apps is exactly what no test in this repo measures.

⚠️ That check used to read "switch the theme to Fluent, then Material, then
Graphite", and it was the strongest tool in this document: a theme switch made
every hardcoded value announce itself for free. Retiring the themes
(2026-08-31) removed the tool and kept the problem. **Light mode is now the
highest-yield substitute** — most work happens in dark, so light is where a
hardcoded `#fff` or a missing `-foreground` partner surfaces. It is weaker than
what it replaces. Compensate by reviewing for the rule, not only by looking.
