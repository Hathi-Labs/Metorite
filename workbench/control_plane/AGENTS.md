<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# This UI is themed — read DESIGN_SYSTEM.md first

`DESIGN_SYSTEM.md` in this directory is the contract, not a style suggestion.
The short version, because these are the three mistakes that actually happen:

1. **Never write a colour.** `bg-primary`, `text-muted-foreground`,
   `var(--success)` — never `#0ea5e9`, `hsl(…)`, or `bg-[#1a1b1e]`. On a
   coloured fill use the `-foreground` partner, not `text-white`.
   **`bg-sky-500` counts** — Tailwind's own palette is a hardcoded colour with a
   friendly name. See rule 7 for what to use instead.
2. **Never `import … from "lucide-react"`.** Use `<Icon name="Plus" />` — the
   active theme decides which pack draws it.
3. **Never hand-roll a control.** `Button` / `Input` / `Select` / `Textarea` /
   `Badge` from `src/components/ui/`. Material makes every button a pill,
   Graphite uppercases every label; no class string can express that.
   **`Select` exists since S5** — a bare `<select>` wears the OS's own
   disclosure triangle, and 38 files had each copied their own class string
   instead. A **file input must be hidden** (`className="hidden"`) behind a
   `<Button>` that raises it, with the chosen filenames listed by the app:
   "Choose Files / No file chosen" is the browser's string in the browser's
   font and no theme can reach it.

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
not px.

`npx vitest run src/lib/theme/` before you push.

## Every app renders through the theming engine — there are no app-local looks

*(Owner directive, 2026-08-10: "I want the UI for the projects to match the
theming configuration used in the Metorite… ensuring future development
considers it." It applies to every surface, not only Projects.)*

An app inside Metorite is a **projection of one product**, not a product
with its own visual identity. `/projects`, `/tasks`, `/email`, `/notes`, `/crm`
and everything after them draw from the same engine, so switching the org to
Fluent or Material or Graphite repaints all of them together. The moment one
app carries its own palette, that app is the one that looks broken on the day
somebody changes the theme — and nobody notices until then, because a hardcoded
value renders *fine*.

Five rules on top of the three above. Each one exists because it was broken:

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
7. **Categorical hues are a theme decision too.** A set of colours that only
   has to be *mutually distinguishable* (contexts, tags, labels) still belongs to
   the theme. **The ramp now exists**: `--cat-1` … `--cat-8`, eight slots every
   theme supplies in both modes (WS-27af; values in `src/lib/theme/themes.ts`,
   class strings in **`src/lib/categorical.ts`** — `categoricalAccent(name)`,
   never a hand-written `bg-cat-*` table). Pick the slot by hashing the item's
   NAME, never by array index; never reorder the slots, which silently repaints
   everything already assigned. `app/tasks/lib/contextColors.ts` is the worked
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

**What CI cannot catch, and you must.** There is no structural or layout test in
this tree: nothing asserts panel counts, shell adoption, mobile branches, or that
two apps draw a card the same way. The conformance suite checks eight regexes.
(`src/lib/sharedTaskUi.test.ts` is the nearest thing to a structural test and is
narrower than it sounds: it pins that a shared module is declared **once** and
that each app still imports it — never that a surface actually uses it.)
So the real check is `DESIGN_SYSTEM.md` §8: **switch the theme to Fluent, then
Material, then Graphite, and look at the surface you changed** — and at the
neighbouring app, because continuity between two apps is exactly what no test in
this repo measures. That check is what would have caught every divergence listed
above before it landed.
