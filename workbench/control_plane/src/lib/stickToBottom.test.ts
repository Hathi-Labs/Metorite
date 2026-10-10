/**
 * An approval card that arrives while the member reads the bottom of the
 * transcript draws in view, with no jump and no pin. One that arrives while
 * the member has scrolled up moves nothing, and the pin offers "Show"
 * (owner request, 2026-10-10).
 *
 * The defect, measured in a browser before the fix: at the bottom (distance
 * 0), the approval card drew below the fold (its top 34 px past the bottom
 * of a 638 px viewport), the transcript did not move, and the pin showed.
 * Two causes, and each has a case below:
 *
 * - the chat followed the bottom only on a new MESSAGE, and an approval card
 *   comes from the confirmation queue;
 * - the late scroll event of our own snap read as "the member scrolled away".
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `attachStickToBottom` stops setting `scrollTop` -> "at the bottom: the
 *   transcript stays pinned, and the pin does not show" (run 2026-10-10);
 * - `nearAfterScroll` trusts every scroll event -> "the late event of our
 *   own snap keeps the member at the bottom" (run 2026-10-10: red);
 * - `AgentChat` stops attaching it -> "the chat attaches it", and in the
 *   browser `e2e/chat-card-rollup.spec.ts` (run 2026-10-10: red, the pin
 *   drew in all 62 samples while the card sat 166 px below the fold).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AskPin from "@/components/AskPin";
import type { PendingAsk } from "@/lib/askPin";
import {
  MANUAL_TOGGLE_QUIET_MS,
  attachStickToBottom,
  distanceFromBottom,
  nearAfterScroll,
} from "@/lib/stickToBottom";

/** A scroll box and a ResizeObserver the test drives by hand. */
function rig(opts: { scrollTop: number; nearBottom: boolean; lastToggle?: number; now?: number }) {
  // A browser clamps `scrollTop` to the range the content allows.
  let top = opts.scrollTop;
  const box = {
    scrollHeight: 1400,
    clientHeight: 676,
    get scrollTop() {
      return top;
    },
    set scrollTop(v: number) {
      top = Math.max(0, Math.min(v, box.scrollHeight - box.clientHeight));
    },
  };
  const thread = box as unknown as HTMLElement;
  let near = opts.nearBottom;
  let fire: () => void = () => {};
  const observed: unknown[] = [];
  class FakeObserver {
    constructor(cb: () => void) {
      fire = cb;
    }
    observe(el: unknown) {
      observed.push(el);
    }
    disconnect() {}
  }
  const content = {} as Element;
  const stop = attachStickToBottom(thread, content, {
    getNearBottom: () => near,
    setNearBottom: (v) => {
      near = v;
    },
    lastManualToggleAt: () => opts.lastToggle ?? Number.NEGATIVE_INFINITY,
    now: () => opts.now ?? 10_000,
    Observer: FakeObserver,
  });
  return {
    thread,
    observed,
    near: () => near,
    stop,
    /** An approval card of `h` px lands at the end of the content. */
    cardArrives(h: number) {
      const top = (thread as unknown as { scrollHeight: number }).scrollHeight;
      (thread as unknown as { scrollHeight: number }).scrollHeight += h;
      fire();
      return { top, bottom: top + h };
    },
  };
}

/** Is the card wholly inside the viewport of the scroll box? */
function inView(thread: HTMLElement, card: { top: number; bottom: number }): boolean {
  return card.top >= thread.scrollTop && card.bottom <= thread.scrollTop + thread.clientHeight;
}

const ASK: PendingAsk = { kind: "confirm", title: "Create 5 tasks in MCP & External", count: 1, target: "hitl" };

function pinHtml(cardInView: boolean): string {
  return renderToStaticMarkup(
    createElement(AskPin, { ask: ASK, threadRef: createRef<HTMLElement>(), initialInView: cardInView }),
  );
}

describe("at the bottom: the transcript stays pinned, and the pin does not show", () => {
  it("an approval card lands in view, and the bar draws nothing", () => {
    const r = rig({ scrollTop: 724, nearBottom: true }); // 1400 - 676: the bottom
    expect(distanceFromBottom(r.thread)).toBe(0);
    const card = r.cardArrives(117);
    expect(distanceFromBottom(r.thread)).toBeLessThanOrEqual(0);
    expect(inView(r.thread, card)).toBe(true);
    expect(pinHtml(inView(r.thread, card))).toBe("");
  });

  it("watches both the viewport and the content", () => {
    const r = rig({ scrollTop: 724, nearBottom: true });
    expect(r.observed).toHaveLength(2);
  });

  it("the late event of our own snap keeps the member at the bottom", () => {
    // Measured: the snap set scrollTop 543, a card then grew the content to
    // 1473, and the scroll event arrived after that layout.
    expect(nearAfterScroll(true, 543, { scrollTop: 543, scrollHeight: 1473, clientHeight: 676 })).toBe(true);
    // A real scroll up still leaves the bottom.
    expect(nearAfterScroll(true, 543, { scrollTop: 200, scrollHeight: 1473, clientHeight: 676 })).toBe(false);
    // A scroll down to the bottom comes back to it.
    expect(nearAfterScroll(false, 200, { scrollTop: 797, scrollHeight: 1473, clientHeight: 676 })).toBe(true);
  });
});

describe("scrolled up: the pin shows and no scroll happens", () => {
  it("the transcript keeps its place, and the bar names the card", () => {
    const r = rig({ scrollTop: 0, nearBottom: false });
    const card = r.cardArrives(117);
    expect(r.thread.scrollTop).toBe(0);
    expect(inView(r.thread, card)).toBe(false);
    const html = pinHtml(inView(r.thread, card));
    expect(html).toContain("data-ask-pin");
    expect(html).toContain("Waiting for your approval");
    expect(html).toContain("Show");
  });
});

describe("a fold by hand keeps its place", () => {
  it("does not snap to the bottom, and learns where the member now is", () => {
    const r = rig({ scrollTop: 724, nearBottom: true, lastToggle: 9_800, now: 10_000 });
    expect(10_000 - 9_800).toBeLessThan(MANUAL_TOGGLE_QUIET_MS);
    r.cardArrives(400); // the opened card grows under the member
    expect(r.thread.scrollTop).toBe(724);
    expect(r.near()).toBe(false);
  });
});

describe("the chat attaches it", () => {
  it("AgentChat follows the bottom on a change of size, and trusts only a scroll up", () => {
    const src = readFileSync(fileURLToPath(new URL("../components/AgentChat.tsx", import.meta.url)), "utf8");
    expect(src).toContain("attachStickToBottom(thread, content,");
    expect(src).toContain("nearAfterScroll(isNearBottomRef.current, lastTop, el)");
    expect(src).toMatch(/<div ref=\{contentRef\}/);
    // A fold by hand can leave the bottom with no scroll event, so the
    // observer keeps the scroll-to-bottom button in step (review round 1).
    expect(src).toMatch(/setNearBottom: \(near\) => \{[\s\S]{0,240}setShowScrollBtn\(/);
  });
});
