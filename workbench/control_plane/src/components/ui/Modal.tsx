"use client";

/**
 * Modal — THE dialog primitive (WS-27ak item 1, D-PM-15).
 *
 * A Metorite wrapper over `@base-ui/react`'s `dialog`. **This file is the
 * only place in `src/` allowed to import that library** (conformance rule 8):
 * call sites import this, never the substrate, or the library's own defaults
 * quietly become a second design system — D-PM-15 condition 1, and condition 2
 * is why there is exactly one substrate to wrap.
 *
 * ## Why a library at all
 *
 * Measured at `00c47c6b` across the tree: **70 files** carry a `fixed inset-0`
 * overlay, **zero** of them trap focus and **zero** set `inert`. Driven in
 * Chromium against `/projects` with the shortcuts sheet open: focus never moved
 * into the dialog (`activeElement` stayed `BODY`), six Tabs walked it out into
 * the page behind, and closing left focus on an arbitrary anchor. None of that
 * is a bug in any one of those 70 files — it is a primitive that was never
 * written. The behaviour below is the part nobody hand-rolls correctly twice.
 *
 * ## What this gives every dialog, and where it comes from
 *
 * * **Focus moves in** on open and **returns to the opener** on close — and
 *   when the opener has unmounted, to the page's `<main>` landmark rather than
 *   to `<body>`. That fallback is **this wrapper's**, not the substrate's; the
 *   substrate has none. See `finalFocus` below for the exact order and for the
 *   one case where there is nothing left to focus.
 * * **Tab wraps at both ends** — `modal` (`true`, the default here) puts the
 *   popup in a real focus trap rather than a `keydown` handler that guesses.
 * * **The background is hidden from assistive tech and sealed against Tab —
 *   but NOT from find-in-page.** Base UI's `markOthers` sets
 *   `aria-hidden="true"` plus a `data-base-ui-inert` **marker** attribute on
 *   the popup's siblings; it never sets a real `inert` attribute
 *   (`FloatingFocusManager.js:339` calls `markOthers(…, { ariaHidden: modal })`
 *   and nothing in the package passes `inert: true` —
 *   `markOthers.mjs:147-155` defaults it to `false`). Measured on `/projects`
 *   with a dialog open: `[inert]` elements **0**. So a screen reader cannot
 *   walk into the page underneath and Tab cannot leave the dialog (guard
 *   nodes), and **Ctrl+F still finds the text behind the scrim**. That gap is
 *   the substrate's; closing it means a second implementation of `markOthers`,
 *   which is a board decision (`project_management_app.md` §11.31), not this
 *   file's. `aria-modal` is set here as a hint for assistive tech that predates
 *   `inert`; Base UI deliberately does not set it.
 * * **Scroll is locked with scrollbar-width compensation**, so the page does
 *   not shift sideways when the dialog opens. This is the one behaviour only a
 *   layout engine can verify, which is D-PM-21's whole argument for Playwright
 *   over jsdom — see `e2e/modal.spec.ts`.
 * * **Outside press dismisses only when the press both started _and_ ended
 *   outside**, so a text selection dragged out of the dialog does not close it.
 *
 * ⚠️ **`outsidePressEvent` is NOT a prop of `Dialog.Root` in `@base-ui/react`
 * 1.7.0** — the audit brief expected one. `dialog/root/useDialogRoot.mjs:23-33`
 * computes it internally and returns `'intentional'` (press must start and end
 * outside; `useDismiss.mjs:265` suppresses the started-inside case explicitly)
 * **whenever a backdrop element exists** — either `<Dialog.Backdrop>` or the
 * internal one `<Dialog.Portal>` renders for `modal === true`. This wrapper
 * always renders both, so the ticket's requirement holds by construction rather
 * than by a prop. That is what `Modal.test.ts` pins, and why it pins the
 * backdrop rather than a prop name that does not exist.
 *
 * ## Escape, and the one thing that must not break
 *
 * The ticket asked for Escape "captured at the dialog, not on `document`".
 * **The substrate cannot do that** — `floating-ui-react/hooks/useDismiss` binds
 * `keydown` on `ownerDocument(...)`. The observable that matters, and the one
 * `e2e/modal.spec.ts` asserts, is that **one Escape closes exactly one
 * surface**: `app/projects/page.tsx:1039`'s window listener returns early on
 * `live.current.overlayOpen` (`:1003`), so the page's own Escape and `g`/`v`
 * sequences stay suppressed while any of the six dialogs is up.
 *
 * That suppression is why this component is **strictly controlled and holds no
 * open state of its own**. `open` comes in, `onClose` goes out, and the page's
 * `overlayOpen` — which is derived from the page's state, not from anything in
 * here — keeps telling the truth. A wrapper that owned its own open state would
 * break that silently, after which `g` navigates the page out from under a
 * half-filled ClickUp import form.
 *
 * ## Theming
 *
 * Semantic tokens only (`bg-card`, `border-border`, `bg-background/80` for the
 * scrim — see DESIGN_SYSTEM.md §4a, which this primitive is the reason for),
 * icons through `<Icon name>`, and every control is a `<Button>` so it carries
 * `.cc-control`.
 *
 * ## Not consuming `src/lib/outsideClick.ts` — deliberate
 *
 * Wave 1 shipped that walker with a docstring naming "Wave 2's Modal / Tooltip
 * / Combobox" as why it existed ahead of need. Base UI brings its own
 * outside-press handling, with the start-and-end-outside rule the hand-rolled
 * walker does not express, so this Modal does **not** use it. `outsideClick.ts`
 * remains the answer for a popover we do not build on the substrate; a dialog
 * built on Base UI gets it from Base UI. Two mechanisms, one written-down
 * reason, no silent second answer.
 *
 * ## Retirement note
 *
 * `app/email/components/automation/ui.tsx:15` exports a second `Modal` with
 * five consumers, kept dependency-free "to match the rest of the email app,
 * which hand-rolls its overlays". It has window-Escape and backdrop dismiss and
 * **no** focus trap, focus return, scroll lock, `role` or `aria-modal`. It is
 * owed a retirement onto this one — an email ticket, not this slice, exactly as
 * WS-27bd recorded for the email `ContextMenu`. Do not add a sixth consumer.
 */

import { Dialog } from "@base-ui/react/dialog";
import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

/**
 * Reading width. The names are the Tailwind max-widths the six converted
 * dialogs already used, so adoption changed no dialog's size.
 */
export type ModalSize = "sm" | "md" | "lg" | "xl" | "2xl" | "3xl";

const SIZES: Record<ModalSize, string> = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-xl",
  "2xl": "max-w-2xl",
  "3xl": "max-w-3xl",
};

/**
 * Where the popup sits in the viewport.
 *
 * `top` is for surfaces raised by a keystroke that the eye should find without
 * moving — the command palette and the shortcuts sheet — and matches what both
 * already did.
 */
export type ModalPlacement = "center" | "top";

const PLACEMENT: Record<ModalPlacement, string> = {
  center: "items-center justify-center p-4",
  top: "items-start justify-center p-4 pt-16",
};

/**
 * Does the call site already say how tall the popup may be?
 *
 * The base class string used to carry `max-h-full` unconditionally, which made
 * every call site's height a **dead knob**: measured,
 * `getComputedStyle(popup).maxHeight === "100%"` with `max-h-[80vh]` passed in,
 * because two `max-height` utilities of equal specificity are decided by the
 * order Tailwind emits them, not by the order they appear in `class`. So the
 * default is applied only when the caller has not expressed one.
 *
 * Matches any responsive/state prefix (`sm:max-h-[70vh]`) as well, because a
 * caller who set a height at one breakpoint means to own the property.
 */
const CALLER_SETS_MAX_HEIGHT = /(?:^|\s)(?:[\w-]+:)*max-h-/;

/**
 * The landmark focus lands on when the opener is gone. First in document
 * order, which on every Metorite page is `AppShell`'s own `<main>`.
 */
const LANDMARK = "main, [role='main']";

/**
 * Focus a landmark that is not focusable, and leave the DOM as it was found.
 *
 * `<main>` has no tabindex, so `.focus()` on it does nothing. The skip-link
 * pattern is to add `tabindex="-1"` for the duration; the attribute is removed
 * again the first time the element loses focus, so a Tab from here behaves
 * exactly as it would have.
 *
 * Deliberately does the focusing itself, in a microtask, rather than handing
 * the element back to the substrate: Base UI runs the return element through
 * `getFirstTabbableElement()`, which for a `tabindex="-1"` container returns
 * its **first tabbable child** — i.e. the first control on the page, which is
 * a jump, not a return.
 */
function focusLandmark(el: HTMLElement): void {
  const owned = !el.hasAttribute("tabindex");
  if (owned) el.setAttribute("tabindex", "-1");
  queueMicrotask(() => {
    el.focus({ preventScroll: true });
    if (owned) {
      el.addEventListener("blur", () => el.removeAttribute("tabindex"), { once: true });
    }
  });
}

export type ModalProps = {
  /**
   * Whether the dialog is up. **Controlled, always** — this component holds no
   * open state, so the page that renders it stays the single source of truth
   * for whether an overlay is up (see the note on `overlayOpen` above).
   */
  open: boolean;
  /** Called for every dismissal: Escape, outside press, or the close button. */
  onClose: () => void;
  /**
   * Visible title. Rendered as `<Dialog.Title>`, which supplies
   * `aria-labelledby`. Omit it for a surface whose first row is its own header
   * (the search palette) and pass `label` instead.
   */
  title?: React.ReactNode;
  /** Accessible name when there is no visible `title`. */
  label?: string;
  /** One line under the title. Rendered as `<Dialog.Description>`. */
  description?: React.ReactNode;
  /** Lucide icon name shown before the title; follows the active pack. */
  icon?: string;
  size?: ModalSize;
  placement?: ModalPlacement;
  /**
   * Whether the default header draws a close button. A dialog with no header
   * (no `title`) has nowhere to put one; Escape and outside press still close
   * it.
   */
  showClose?: boolean;
  /**
   * Accessible name of the close button. Defaults to `"Close"`, which is right
   * for a dialog whose title is already on screen beside it. Pass a specific
   * one where the button is reachable out of that context — a screen-reader
   * user listing the page's buttons hears the label and nothing else.
   */
  closeLabel?: string;
  /**
   * What to focus on open. Defaults to Base UI's behaviour — the first tabbable
   * element, or the popup itself when opened by touch. Pass a ref to aim it at
   * a search box; pass `false` only with a reason.
   */
  initialFocus?: React.ComponentProps<typeof Dialog.Popup>["initialFocus"];
  /**
   * Where focus goes on close. **This wrapper resolves it**, in this order,
   * stopping at the first that holds:
   *
   *   1. `finalFocus`, when you pass a ref whose element is still connected;
   *   2. the element that had focus at the moment this dialog opened, captured
   *      here, when it is still connected — the ordinary case;
   *   3. the page's `<main>` landmark (`main, [role="main"]`), focused with a
   *      `tabIndex={-1}` this wrapper adds and removes again on blur;
   *   4. **nothing.** A document with no `<main>` and no `[role="main"]` leaves
   *      focus wherever the browser put it, which is `<body>`. There is no
   *      fifth thing to focus and this wrapper does not invent one.
   *
   * Pass `false` to leave focus alone entirely.
   *
   * ⚠️ **Step 3 exists because the substrate has no fallback at all**, contrary
   * to what this file used to claim. `FloatingFocusManager.js:476` drops
   * `elementFocusedBeforeOpen` when `!isConnected`,
   * `getPreviouslyFocusedElement()` filters disconnected elements and returns
   * `undefined`, `getReturnElement` returns `null`, and **no `.focus()` runs**
   * — so focus stays on `<body>`. That is reachable in this app, not
   * theoretical: `ImportClickUp`'s only trigger is the empty-state button
   * (`ProjectTree.tsx:140`), a real import calls `onImported()`, `page.tsx`
   * bumps `treeKey`, and the tree refetches and replaces the trigger **while
   * the dialog is still open**. Fenced by `e2e/modal.spec.ts`, "focus does not
   * fall to `<body>` when the opener unmounted".
   */
  finalFocus?: React.RefObject<HTMLElement | null> | false;
  /**
   * Layout classes for the popup — height, flex, overflow. Never colour.
   *
   * A `max-h-*` here **wins**: the wrapper drops its own `max-h-full` when the
   * caller expresses a height. See `CALLER_SETS_MAX_HEIGHT`.
   */
  className?: string;
  children?: React.ReactNode;
};

export default function Modal({
  open,
  onClose,
  title,
  label,
  description,
  icon,
  size = "lg",
  placement = "center",
  showClose = true,
  closeLabel = "Close",
  initialFocus,
  finalFocus,
  className = "",
  children,
}: ModalProps) {
  /**
   * The element that had focus when this dialog opened.
   *
   * Captured **during the render in which `open` flips true** — React's
   * adjusting-state-on-a-prop-change pattern — and deliberately not in an
   * effect. `Dialog.Popup` is a child and the substrate reads its own
   * `elementFocusedBeforeOpen` in a **layout** effect
   * (`FloatingFocusManager.js:418`); child layout effects run before the
   * parent's, so no effect here can see the opener. During render the DOM has
   * not been touched yet. State rather than a ref because a ref written during
   * render is a `react-hooks/refs` error, and because React re-runs this render
   * before touching any child — the popup has not mounted when the read
   * happens.
   *
   * The transition has to be detected rather than the value simply seeded at
   * mount, because the six call sites are not all the same shape:
   * `SearchPalette` keeps its `Modal` mounted and toggles `open`, while the
   * other five mount already open.
   */
  // `false`, never `open`: `ShortcutsSheet` and the four managers MOUNT with
  // `open` already true, so seeding this from the prop would make the first
  // render look like "no change" and the opener would never be captured at all.
  const [wasOpen, setWasOpen] = useState(false);
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open && typeof document !== "undefined") {
      const active = document.activeElement;
      setOpener(active instanceof HTMLElement && active !== document.body ? active : null);
    }
  }

  /** The order documented on `finalFocus`. Runs in Base UI's close cleanup. */
  const returnFocus = () => {
    if (finalFocus === false) return false;
    const explicit = finalFocus?.current;
    if (explicit?.isConnected) return explicit;
    if (opener?.isConnected) return opener;
    const landmark = document.querySelector<HTMLElement>(LANDMARK);
    if (landmark) focusLandmark(landmark);
    // `false` either way: step 3 focuses the landmark itself (see
    // `focusLandmark`), and step 4 has nothing to focus. Returning an element
    // here would hand it to `getFirstTabbableElement()` instead.
    return false;
  };

  return (
    <Dialog.Root
      open={open}
      // Every dismissal routes to the caller's `onClose`. `open` is never
      // stored here, so the caller's state and what is on screen cannot drift.
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      // `true`, not `'trap-focus'`: the second one traps focus but leaves the
      // page scrollable and outside pointer events live, which is a dialog you
      // can scroll the document behind.
      modal
    >
      {/* The portal also renders the internal backdrop that makes outside
          press `intentional` — see this file's header. */}
      <Dialog.Portal>
        <Dialog.Backdrop
          // DESIGN_SYSTEM.md §4a — the one scrim token. Not `bg-black/…`:
          // black is not a theme token and passes every colour rule in
          // `conformance.test.ts` only because `PALETTE_CLASS` lists the
          // numbered ramps and not `black`/`white`.
          className="fixed inset-0 z-50 bg-background/80"
        />
        {/* `Dialog.Viewport`, not a hand-written positioning div: it is
            `role="presentation"`, hidden while unmounted, and drops its own
            pointer events when closed. A raw div does none of that. */}
        <Dialog.Viewport className={`fixed inset-0 z-50 flex ${PLACEMENT[placement]}`}>
          <Dialog.Popup
            aria-label={title ? undefined : label}
            // Base UI does not set this. It is a hint for assistive tech that
            // predates `inert` — and here it is doing real work, because the
            // substrate marks the background with `aria-hidden` and a
            // `data-base-ui-inert` marker rather than a real `inert` attribute
            // (see this file's header).
            aria-modal="true"
            initialFocus={initialFocus}
            finalFocus={returnFocus}
            className={`flex ${
              CALLER_SETS_MAX_HEIGHT.test(className) ? "" : "max-h-full"
            } w-full ${SIZES[size]} flex-col overflow-hidden rounded-lg border border-border bg-card shadow-lg outline-none ${className}`}
          >
            {title ? (
              <header className="flex items-center gap-2 border-b border-border px-3 py-2">
                {icon ? (
                  <Icon name={icon} className="h-4 w-4 shrink-0 text-muted-foreground" />
                ) : null}
                <div className="min-w-0 flex-1">
                  <Dialog.Title className="truncate text-sm font-medium text-foreground">
                    {title}
                  </Dialog.Title>
                  {description ? (
                    // NOT `truncate`. The title is one line by design; the
                    // description is a sentence, and on a phone
                    // `ImportClickUp`'s ("Every Preview writes nothing. Only
                    // the buttons that say so write.") clipped mid-word — which
                    // deletes the half that says which buttons.
                    <Dialog.Description className="text-xs text-muted-foreground">
                      {description}
                    </Dialog.Description>
                  ) : null}
                </div>
                {showClose ? (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    icon="X"
                    aria-label={closeLabel}
                    onClick={onClose}
                  />
                ) : null}
              </header>
            ) : null}
            {children}
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
