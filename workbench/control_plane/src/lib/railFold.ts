/**
 * A left rail that folds itself below `lg`, and respects the member.
 *
 * Measured 2026-09-24 at 768px on `/tasks`: the app's nav rail and the My
 * Tasks lists rail were both open, the Inbox pane was about 270px wide, and
 * the capture field inside it was 105px. A capture field that narrow cannot
 * show the sentence being typed into it.
 *
 * So the rail STARTS folded below `lg` (1024px) and open at or above it. The
 * toggle in the app bar still opens it. The member's choice holds until the
 * width crosses into the other band. Then the band's default applies again,
 * because a choice made on a tablet says nothing about a desktop monitor.
 *
 * Three pure steps and one hook. The steps are what `railFold.test.ts`
 * fences. The runner is `environment: "node"`, so the hook is not tested.
 */

import { useEffect, useState } from "react";

/** Tailwind's `lg`. The rail is open by default at or above it. */
export const RAIL_WIDE_QUERY = "(min-width: 1024px)";

export type RailBand = "wide" | "narrow";

export interface RailState {
  open: boolean;
  band: RailBand;
}

/** The default for a band: open when wide, folded when narrow. */
export function railStart(band: RailBand): RailState {
  return { open: band === "wide", band };
}

/**
 * The width moved. Inside one band this changes nothing, so the member's
 * choice holds. Across bands the new band's default applies.
 */
export function railOnBand(state: RailState, band: RailBand): RailState {
  return band === state.band ? state : railStart(band);
}

/** The member pressed the toggle. */
export function railToggle(state: RailState): RailState {
  return { ...state, open: !state.open };
}

function currentBand(): RailBand {
  if (typeof window === "undefined" || !window.matchMedia) return "wide";
  return window.matchMedia(RAIL_WIDE_QUERY).matches ? "wide" : "narrow";
}

/**
 * The state the FIRST render uses, on the server and on the client alike.
 *
 * ⚠️ It must not read the window. The server has no window and renders
 * "wide", with the rail. A client initializer that read `matchMedia` below
 * 1024px rendered "narrow", without it: a hydration mismatch on every tablet
 * and phone load. So both start wide, and the mount effect folds the rail.
 * Fence: `railFold.test.ts`.
 */
export const RAIL_INITIAL: RailState = railStart("wide");

/** The rail's open state, following the width band. */
export function useRailFold(): { open: boolean; toggle: () => void } {
  const [state, setState] = useState<RailState>(RAIL_INITIAL);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(RAIL_WIDE_QUERY);
    const onChange = () => setState((s) => railOnBand(s, currentBand()));
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return { open: state.open, toggle: () => setState(railToggle) };
}
