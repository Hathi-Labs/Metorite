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
 * - It draws ONE tour card (`TourCard`) in the guide's top layer, above the
 *   dim. The card never sits in the side chat (owner, 2026-11-03: there it
 *   mixed in with the chat's own buttons). On the tour's last stop it holds
 *   the app chips (GD-5a).
 * - It opens the assistant where the step needs it: the panel for a `chat.*`
 *   anchor, and on a phone the peek strip or the sheet (`assistant_guide.md`
 *   §4.3). The chat draws nothing for the tour.
 * - It holds the ONE live region that announces each step's line, at every
 *   width. The card holds none.
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

import { useAccess } from "@/components/AccessProvider";
import { visibleSections } from "@/lib/nav";

import {
  GUIDE_MENU_EVENT,
  backStep,
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
  tourChips,
  useGuide,
  withoutGuideParam,
  type TourChip,
} from "./guide";
import { assistantState, closeAssistant, onChatLink, openAssistant, setAssistantMode } from "./assistant";
import { chatFirstOn } from "./chatFlag";
import GuideSpotlight, { GuideDim, TourCard, bindEscape } from "./GuideSpotlight";
import { starrableHeld } from "./presets";
import { markTourSeen } from "./shellPrefs";

/** The phone stop that opens the Menu drawer for its app list. */
const DRAWER_ANCHOR = "home.app-list";

/** Whether the anchor sits inside the assistant. */
export const isChatAnchor = (anchor: string | null): boolean => !!anchor && anchor.startsWith("chat.");

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
  const { access, loading } = useAccess();
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

  // The live apps the member holds, by the sidebar's own filter, with the
  // preview flag off: a guide never points at a preview pane (§1). Only the
  // tour's last stop shows them.
  const chips: TourChip[] =
    view && view.guide.id === TOUR_GUIDE_ID && view.isLast
      ? tourChips(starrableHeld(visibleSections(loading ? null : access.features, access.is_admin, false)))
      : [];
  const onChip = (c: TourChip) => {
    // GD-10: start the app's chapter. Until then the chip ends the tour and opens the app.
    endGuide("done");
    // On a phone the sheet gets out of the way, as for a tapped chat link.
    const act = onChatLink({ phone, chatFirst: chatFirstOn() });
    if (act === "peek") setAssistantMode("peek");
    else if (act === "close") closeAssistant();
    router.push(c.href);
  };
  const card = view
    ? {
        say: view.say,
        step: { index: view.index, total: view.total },
        onBack: g.step > 0 ? backStep : undefined,
        onNext: nextStep,
        onEnd: () => endGuide("end"),
        last: view.isLast,
        chips,
        onChip,
        phone,
      }
    : null;
  const spot =
    view && card && g.phase === "pointing" && view.anchor ? (
      <GuideSpotlight
        key={`${view.guide.id}:${g.step}`}
        anchor={view.anchor}
        {...card}
        onAnchorClick={nextStep}
        onMissing={guideAnchorMissing}
      />
    ) : null;
  // The anchor is missing: no ring, and the dim has no hole. The card still
  // shows, with the reason.
  const missingCard = view && card && g.phase === "missing" ? <TourCard key="missing" rect={null} missing {...card} /> : null;
  // The dim layer (owner request 2026-11-03): one scrim over the window, with
  // ONE hole, the ringed part. It comes first in the one portal, so the ring
  // and the card paint over it.
  const dim =
    view && (g.phase === "pointing" || g.phase === "missing") ? (
      <GuideDim anchor={view.anchor} missing={missing} />
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
      {/* ONE portal for the dim, the ring and the card, in that order. React
          inserts a late child at its place among its siblings, so the card
          stays above the dim whichever of them mounts first. */}
      {createPortal(
        <>
          {dim}
          {spot}
          {missingCard}
        </>,
        document.body,
      )}
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
