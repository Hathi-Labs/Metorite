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
 * - Escape ends the tour in the same frame (G10). The page stays where it is.
 * - A start of the tour (`home.tour`) from any entry stamps `tour_seen_at`
 *   in the shell prefs (GD-5a). A resume after a reload does not.
 *
 * The store refuses every start while `GUIDE_ORGS` is off for the org
 * (`guide.ts`, GD-5a), so `?guide=` here then starts nothing. The parameter
 * still leaves the address.
 */

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

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
import { assistantState, openAssistant, setAssistantMode } from "./assistant";
import GuideSpotlight, { bindEscape } from "./GuideSpotlight";
import { markTourSeen } from "./shellPrefs";

/** The one phone stop whose strip is under the Menu drawer, so its popover stays. */
const DRAWER_ANCHOR = "home.app-list";

/** Whether the anchor sits inside the assistant. */
export const isChatAnchor = (anchor: string | null): boolean => !!anchor && anchor.startsWith("chat.");

/**
 * Whether the spotlight draws its popover. On a phone the strip or the step
 * card carries the line, except under the Menu drawer. On the tour's last
 * stop the step card carries the app chips (GD-5a). That stop rings the
 * message field, and a popover above the field would cover the chips, so the
 * card alone carries the line there.
 */
export function showsPopover(a: { phone: boolean; anchor: string; tourLast: boolean }): boolean {
  if (a.tourLast && isChatAnchor(a.anchor)) return false;
  return !a.phone || a.anchor === DRAWER_ANCHOR;
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

export default function GuideHost({ phone = false }: { phone?: boolean }) {
  const g = useGuide();
  const pathname = usePathname();
  const router = useRouter();
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
  useEffect(() => {
    const id = guideFromSearch(window.location.search);
    if (id !== null) {
      startGuide(id, "url");
      // Out of the address, so a reload resumes and does not start again.
      const { pathname: p, search, hash } = window.location;
      window.history.replaceState(window.history.state, "", withoutGuideParam(p, search, hash));
    } else {
      resumeGuide();
    }
  }, []);

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

  if (!view || g.phase !== "pointing" || !view.anchor) return null;
  return (
    <GuideSpotlight
      key={`${view.guide.id}:${g.step}`}
      anchor={view.anchor}
      say={view.say}
      step={{ index: view.index, total: view.total }}
      onNext={nextStep}
      onEnd={() => endGuide("end")}
      popover={showsPopover({ phone, anchor: view.anchor, tourLast: view.guide.id === TOUR_GUIDE_ID && view.isLast })}
      onAnchorClick={nextStep}
      onMissing={guideAnchorMissing}
    />
  );
}
