"use client";

/**
 * RunStatusRow — the one line that says what the run does (owner report,
 * 2026-10-19). It draws the run state of `lib/runStatus.ts` and decides
 * nothing of its own.
 *
 * Two places, one state: above the composer (`placement="composer"`), and in
 * the thread where the reply will appear (`placement="thread"`), while no
 * live bubble shows the run. While the run works, the row stays mounted and
 * only its words change, so a long tool call or a delegation never hides it.
 * A change shorter than `FLASH_MS` never shows or hides it
 * (`steadyPresence`).
 *
 * Fence (R7): `src/lib/runStatus.test.ts` (the state and the presence) and
 * `e2e/chat-run-status.spec.ts` (the row in a real browser).
 */

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import {
  FLASH_MS,
  runView,
  runViewText,
  steadyPresence,
  type RunStatus,
  type RunTone,
  type RunView,
} from "@/lib/runStatus";

const TONE_ICON: Record<RunTone, string> = {
  working: "LoaderCircle",
  finishing: "LoaderCircle",
  waiting: "MessageCircleQuestion",
  parked: "CirclePause",
  done: "CircleCheck",
  stopped: "CircleStop",
};

const TONE_CLASS: Record<RunTone, string> = {
  working: "text-primary",
  finishing: "text-muted-foreground",
  waiting: "text-warning",
  parked: "text-warning",
  done: "text-success",
  stopped: "text-muted-foreground",
};

/**
 * The view to draw now, with no flash: it appears only after the run has
 * wanted it for `FLASH_MS`, and goes only after it has wanted it gone for as
 * long. While it shows, the words follow the state at once. A run this tab
 * started (`starting`) shows at once: the member pressed Send.
 */
export function useSteadyRunView(run: RunStatus): RunView | null {
  const [now, setNow] = useState(() => Date.now());
  const target = runView(run, now);
  const want = !!target;
  const [shown, setShown] = useState(want);
  // The last words, so the row keeps them during its hide delay.
  const [last, setLast] = useState<RunView | null>(target);
  const key = target ? `${target.tone}|${runViewText(target)}` : "";

  // The clock: the elapsed time, and the end of "Done" after its time.
  const ticking = want || shown;
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(t);
  }, [ticking]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- keep the words for the hide delay
    if (target) setLast(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const immediate = run.phase === "starting";
  useEffect(() => {
    const r = steadyPresence(shown, want, 0, Date.now(), immediate);
    if (r.shown !== shown) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the member's own send shows at once
      setShown(r.shown);
      return;
    }
    if (!r.pendingSince) return;
    // The change must hold for FLASH_MS. A change back cancels this timer.
    const t = setTimeout(() => setShown(want), FLASH_MS);
    return () => clearTimeout(t);
  }, [want, shown, immediate]);

  if (!shown) return null;
  // While it shows, the words are the state's own.
  return target ?? last;
}

export default function RunStatusRow({
  view,
  placement,
}: {
  view: RunView | null;
  placement: "composer" | "thread";
}) {
  if (!view) return null;
  const text = runViewText(view);
  const spin = view.tone === "working" || view.tone === "finishing";
  return (
    <span
      role="status"
      aria-live="polite"
      data-run-status={placement}
      data-run-tone={view.tone}
      title={text}
      className={[
        "flex min-w-0 items-center gap-2 text-[11px]",
        placement === "thread" ? "py-1" : "",
      ].join(" ")}
    >
      <Icon
        name={TONE_ICON[view.tone]}
        size={12}
        strokeWidth={1.5}
        className={`shrink-0 ${TONE_CLASS[view.tone]} ${spin ? "motion-safe:animate-spin" : ""}`}
      />
      <span className={`min-w-0 truncate ${view.tone === "working" ? "text-foreground" : TONE_CLASS[view.tone]}`}>
        {view.label}
      </span>
      {view.elapsed && (
        <span className="shrink-0 tabular-nums text-muted-foreground">· {view.elapsed}</span>
      )}
    </span>
  );
}
