# MOTION.md

Rules for adding motion to an interface.

**Prerequisite:** `DESIGN_SYSTEM.md` must pass first. Motion is applied to a
component that already works, never as part of building one. If a component fails
the verification list in `DESIGN_SYSTEM.md`, fix that and stop. Do not animate it.

The default state of any element is **not animated**. Motion is added element by
element, each time by passing the gate in §1. There is no rule in this file that
tells you to animate something. Every rule tells you when you are allowed to.

---

## 1. The gate

Before you animate anything, name which of these four it does. If none apply,
**do not animate it.** "It looks better" and "it feels more polished" are not
entries on this list.

| # | Purpose | The animation answers |
|---|---------|----------------------|
| **C** | Continuity | "Is this the same object I was just looking at?" |
| **F** | Feedback | "Did my input land?" |
| **S** | Spatial model | "Where did this come from, and where does it go?" |
| **P** | Progress | "Is the system still working?" |

State the letter in a comment above the rule:

```css
/* S: drawer enters from the edge it will return to */
.drawer { transition: transform 300ms var(--ease-out); }
```

If you cannot write that comment truthfully, delete the transition.

**Gate failures. These are decoration, not motion:**

- Elements fade or slide in on page load because they are new to the viewport
- Scroll-triggered reveals on body content
- Hover effects that move an element instead of a change to its surface
- Staggered entrances on lists the user did not just cause to change
- Anything that animates because a library made it easy

## 2. Context tiers

Duration is a function of how much of the screen changes. It is also a function of
whether the user manipulates it directly. Pick the tier, then use its range. Never
go above 400ms for anything a user started.

| Tier | Applies to | In | Out | Easing |
|------|-----------|-----|-----|--------|
| **0 — Direct** | press, drag, gesture-follow | 0ms | 100–150ms | `--ease-out`, or spring while it tracks |
| **1 — Micro** | icon swap, checkbox, hover surface, focus ring | 100–160ms | 80–120ms | `--ease-out` |
| **2 — Local** | tooltip, dropdown, popover, toast, inline expand | 180–240ms | 120–180ms | `--ease-out` in, `--ease-in` out |
| **3 — Surface** | dialog, drawer, sheet, view transition | 260–360ms | 200–260ms | `--ease-out` in, `--ease-in` out |
| **4 — Ambient** | spinner, skeleton, indeterminate progress | loop | — | `linear` |

Rules that follow from the table:

- **An exit is always faster than an entry.** About 70%. The user has
  decided already. Do not make them wait for the interface to agree.
- **A press-in is 0ms.** Feedback for direct manipulation cannot have a ramp. The
  finger is there already. Only the release has a duration.
- **More travel gets more time, not more easing.** A drawer that crosses 400px
  needs Tier 3 because of the distance, not because it is important.

## 3. Tokens

Define a value once, and refer to it everywhere. NEVER write a raw duration or a
raw cubic-bezier at a call site.

```css
:root {
  --dur-instant: 0ms;
  --dur-fast:   120ms;
  --dur-snap:   180ms;
  --dur-base:   240ms;
  --dur-slow:   320ms;

  --ease-out:      cubic-bezier(0.2, 0, 0, 1);
  --ease-in:       cubic-bezier(0.4, 0, 1, 1);
  --ease-spring:   cubic-bezier(0.34, 1.42, 0.64, 1);
}
```

- `--ease-out` is the default. Use it unless you can say why not.
- `--ease-in` is for exits only. Never use it for something that enters. It starts
  slow, and a reader sees that as lag.
- `--ease-spring` goes past the target and comes back. Use it only where that means
  something physical: a switch thumb, a drag release, a pull-to-refresh. NEVER use
  it on text, on dialogs, or on anything that reports an error or a destructive
  result.
- NEVER use `ease-in-out` for interface motion. It is slow at both ends, and it
  makes short durations feel longer than they are.
- NEVER use `linear`, except for continuous loops and pure opacity crossfades.

## 4. What you may animate

**Allowed:** `transform`, `opacity`. These composite on the GPU, and they cost no
layout and no paint.

**Allowed with care.** These paint, but they do not reflow: `color`,
`background-color`, `border-color`, `box-shadow`, `filter`. Keep them to Tier 0–2.

**NEVER animate:** `width`, `height`, `top`, `left`, `right`, `bottom`, `margin`,
`padding`, `font-size`. These start layout on every frame. Use `transform:
translate()` and `scale()` instead.

**NEVER use `transition: all`.** Name the properties. `all` animates things you did
not intend, and that includes a property somebody adds later.

Other property rules:

- Set `transform-origin` deliberately. A menu that opens from a button MUST scale
  from the corner nearest that button, and not from its centre. If it does not, the
  menu materialises instead of emerges. This is the highest-value single line in
  most dropdown code.
- Scale for press feedback is **0.97 to 0.98**. Below 0.95 reads as a gimmick.
- Put the base transition on the element, and override the duration on the state.
  That is how you get asymmetric timing without keyframes:
  ```css
  .button {
    transition: transform var(--dur-fast) var(--ease-out);
  }
  .button:active {
    transform: scale(0.97);
    transition-duration: var(--dur-instant);
  }
  ```

## 5. Interruption

- Every animation MUST accept an interruption. If the user acts while it is in
  flight, the animation goes to the new target from where it is. It does not finish
  first, and it does not queue.
- Prefer `transition` to `@keyframes` for a state change. A transition interrupts
  correctly by default, and a keyframe animation starts again.
- For anything the user drags or throws, use a spring that velocity drives, not a
  tween with a fixed duration. A fixed duration cannot answer how fast they moved.
- NEVER queue animations behind each other in answer to repeated input.

## 6. Budget

- At most **one Tier 3 animation in flight** at one moment. If two surfaces must
  move, they are one movement and not two.
- At most **three animated moments per view** that a user notices. Tier 0 and
  Tier 1 feedback do not count against this. They exist to go unnoticed.
- Stagger only when the user caused the collection to change, and only for **six
  items or fewer**, at **20 to 40ms** apart. A longer list MUST animate as one
  block.
- NEVER animate content on the first page load. The first paint is not a state
  change.

## 7. Never animate these

This applies whatever the tier and whatever the gate:

- An error message that appears. It must be there the instant it is true.
- Focus rings. They must follow the keyboard exactly.
- Destructive confirmations, and anything the user must read before they act.
- Values that update inside a data table or a dashboard the user is reading.
- Loading indicators for an operation you expect to take less than **200ms**. Draw
  nothing instead of a spinner that flashes.
- Anything that would move text a user is reading now.

## 8. Reduced motion

- Every file that defines motion MUST carry this, and you MUST test it with the
  operating-system setting on:
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 1ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 1ms !important;
      scroll-behavior: auto !important;
    }
  }
  ```
- Reduced motion MUST NOT remove information. An animation is sometimes the only
  signal that something changed. The reduced-motion path then needs a substitute
  that does not move: a colour change, a text update, or an announcement.
- Prefer a cross-fade to a removal where the motion carried continuity (gate C).

---

## Verification

For each animated element, confirm:

1. The gate letter (C, F, S or P) is in a comment, and it is truthful.
2. Its tier matches its scope, and its duration is inside that tier's range.
3. The exit is faster than the entry.
4. Only `transform` and `opacity` animate, or the exception is deliberate.
5. There is no `transition: all` in the file.
6. An interruption in flight redirects it. It does not queue, and it does not
   start again.
7. `transform-origin` is set on purpose on anything that scales.
8. The view stays inside the budget: one Tier 3 at most, three noticeable moments
   at most.
9. With `prefers-reduced-motion: reduce` on, the component still tells the reader
   everything it told them before.

Then remove one. There is almost always one animation in a finished view that
exists because it was easy, and not because it was necessary. Find it and delete
it.
