---
name: visual-review
description: >
  Render any Metorite control-plane surface in a real browser and LOOK at it, in
  the contexts a member can put it in — light mode, compact and comfortable
  density, a changed accent, and four widths down to mobile. Use this skill
  whenever you change, build or review UI in workbench/control_plane or
  workbench/operator_console, and whenever somebody asks you to check, audit,
  critique or improve how a screen looks. Use it before you open a PR that
  touches a component, a stylesheet or a layout. Use it when a report mentions
  spacing, contrast, dark mode, light mode, density, accent, wrapping, clipping,
  overflow, a screenshot, or "how does it look". Also use it to reproduce a
  visual bug, to see an empty or error state, or to prove that a colour comes
  from the right token. Trigger on "review the UI", "audit this screen", "does
  this look right", "check it in light mode", "screenshot the app", "visual
  regression", "design review", "check the mobile view".
---

# Visual review — see the surface before you judge it

You cannot review a screen you have not looked at. This skill renders any
control-plane surface in Chromium, with no gateway and no database, and
captures it in the contexts that break layouts.

## Why the repo needs this

`workbench/control_plane/DESIGN_SYSTEM.md` §0 states the risk directly. A
hardcoded value "will render fine". It is still wrong in light mode, at compact
density, and under a changed accent. That is where somebody who did not write
it finds it, weeks later.

It then asks a person to check those contexts by eye. **Nothing in the tree
can.** The conformance suite tests eight regular expressions. The theme-switch
check was the real gate, and it was deleted with the theming engine on
2026-08-31. So the contract names a gate that no longer exists.

This skill is that gate. Its first run found three defects. A status colour was
wired to the member's accent. A native checkbox turned into a black square in
light mode. Three components crashed the whole page on a missing response
field. None of them were visible in the code.

## The loop

1. **Copy the example.** `workbench/control_plane/e2e/visual/example.visual.ts`
   is the template. Change the route and the stubs.
2. **Run it by name.** `npx playwright test e2e/visual/<yours>.visual.ts
   --project=chromium`, from `workbench/control_plane`.
3. **Read the captures.** Open the PNGs. Look at them. The point of the rig is
   that a person or a model sees the surface.
4. **Turn what you find into an assertion**, where you can. A screenshot proves
   a defect once. A test keeps it fixed.
5. **Delete your rig file.** The captures are the deliverable.

## What the harness gives you

`e2e/visual/harness.ts` holds the shared parts. Read that file — it is short,
and each function says which trap it exists for.

| Function | What it does |
|---|---|
| `stubApi(page, handlers, opts)` | Answers every `/api/**` call. Keys are path fragments, and the longest match wins |
| `gotoAndSettle(page, route)` | Navigates and waits, without `networkidle` |
| `firstVisible(page, label)` | The first VISIBLE control with that name |
| `clickAndWait(page, label)` | Clicks, then waits for what it opens to mount |
| `captureContexts(page, name)` | One capture per context, then restores the page |
| `readPaint(locator, prop)` | The computed colour of an element |
| `underAccents(page, accents, fn)` | Runs `fn` under each accent, and returns the results |
| `watchErrors(page)` | Collects console and page errors |

## The eight contexts

`CONTEXTS` in the harness holds them. Three axes and four widths.

- **dark-1440** and **light-1440** — colour mode. The `.light` class on `<html>`.
- **compact-1440** and **comfortable-1440** — density. `--ui-scale` drives the
  root font size, so a `rem` follows it and a `px` does not. Compact is where a
  pinned font size stops matching its neighbours.
- **accent-violet-1440** — the member's accent. Anything that reads `--primary`
  to mean something other than "selected" shows up here, and nowhere else.
- **wide-1920**, **small-1280**, **mobile-390** — width. **1440 is the width
  that matters most**, because a row that wraps there fits at 1920 and is
  already broken at 1280. A defect visible only at 1440 hides from both.

## Nine traps, all measured

Each of these reads like a different problem than it is. That is why they are
written down.

1. **`networkidle` never arrives.** `projects/notifications` polls. Use
   `gotoAndSettle`.
2. **`127.0.0.1` is not `localhost`.** Next refuses its own dev resources
   across that origin, the shell renders, and hydration never completes.
   `playwright.config.ts` explains this at length. Pass a path, not a host.
3. **`.first()` returns a HIDDEN element.** A mobile bar carries the same
   labels as the desktop one. Use `firstVisible`.
4. **`getByText` misses a button** whose label is a child span beside an icon.
   `firstVisible` tries role first.
5. **A tab bar mounts with its data, not with the click.** Under about a second
   it is not there yet. Use `clickAndWait`.
6. **Clicking an already-selected node DEselects it.** After a reload the app
   restores the selection. Check whether the thing you want is already there
   before you click.
7. **A stub key that is a prefix swallows its children.** `projects/tasks` also
   matches `projects/tasks/<id>/relations`. `stubApi` sorts by key length to
   remove this, but a fragment you choose can still be too short.
8. **An unexpected response shape blanks the page.** Several components read a
   field behind only a `!data` guard. `SAFE_EMPTY` is a superset for that
   reason. If the page goes blank, read the stack before you blame the rig.
9. **The default 45-second timeout is not enough.** Eight contexts and some
   navigation take minutes. Call `test.setTimeout`.

## What to look for

Read `references/what-to-look-for.md` before a review you intend to report on.
It carries the defect classes this repo produces, with the rule each one
breaks.

## Where an assertion beats a screenshot

A capture proves a defect to a person. Some claims can be tests instead, and
those are worth the extra minutes because they hold after you leave.

The colour case is the clearest. A status colour must not move when the member
changes their accent:

```ts
const paints = await underAccents(page, ["hsl(210 90% 50%)", "hsl(120 60% 45%)"], () =>
  readPaint(page.getByRole("button", { name: "In progress" })),
);
expect(paints[0]).toBe(paints[1]);   // a status hue is not an accent
```

Assert the **computed paint**, never a class name or a hue name. A unit test
that asserts the word `blue` passes while every lane draws the same grey. That
has happened in this repo, and `e2e/project-state.spec.ts` records it.

## Honest limits

- The rig **stubs the API**. It says nothing about latency, pagination, a slow
  network or a real permission model.
- It runs the **dev bundle**, because the dev auth bypass is what lets it in
  with no login. `playwright.config.ts` states that trade. Minification and RSC
  boundary faults are outside it.
- A capture is **one frame**. Hover, focus and keyboard states need their own
  step, and they are usually where a control is weakest.
- It does not measure contrast. `src/lib/theme/contrast.ts` does that.
