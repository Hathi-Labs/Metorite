# Operator Console — design contract

This file tells an agent how to draw this app. Read it before you change a
surface. `src/app/globals.css` is the code of record. This file explains it.

## 1. Why this app has its own design system

**D35.4 keeps the Operator Console off the customer design system.** The reason
is structural. This is a different Next.js app, and `customer_console.md` §2.4
measures the cross-import count from `control_plane` as zero.

⚠️ **The exemption is a boundary, not a licence to be plain.** The spec gives
the method: port the primitives as plain-CSS equivalents, or restyle. This file
records the port.

**Never import from `control_plane`.** A cross-import breaks the fence. Copy the
idea and write the CSS again.

## 2. Tokens

**Never write a colour at a call site.** Use a token. `globals.css` holds them
all.

| Group | Tokens |
|---|---|
| Surface | `--bg` `--bg-subtle` `--panel` `--panel-2` `--panel-3` |
| Line and text | `--border` `--border-strong` `--text` `--muted` `--faint` |
| Semantic hue | `--accent` `--ok` `--warn` `--danger` |
| Scale | `--r-sm` `--r` `--r-lg` `--r-pill` `--shadow-1` `--shadow-2` |

**Each semantic hue carries two partners.** `--accent-soft` fills a background.
`--accent-line` draws a border. Use them instead of `rgba()`.

## 3. Two modes

The console supports a dark theme and a light theme. `src/lib/theme.ts` is the
seam. `layout.tsx` runs a boot script before the first paint.

🔴 **Dark is the default, and the default is not `prefers-color-scheme`.** No
person has looked at the light theme yet. `DESIGN_SYSTEM.md` §8 says a person
must switch the theme and look at the surface.

**To make the console follow the operating system, do this after somebody looks
at the light theme:**

```css
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) { /* the light block */ }
}
```

⚠️ **Write the light palette once.** Two copies of a token set drift apart.

## 4. Tone is the one status vocabulary

`src/lib/tone.ts` decides what a state looks like. A tone is a measurement. It
answers "how broken" or "how urgent".

| Tone | Meaning |
|---|---|
| `neutral` | A fact with no judgement |
| `accent` | A state somebody chose |
| `ok` | Healthy |
| `warn` | A person must act soon |
| `danger` | Broken now, or money is lost |

**Do not add a second vocabulary.** Extend `tone.ts` instead. Three vocabularies
existed before this file, and they disagreed.

⚠️ **A tone is not an identity.** Do not use a tone to say which tier or which
tag. That is a categorical hue and this app has none yet.

## 5. Components

| Class | Use |
|---|---|
| `.panel` + `.panel-head` | A section with a title and a description |
| `.stat` | One number. Add `good`, `caution` or `alarm` |
| `.chip` | A short state label. `tone.ts` picks the modifier |
| `.banner` | A message. Add `info` or `danger` |
| `.tabs` | Three or more tables in one panel |
| `.toolbar` + `.segmented` | Search and filter above a list |
| `.matrix` | A grid where two axes meet |
| `.formrow` + `.field` | A form on one line |
| `.empty` | A first-run state |

**Do not hand-roll a control.** Add the class to `globals.css`.

## 6. Type

| Size | Use |
|---|---|
| 21px | The page title |
| 15px | A section title |
| 14px | Body |
| 12px | A hint, or `.small` |
| 11px | A label, or a table heading |

**Use `--mono` for a model id, a slug or a token.** These are strings a person
copies.

## 7. Rules that came from a defect

1. **Show the gap, not the tables.** Three tables that must agree cannot be
   compared by eye. Draw the join.
2. **Lead with the number the reader opened the page for.** The roster showed
   four counts and no revenue.
3. **An empty state is not a blank table.** Name what is absent, and what it costs.
4. **Relay a refusal from the Console word for word.** A paraphrase invents a
   second vocabulary.
5. **Put the logic in `src/lib/`.** This app has no React renderer in its test
   suite. Logic inside JSX has no test.

## 8. Before you open a pull request

**Run these commands:**

```sh
npx tsc --noEmit
npx vitest run
npm run build
```

🔴 **Then look at the surface.** No test in this app measures layout. Open the
page, switch the theme, and look at it. An agent cannot do this step.

**Check each item:**

- [ ] The page reads in the dark theme and in the light theme.
- [ ] No call site writes a colour outside `globals.css`.
- [ ] Every state chip comes from `tone.ts`.
- [ ] A long value wraps. It does not truncate a credential.
- [ ] The keyboard reaches every control, and the focus ring is visible.
- [ ] The empty state names what is absent.

## 9. Where the data on a screen comes from

We build a screen before its backend exists. That is deliberate. It is also the
fastest way to put fiction in front of an operator.

**Four rules hold the line.**

1. **`src/lib/contract.ts` is the only shape a screen reads.** No page touches a
   Console JSON response. A late or different endpoint is one change in
   `read.ts`.
2. **`read.ts` stamps an origin on every read.** The origin travels with the
   data, so a screen cannot hold one without the other.
3. **`Shell` draws the banner.** A page that drew its own banner is a page that
   can forget one. `Shell` takes the origin as a required value.
4. **No file under `src/app/` may import `@/lib/sample`.**
   `source.test.ts` scans for it and fails.

**Four origins, and each says a different thing:**

| Origin | What it means | What the reader sees |
|---|---|---|
| `live` | This deployment answered | Nothing. The only silent case. |
| `sample` | Designed placeholder | A warning. None of the numbers are real. |
| `missing` | The backend is not built | What the backend still owes. |
| `error` | The Console refused | The refusal, word for word. |

**To see the sample data, set `OPERATOR_CONSOLE_SAMPLE_DATA=1`.**

🔴 **Production never sets it.** The flag is off unless a person sets it to
`1`, `true`, `yes` or `on`. The parse is strict on purpose. A loose test turns
`0` on, because `0` is a non-empty string.

⚠️ **A refusal outranks the flag.** An endpoint that answered 500 is not an
unbuilt feature. `resolve` returns `error` even in sample mode.
