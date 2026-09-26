"use client";

/**
 * Scroll-into-view + flash for a card that just landed (WS-27y, shared).
 *
 * Born in /projects and promoted here so /tasks announces a drop or quick-add
 * the same way — one landing animation across both task surfaces.
 *
 * After any drop or quick-add, the moved/created card must be SEEN to land —
 * scrolled into view and briefly tinted — or the gesture ends in silence and
 * people re-do it. The awkward part is timing: at the moment of the gesture
 * the card's element may not exist yet (a quick-add's card appears only after
 * the reload; a cross-column drop remounts the card in its new column). So a
 * flash is *pending* until the element shows up: `flash(id)` fires
 * immediately when the node is on the page, and otherwise the ref callback
 * fires it the moment React mounts it. The pending marker expires after a few
 * seconds so a card re-mounted much later (a filter change) does not flash
 * out of nowhere.
 *
 * Refs and classList rather than state, deliberately: a flash must not
 * re-render three hundred cards, and the animation itself is CSS
 * (`flash.module.css`, theme tokens only).
 */

import { useCallback, useRef } from "react";

import styles from "./flash.module.css";

const PENDING_MS = 3000;
const CLEAR_MS = 1600;

export interface Flash {
  /** Flash the element registered under `id`, now or when it next mounts. */
  flash: (id: string) => void;
  /** Ref callback registering an element under `id`. */
  attach: (id: string) => (el: HTMLElement | null) => void;
  /** Scroll to a registered element without flashing — the cursor's need. */
  scrollTo: (id: string) => void;
  /** The element registered under `id` now, or null — what a menu hangs from. */
  element: (id: string) => HTMLElement | null;
}

export function useFlash(): Flash {
  const els = useRef(new Map<string, HTMLElement>());
  const pending = useRef<string | null>(null);
  const expiry = useRef<ReturnType<typeof setTimeout> | null>(null);

  const run = useCallback((el: HTMLElement) => {
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
    // Remove-reflow-add restarts the animation when the same card flashes
    // twice in a row (two quick-adds into one column).
    el.classList.remove(styles.flash);
    void el.offsetWidth;
    el.classList.add(styles.flash);
    setTimeout(() => el.classList.remove(styles.flash), CLEAR_MS);
  }, []);

  const flash = useCallback(
    (id: string) => {
      // Pending survives an immediate run: a drop flashes the card where it
      // stands AND re-flashes it if the reload remounts it in its new column.
      pending.current = id;
      if (expiry.current) clearTimeout(expiry.current);
      expiry.current = setTimeout(() => {
        pending.current = null;
      }, PENDING_MS);
      const el = els.current.get(id);
      if (el) run(el);
    },
    [run]
  );

  const attach = useCallback(
    (id: string) => (el: HTMLElement | null) => {
      if (el) {
        els.current.set(id, el);
        if (pending.current === id) {
          pending.current = null;
          run(el);
        }
      } else {
        els.current.delete(id);
      }
    },
    [run]
  );

  const scrollTo = useCallback((id: string) => {
    els.current.get(id)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []);

  const element = useCallback((id: string) => els.current.get(id) ?? null, []);

  return { flash, attach, scrollTo, element };
}
