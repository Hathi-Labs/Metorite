"use client";

/**
 * The guide host (WS-OB GD-4, D167, D89). The shell mounts ONE of it
 * (`AppShell.tsx`), and no app mounts another. It joins the guide store
 * (`guide.ts`) to the page:
 *
 * - It reads a start the member made: `?guide=<id>` in the address, or a
 *   stored mark after a reload. It starts nothing else (G1, F4).
 * - It pushes the route of an `open` step, once, and tells the store when the
 *   page arrived.
 * - It rings the anchor of a `point` step with `GuideSpotlight`.
 * - It opens the assistant where the step needs it: the panel for a `chat.*`
 *   anchor, and on a phone the peek strip (`assistant_guide.md` §4.3), which
 *   shows the step line with Next and End.
 * - It holds the ONE live region that announces each step's line, at every
 *   width. The popover, the step card and the strip hold none.
 * - Escape ends the tour in the same frame (G10). The page stays where it is.
 * - A start of the tour (`home.tour`) from any entry stamps `tour_seen_at`
 *   in the shell prefs (GD-5a). A resume after a reload does not.
 *
 * The store refuses every start while `GUIDE_ORGS` is off for the org
 * (`guide.ts`, GD-5a), so `?guide=` here then starts nothing. The parameter
 * still leaves the address.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import {
  GUIDE_MENU_EVENT,
  endGuide,
  guideAnchorMissing,
  guideFromSearch,
  guidePushed,
  guideRouteChanged,
  guideView,
  isActive,
  nextStep,
  resumeGuide,
  startGuide,
  TOUR_GUIDE_ID,
  useGuide,
  withoutGuideParam,
} from "./guide";
import { assistantState, openAssistant, setAssistantMode, useAssistant } from "./assistant";
import GuideSpotlight, { bindEscape } from "./GuideSpotlight";
import { markTourSeen } from "./shellPrefs";

/** The one phone stop whose strip is under the Menu drawer, so its popover stays. */
const DRAWER_ANCHOR = "home.app-list";

/** Whether the anchor sits inside the assistant. */
export const isChatAnchor = (anchor: string | null): boolean => !!anchor && anchor.startsWith("chat.");

/**
 * Whether the spotlight draws its popover. The line shows ONCE. The popover
 * shows only when neither the step card nor the phone strip is on screen:
 *
 * - The step card sits in the assistant, so with the assistant open (the side
 *   panel on a desktop) the card carries the line.
 * - On a phone the strip or the sheet's card carries it. The one exception is
 *   the Menu drawer stop, whose strip is under the drawer.
 * - On the tour's last stop the card carries the app chips (GD-5a), and a
 *   popover above the message field would cover them.
 * - An anchor inside an open dialog (`[role="dialog"]`) is the exception to
 *   all of these. The dialog's backdrop covers the dock, the strip and the
 *   card, so the popover alone can carry the line. It sits on the `top`
 *   panel layer, above a dialog.
 */
export function showsPopover(a: {
  phone: boolean;
  anchor: string;
  tourLast: boolean;
  /** The assistant is open, in any form. */
  assistantOpen: boolean;
  /** The anchor sits inside an open dialog. */
  inDialog?: boolean;
}): boolean {
  if (a.inDialog) return true;
  if (a.tourLast && isChatAnchor(a.anchor)) return false;
  if (a.phone) return a.anchor === DRAWER_ANCHOR;
  return !a.assistantOpen;
}

/**
 * Where the assistant must be for a step (`assistant_guide.md` §4.3).
 * - desktop: open for a `chat.*` anchor, a step with no anchor, and a missing anchor.
 * - phone: the full sheet for a `chat.*` anchor, else the peek strip.
 * Null when it need not move.
 */
export function assistantFor(a: {
  phone: boolean;
  anchor: string | null;
  missing: boolean;
  open: boolean;
  mode: "dock" | "full" | "peek";
}): "dock" | "full" | "peek" | null {
  const chat = isChatAnchor(a.anchor);
  if (a.phone) {
    const want = chat ? "full" : "peek";
    return a.open && a.mode === want ? null : want;
  }
  const needs = chat || a.anchor === null || a.missing;
  return needs && !a.open ? "dock" : null;
}

function GuideHostInner({ phone = false }: { phone?: boolean }) {
  const g = useGuide();
  const pathname = usePathname();
  const router = useRouter();
  const assistant = useAssistant();
  // The address, reactive: a client move to `/?guide=home.tour` (the account
  // menu's "Take the tour" from another page) starts the guide too.
  const searchKey = useSearchParams()?.toString() ?? "";
  const active = isActive(g);
  const view = active ? guideView() : null;
  const anchor = view?.anchor ?? null;
  const missing = g.phase === "missing";

  // The route first, so a start from the address knows where the page is.
  useEffect(() => {
    guideRouteChanged(pathname ?? "/");
  }, [pathname]);

  // The two ways in that a member made earlier: the link they followed, or a
  // stored mark of this tab. A mount with neither starts nothing.
  const mounted = useRef(false);
  useEffect(() => {
    const id = guideFromSearch(window.location.search);
    if (id !== null) {
      startGuide(id, "url");
      // Out of the address at once, so a reload resumes and does not start
      // again, and the flag read (which can be slow) never holds it there.
      // ⚠️ Through the router, never `history.replaceState`: that call keeps
      // the router's own `__NA` state, so Next skips its sync, keeps the old
      // address in its tree, and writes it back on the next change.
      const { pathname: p, search, hash } = window.location;
      router.replace(withoutGuideParam(p, search, hash), { scroll: false });
    } else if (!mounted.current) {
      resumeGuide();
    }
    mounted.current = true;
  }, [searchKey, router]);

  // GD-5a: a member started the tour, from any entry. The record keeps the
  // first start, and a resume after a reload is not a start.
  useEffect(() => {
    if (active && g.guideId === TOUR_GUIDE_ID && g.source !== "resume") void markTourSeen();
  }, [active, g.guideId, g.source]);

  // Escape ends the tour, with no timer and no exit move. Bound once, before
  // any spotlight, so the reason is Escape.
  useEffect(() => bindEscape(document, () => endGuide("escape")), []);

  // An `open` step pushes its route once.
  useEffect(() => {
    if (g.push) {
      const path = g.push;
      guidePushed();
      router.push(path);
    }
  }, [g.push, router]);

  // Put the assistant where the step needs it, on a step change only, so a
  // member who raises the strip is not dropped back.
  useEffect(() => {
    if (!active) return;
    const a = assistantState();
    const form = assistantFor({ phone, anchor, missing, open: a.open, mode: a.mode });
    if (!form) return;
    // A move of the form keeps the composer closed to the keyboard. An open asks for the cursor.
    if (a.open && phone) setAssistantMode(form);
    else if (phone) openAssistant({ mode: form });
    else openAssistant();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a step change only
  }, [active, g.guideId, g.step, missing, phone]);

  // A phone keeps its app list in the Menu drawer: open it for that stop, and
  // close it when the tour moves on or ends.
  const menuOpenRef = useRef(false);
  const wantMenu = phone && active && anchor === DRAWER_ANCHOR;
  useEffect(() => {
    if (wantMenu === menuOpenRef.current) return;
    menuOpenRef.current = wantMenu;
    window.dispatchEvent(new CustomEvent(GUIDE_MENU_EVENT, { detail: wantMenu ? "open" : "close" }));
  }, [wantMenu]);

  const spot =
    view && g.phase === "pointing" && view.anchor ? (
      <GuideSpotlight
        key={`${view.guide.id}:${g.step}`}
        anchor={view.anchor}
        say={view.say}
        step={{ index: view.index, total: view.total }}
        onNext={nextStep}
        onEnd={() => endGuide("end")}
        last={view.isLast}
        popover={(inDialog) =>
          showsPopover({
            phone,
            anchor: view.anchor!,
            tourLast: view.guide.id === TOUR_GUIDE_ID && view.isLast,
            assistantOpen: assistant.open,
            inDialog,
          })
        }
        onAnchorClick={nextStep}
        onMissing={guideAnchorMissing}
      />
    ) : null;
  return (
    <>
      {/* The ONE live region of the guide: it announces each step's line, on
          a desktop and on a phone. It stays in the tree, so a change of its
          text is announced. It sits on `body`: an open Modal hides the app
          shell from a screen reader (`aria-hidden`), and a region inside the
          shell would go silent while the tour points into the Modal. */}
      {createPortal(
        <div role="status" aria-live="polite" data-guide-live="" className="sr-only">
          {view ? view.say : ""}
        </div>,
        document.body,
      )}
      {spot}
    </>
  );
}

export default function GuideHost({ phone = false }: { phone?: boolean }) {
  return (
    <Suspense fallback={null}>
      <GuideHostInner phone={phone} />
    </Suspense>
  );
}
