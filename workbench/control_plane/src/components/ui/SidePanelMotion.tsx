"use client";

/**
 * The frame of a docked side panel: THE one motion of a panel that opens at
 * the right of a page (task 7, owner answer 2026-11-22: "one shared motion
 * for all right-side panels").
 *
 * **The reference is the assistant's side panel** (`lib/shell/AssistantDock.tsx`),
 * which mirrors the left sidebar's fold. This frame takes the same dock form
 * on the same two tokens, `--panel-duration` and `--panel-easing`:
 *
 * - The frame eases its width from 0 to the panel's width, so the page beside
 *   it reflows with it, as it does for the assistant's spacer.
 * - The panel inside keeps its own width and rides the frame's left edge. So
 *   its content never reflows while it moves, and it slides in from the right
 *   as the assistant does. The frame clips it while it moves.
 * - A close is the same motion back. The panel stays on screen, inert, until
 *   the motion ends (`useSidePanelPresence`), and then it unmounts.
 * - Reduced motion: the panel shows and goes at once.
 *
 * The CSS is `.side-panel` in `globals.css`. `panelMotion.test.ts` fences it.
 *
 * **Which panel is docked.** A host whose content is always a docked column
 * passes `docked` (the Projects task panel). A record card is a column only
 * at its side stop, and `CardPanel` marks its host then (`SIDE_DOCKED_ATTR`).
 * A card that stands over the page (a full card, a phone screen) leaves the
 * frame with no box: `display: contents`. It has no motion and no wait.
 *
 * Use it at the mount site, round the host's `cond ? <Panel/> : null`:
 *
 *     <SidePanelMotion docked>{taskPanel}</SidePanelMotion>
 */

import { type ReactNode, useLayoutEffect, useRef } from "react";

import { SIDE_DOCKED_ATTR, isMoving, isShown, settlesAtOnce, useSidePanelPresence } from "@/lib/panelMotion";

export default function SidePanelMotion({
  docked = false,
  children,
}: {
  /** The content is always a docked column. Else the content marks itself. */
  docked?: boolean;
  /** The panel while it is open. Null or false closes it. */
  children?: ReactNode;
}) {
  const { content, phase, settle } = useSidePanelPresence(children);
  const frameRef = useRef<HTMLDivElement>(null);
  const moving = isMoving(phase);

  // A frame with no box (a card over the page) has nothing to move. Its close
  // settles at once, so a full card leaves with no wait. An open needs no such
  // step: nothing on screen waits for it, and a card that loads its code late
  // (My Day's) can still dock while the frame opens.
  useLayoutEffect(() => {
    if (phase !== "closing") return;
    const frame = frameRef.current;
    if (settlesAtOnce(frame ? getComputedStyle(frame).display : null)) settle();
  }, [phase, settle]);

  if (content === null) return null;
  return (
    <div
      ref={frameRef}
      className="side-panel"
      data-panel-phase={phase}
      data-shown={isShown(phase) ? "" : undefined}
      data-moving={moving ? "" : undefined}
      // A panel on its way out takes no input, and it gives the focus back.
      inert={phase === "closing"}
      onTransitionEnd={(e) => {
        if (e.target !== e.currentTarget || e.propertyName !== "width") return;
        settle();
        // The page grows or shrinks with no window `resize`, as for the
        // assistant's spacer. Tell the layouts that measure on it.
        window.dispatchEvent(new Event("resize"));
      }}
    >
      <div data-side-panel-body="" {...(docked ? { [SIDE_DOCKED_ATTR]: "" } : {})}>
        {content}
      </div>
    </div>
  );
}
