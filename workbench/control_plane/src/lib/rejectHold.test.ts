/**
 * Undo after Reject (#916). R7 fences:
 *   * `reject-hold-undo`: an Undo inside the window cancels the POST.
 *   * `reject-hold-sends`: after the window, and on flush, the REJECT goes.
 *   * `reject-hold-receipt`: the line reads "Rejected · … — Undo" and the
 *     card comes back with no error.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationReceipts from "@/components/ConfirmationReceipts";
import {
  EMPTY_CONFIRMATIONS,
  confirmationReducer,
  receiptText,
  type ConfirmationAction,
  type PendingConfirmation,
} from "@/lib/confirmationQueue";
import { REJECT_HOLD_MS, createRejectHold, type HoldTimers } from "@/lib/rejectHold";

/** Fake timers: `tick(ms)` runs what is due. */
function fakeTimers() {
  let now = 0;
  let next = 1;
  const jobs = new Map<number, { at: number; fn: () => void }>();
  const timers: HoldTimers = {
    set: (fn, ms) => { jobs.set(next, { at: now + ms, fn }); return next++; },
    clear: (h) => { jobs.delete(h as number); },
  };
  const tick = (ms: number) => {
    now += ms;
    for (const [id, j] of [...jobs]) if (j.at <= now) { jobs.delete(id); j.fn(); }
  };
  return { timers, tick, count: () => jobs.size };
}

describe("reject-hold-undo", () => {
  it("an Undo inside the window sends nothing", () => {
    const t = fakeTimers();
    const hold = createRejectHold(t.timers);
    const sent: string[] = [];
    hold.start("r1", () => sent.push("r1"));
    t.tick(REJECT_HOLD_MS - 1);
    expect(hold.undo("r1")).toBe(true);
    t.tick(REJECT_HOLD_MS * 2);
    expect(sent).toEqual([]);
    expect(t.count()).toBe(0);
  });

  it("an Undo after the POST went is refused", () => {
    const t = fakeTimers();
    const hold = createRejectHold(t.timers);
    hold.start("r1", () => {});
    t.tick(REJECT_HOLD_MS);
    expect(hold.undo("r1")).toBe(false);
  });
});

describe("reject-hold-sends", () => {
  it("the REJECT goes out when the window ends", () => {
    const t = fakeTimers();
    const hold = createRejectHold(t.timers);
    const sent: string[] = [];
    hold.start("r1", () => sent.push("r1"));
    t.tick(REJECT_HOLD_MS - 1);
    expect(sent).toEqual([]);
    t.tick(1);
    expect(sent).toEqual(["r1"]);
    expect(hold.has("r1")).toBe(false);
  });

  it("a session switch or an unmount sends every held REJECT at once", () => {
    const t = fakeTimers();
    const hold = createRejectHold(t.timers);
    const sent: string[] = [];
    hold.start("r1", () => sent.push("r1"));
    hold.start("r2", () => sent.push("r2"));
    hold.flushAll();
    expect(sent.sort()).toEqual(["r1", "r2"]);
    t.tick(REJECT_HOLD_MS * 2);
    expect(sent).toHaveLength(2); // no second send from a stale timer
  });

  it("a second hold on one key sends the first, never loses it", () => {
    const t = fakeTimers();
    const hold = createRejectHold(t.timers);
    const sent: string[] = [];
    hold.start("r1", () => sent.push("a"));
    hold.start("r1", () => sent.push("b"));
    expect(sent).toEqual(["a"]);
    t.tick(REJECT_HOLD_MS);
    expect(sent).toEqual(["a", "b"]);
  });

  it("AgentChat flushes on a session change and an unmount", () => {
    const src = readFileSync(fileURLToPath(new URL("../components/AgentChat.tsx", import.meta.url)), "utf-8");
    expect(src).toMatch(/useEffect\(\(\) => \(\) => \{ rejectHold\.flushAll\(\); \}, \[sessionId, rejectHold\]\)/);
  });
});

const card: PendingConfirmation = { key: "r1", title: "Confirm action", detail: "Delete task", requestId: "r1" };
const run = (actions: ConfirmationAction[]) => actions.reduce(confirmationReducer, EMPTY_CONFIRMATIONS);
const held: ConfirmationAction[] = [
  { type: "requested", value: { request_id: "r1", title: card.title, detail: card.detail } },
  { type: "answering", key: "r1", label: "Delete task", intent: "rejected", afterId: "m1", undo: true },
];

describe("reject-hold-receipt", () => {
  it("reads Rejected while held, and Rejecting once the POST goes", () => {
    const s = run(held);
    expect(s.receipts.map(receiptText)).toEqual(["Rejected · Delete task"]);
    const sent = run([...held, { type: "released", key: "r1" }]);
    expect(sent.receipts.map(receiptText)).toEqual(["Rejecting… Delete task"]);
    const done = run([...held, { type: "released", key: "r1" }, { type: "answered", key: "r1" }]);
    expect(done.receipts.map(receiptText)).toEqual(["Rejected · Delete task"]);
    expect(done.receipts[0].undo).toBeUndefined();
  });

  it("Undo puts the card back with no line and no error", () => {
    const s = run([...held, { type: "restore", card }]);
    expect(s.cards).toEqual([card]);
    expect(s.receipts).toEqual([]);
    expect(s.errors.size).toBe(0);
    expect(s.answering.size).toBe(0);
  });

  it("draws an Undo button for a held line, and none once sent", () => {
    const s = run(held);
    const html = renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: s.receipts, onUndo: () => {} }));
    expect(html).toContain("Rejected · Delete task");
    expect(html).toContain('data-confirmation-undo="r1"');
    expect(html).toContain("Undo");
    const sent = run([...held, { type: "released", key: "r1" }]);
    expect(renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: sent.receipts, onUndo: () => {} })))
      .not.toContain("data-confirmation-undo");
    expect(renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: s.receipts }))).not.toContain("Undo");
  });
});

describe("a closed tab never drops a held REJECT", () => {
  it("AgentChat flushes on pagehide, and the POST is keepalive", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const chat = readFileSync(fileURLToPath(new URL("../components/AgentChat.tsx", import.meta.url)), "utf-8");
    expect(chat).toMatch(/addEventListener\("pagehide", flush\)/);
    const post = readFileSync(fileURLToPath(new URL("./respondInput.ts", import.meta.url)), "utf-8");
    expect(post).toMatch(/keepalive: true/);
  });
});
