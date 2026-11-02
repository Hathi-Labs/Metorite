"use client";

/**
 * The guide runner (WS-OB GD-4, D167, `assistant_guide.md` §3 G1, G5, G7, G10,
 * §4.1, §4.3, §9 F4).
 *
 * ONE small external store holds the tour. It knows the guide, the step and
 * the phase, and nothing about the page. The host (`GuideHost.tsx`) reads it,
 * pushes the route of an `open` step, rings the anchor of a `point` step, and
 * moves the assistant to the phone strip. This file is pure on purpose, so
 * `guide.test.ts` drives each state with a fake storage and no DOM.
 *
 * ## Phases
 *
 * - `idle`: no tour. This is the state of every mount, until a member acts.
 * - `opening`: an `open` step waits for its route.
 * - `pointing`: the step is up. A `point` step rings its anchor. A `say` step
 *   (and a `fill` step until GD-8) shows its words only.
 * - `missing`: the anchor did not appear, or the path of an `open` step is not
 *   an app path. The words still show, and Next still works (G7).
 * - `ended`: the member ended it, or the last step passed.
 *
 * ## Starts (G1, F4)
 *
 * A guide starts only on a member act: `startGuide()` from a button, the link
 * `?guide=<id>` that the member follows, or a "Show me" chip that the member
 * taps. Nothing here starts one on mount. A reload resumes from
 * `sessionStorage`, which a member act wrote earlier. An id that is not in
 * the generated table is ignored, with a warning.
 */

import { useSyncExternalStore } from "react";

import { isInAppPath, inAppPathname } from "@/lib/inAppLink";
import { GENERATED_GUIDES, type Guide, type GuideStep } from "@/lib/shell/guides.generated";

/** The sessionStorage key. It holds `{ guideId, step }`. */
export const GUIDE_STORAGE_KEY = "metorite.guide.v1";
/** The window event that opens or closes the phone's Menu drawer: detail "open" or "close". */
export const GUIDE_MENU_EVENT = "cc-guide-menu";
/** The URL parameter a link carries. */
export const GUIDE_PARAM = "guide";

export type GuidePhase = "idle" | "opening" | "pointing" | "missing" | "ended";
export type GuideSource = "button" | "url" | "agent" | "resume";
export type GuideEndReason = "done" | "escape" | "end";

export type GuideState = {
  phase: GuidePhase;
  guideId: string | null;
  /** 0-based. */
  step: number;
  total: number;
  source: GuideSource | null;
  reason: GuideEndReason | null;
  /** The path the host must push, once. Null when nothing waits. */
  push: string | null;
  /** A "Show me" chip that waits for a tap (a run named a guide where it may not move the page). */
  offer: { guideId: string; title: string } | null;
};

export const IDLE: GuideState = {
  phase: "idle",
  guideId: null,
  step: 0,
  total: 0,
  source: null,
  reason: null,
  push: null,
  offer: null,
};

export const ANCHOR_MISSING_NOTE = "I can't point at it on this screen.";

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type GuideRunnerOptions = {
  guides: readonly Guide[];
  /** The tab's storage. Undefined on the server. */
  storage: () => Store | undefined;
  warn: (message: string) => void;
  /** The telemetry line (`guide_anchor_missing`). */
  log: (event: string, data: Record<string, unknown>) => void;
  isPath: (href: string) => boolean;
};

/** A stored value, or null when it is broken or names no guide. */
export function parseStored(raw: string | null, guides: readonly Guide[]): { guideId: string; step: number } | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object") return null;
  const { guideId, step } = v as { guideId?: unknown; step?: unknown };
  if (typeof guideId !== "string" || typeof step !== "number" || !Number.isInteger(step)) return null;
  const guide = guides.find((g) => g.id === guideId);
  if (!guide || step < 0 || step >= guide.steps.length) return null;
  return { guideId, step };
}

/** The guide id in a query string, or null. */
export function guideFromSearch(search: string): string | null {
  const id = new URLSearchParams(search).get(GUIDE_PARAM);
  return id && id.trim() ? id.trim() : null;
}

/** The URL without the guide parameter, so a reload resumes and does not restart. */
export function withoutGuideParam(pathname: string, search: string, hash: string): string {
  const p = new URLSearchParams(search);
  p.delete(GUIDE_PARAM);
  const q = p.toString();
  return `${pathname}${q ? `?${q}` : ""}${hash}`;
}

/** Whether a step wants a ring on the page. */
export function stepAnchor(step: GuideStep | undefined): string | null {
  return step && step.kind === "point" ? step.anchor : null;
}

/** Whether a phase has a tour on screen. */
export function isActive(s: Pick<GuideState, "phase">): boolean {
  return s.phase === "opening" || s.phase === "pointing" || s.phase === "missing";
}

export type GuideView = {
  guide: Guide;
  step: GuideStep;
  say: string;
  /** 1-based, with the total: "2 of 5". */
  index: number;
  total: number;
  isLast: boolean;
  anchor: string | null;
  missing: boolean;
};

export function createGuideRunner(opts: GuideRunnerOptions) {
  let state: GuideState = IDLE;
  const listeners = new Set<() => void>();
  let here: string | null = null;

  const guideOf = (id: string | null): Guide | undefined => (id ? opts.guides.find((g) => g.id === id) : undefined);
  const set = (next: GuideState) => {
    state = next;
    persist();
    for (const l of [...listeners]) l();
  };
  const persist = () => {
    const store = opts.storage();
    if (!store) return;
    try {
      if (isActive(state) && state.guideId) {
        store.setItem(GUIDE_STORAGE_KEY, JSON.stringify({ guideId: state.guideId, step: state.step }));
      } else {
        store.removeItem(GUIDE_STORAGE_KEY);
      }
    } catch {
      // A full or blocked storage loses the resume, never the tour.
    }
  };

  /** Enter step `i`. An `open` step pushes its route, only when the path is an app path (the audit's rule 4). */
  const enter = (guide: Guide, i: number, source: GuideSource | null, keep: Partial<GuideState> = {}): void => {
    const step = guide.steps[i];
    const base: GuideState = {
      ...state,
      ...keep,
      guideId: guide.id,
      step: i,
      total: guide.steps.length,
      source: source ?? state.source,
      reason: null,
      push: null,
      offer: null,
    };
    if (step.kind !== "open") return set({ ...base, phase: "pointing" });
    if (!opts.isPath(step.path)) {
      opts.log("guide_anchor_missing", { guide: guide.id, step: i, anchor: null, path: step.path });
      return set({ ...base, phase: "missing" });
    }
    // Already there: nothing to push.
    if (here !== null && inAppPathname(step.path) === here) return set({ ...base, phase: "pointing" });
    set({ ...base, phase: "opening", push: step.path });
  };

  const api = {
    get: (): GuideState => state,
    subscribe(l: () => void): () => void {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },

    /** A member act starts a guide. Returns false for an id that is not in the table. */
    start(id: string, source: GuideSource): boolean {
      const guide = guideOf(id);
      if (!guide || guide.steps.length === 0) {
        opts.warn(`[guide] unknown guide id ignored: ${JSON.stringify(id)}`);
        return false;
      }
      enter(guide, 0, source);
      return true;
    },

    /** Read the tab's storage after a reload. It starts nothing without a stored mark. */
    resume(): boolean {
      if (isActive(state)) return false;
      const store = opts.storage();
      if (!store) return false;
      let raw: string | null = null;
      try {
        raw = store.getItem(GUIDE_STORAGE_KEY);
      } catch {
        return false;
      }
      if (raw === null) return false;
      const stored = parseStored(raw, opts.guides);
      if (!stored) {
        try {
          store.removeItem(GUIDE_STORAGE_KEY);
        } catch {
          /* ignore */
        }
        return false;
      }
      const guide = guideOf(stored.guideId)!;
      // The route of an `open` step was pushed before the reload: do not push again.
      set({
        ...IDLE,
        phase: "pointing",
        guideId: guide.id,
        step: stored.step,
        total: guide.steps.length,
        source: "resume",
      });
      return true;
    },

    next(): void {
      const guide = guideOf(state.guideId);
      if (!guide || !isActive(state)) return;
      if (state.step >= guide.steps.length - 1) return api.end("done");
      enter(guide, state.step + 1, null);
    },

    back(): void {
      const guide = guideOf(state.guideId);
      if (!guide || !isActive(state) || state.step === 0) return;
      enter(guide, state.step - 1, null);
    },

    /** The ring leaves in this call. The page stays where it is. Storage is cleared. */
    end(reason: GuideEndReason): void {
      if (!isActive(state)) return;
      set({ ...IDLE, phase: "ended", reason, guideId: state.guideId, total: state.total, step: state.step });
    },

    /** The host pushed the route: it asks for it once. */
    pushed(): void {
      if (state.push === null) return;
      state = { ...state, push: null };
      for (const l of [...listeners]) l();
    },

    /** The anchor of the current `point` step did not appear in time (G7). Never silent. */
    anchorMissing(anchor: string): void {
      const guide = guideOf(state.guideId);
      if (!guide || state.phase !== "pointing" || stepAnchor(guide.steps[state.step]) !== anchor) return;
      // GD-14: the telemetry sink. Until it lands, the line goes to the console.
      opts.log("guide_anchor_missing", { guide: guide.id, step: state.step, anchor });
      set({ ...state, phase: "missing" });
    },

    /** The route changed. It ends an `opening` wait, and follows a member who opens the next step's page. */
    routeChanged(pathname: string): void {
      here = pathname;
      const guide = guideOf(state.guideId);
      if (!guide || !isActive(state)) return;
      const cur = guide.steps[state.step];
      if (state.phase === "opening" && cur.kind === "open" && inAppPathname(cur.path) === pathname) {
        return set({ ...state, phase: "pointing", push: null });
      }
      const nxt = guide.steps[state.step + 1];
      if (nxt && nxt.kind === "open" && state.phase !== "opening" && inAppPathname(nxt.path) === pathname) {
        // The member is already there, so `enter` pushes nothing.
        enter(guide, state.step + 1, null);
      }
    },

    /** A run named a guide where it may not move the page: show the chip. */
    offer(id: string): boolean {
      const guide = guideOf(id);
      if (!guide) {
        opts.warn(`[guide] unknown guide id ignored: ${JSON.stringify(id)}`);
        return false;
      }
      if (isActive(state)) return false;
      set({ ...state, offer: { guideId: guide.id, title: guide.title } });
      return true;
    },
    /** The member tapped "Show me". */
    acceptOffer(): void {
      const o = state.offer;
      if (o) api.start(o.guideId, "agent");
    },
    dismissOffer(): void {
      if (state.offer) set({ ...state, offer: null });
    },

    view(): GuideView | null {
      const guide = guideOf(state.guideId);
      if (!guide || !isActive(state)) return null;
      const step = guide.steps[state.step];
      if (!step) return null;
      return {
        guide,
        step,
        say: step.say,
        index: state.step + 1,
        total: guide.steps.length,
        isLast: state.step === guide.steps.length - 1,
        anchor: stepAnchor(step),
        missing: state.phase === "missing",
      };
    },
  };
  return api;
}

export type GuideRunner = ReturnType<typeof createGuideRunner>;

// ------------------------------------------------------------- singleton ---

const runner: GuideRunner = createGuideRunner({
  guides: GENERATED_GUIDES,
  storage: () => (typeof window === "undefined" ? undefined : window.sessionStorage),
  warn: (m) => console.warn(m),
  // GD-14: the telemetry sink replaces this line.
  log: (event, data) => console.info(`[guide] ${event}`, data),
  isPath: isInAppPath,
});

export const startGuide = (id: string, source: GuideSource = "button") => runner.start(id, source);
export const resumeGuide = () => runner.resume();
export const nextStep = () => runner.next();
export const backStep = () => runner.back();
export const endGuide = (reason: GuideEndReason = "end") => runner.end(reason);
export const guideAnchorMissing = (anchor: string) => runner.anchorMissing(anchor);
export const guideRouteChanged = (pathname: string) => runner.routeChanged(pathname);
export const guidePushed = () => runner.pushed();
export const offerGuide = (id: string) => runner.offer(id);
export const acceptGuideOffer = () => runner.acceptOffer();
export const dismissGuideOffer = () => runner.dismissOffer();
export const guideView = () => runner.view();
export const guideState = () => runner.get();
export const __guideRunner = runner;

/** The tour, live. */
export function useGuide(): GuideState {
  return useSyncExternalStore(runner.subscribe, runner.get, () => IDLE);
}
