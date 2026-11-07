/**
 * WT-6a (one_assistant_context.md §9.4): the Undo of an archive that a chat
 * card approved. The command runs the unarchive route with `If-Match`, and a
 * 412 means the row changed since. The receipt line offers the button.
 *
 * Each case names the mutation that turns it red (R7).
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import ConfirmationReceipts from "@/components/ConfirmationReceipts";
import {
  STALE_MESSAGE,
  archiveUndoEntry,
  archiveUndoFromEvent,
  browserArchiveIo,
  runArchiveUndo,
  unarchivePath,
  type ArchiveIo,
  type ArchiveUndo,
  type ArchiveUndoState,
} from "@/lib/archiveUndo";
import {
  EMPTY_CONFIRMATIONS,
  confirmationReducer,
  receiptText,
  settleAnswer,
  type ConfirmationAction,
  type ConfirmationQueueState,
  type PendingConfirmation,
} from "@/lib/confirmationQueue";

const TID = "11111111-1111-4111-8111-111111111111";
const STAMP = "2026-11-07T10:00:00.123456+00:00";
const RID = "a".repeat(32);
const undo: ArchiveUndo = { kind: "task", id: TID, version: STAMP };

const failing = (status?: number): ArchiveIo => ({
  unarchive: async () => {
    throw Object.assign(new Error("x"), status === undefined ? {} : { status });
  },
});

describe("archiveUndoFromEvent", () => {
  it("takes a request id, a kind, a UUID and a stamp with an offset", () => {
    expect(archiveUndoFromEvent({ request_id: RID, kind: "task", id: TID.toUpperCase(), version: STAMP }))
      .toEqual({ requestId: RID, undo });
  });

  it.each([
    ["no request id", { kind: "task", id: TID, version: STAMP }],
    ["another kind", { request_id: RID, kind: "member", id: TID, version: STAMP }],
    ["a path in the id", { request_id: RID, kind: "task", id: "../members", version: STAMP }],
    ["no version", { request_id: RID, kind: "task", id: TID }],
    ["a stamp with no offset", { request_id: RID, kind: "task", id: TID, version: "2026-11-07T10:00:00" }],
    ["a token", { request_id: RID, kind: "task", id: TID, version: STAMP, token: "t" }],
  ])("refuses %s", (_name, value) => {
    // Mutation: accept any id -> "a path in the id" goes red. A token is
    // ignored, never carried: the result has no key for it.
    const got = archiveUndoFromEvent(value);
    if (_name === "a token") expect(got && Object.keys(got.undo)).toEqual(["kind", "id", "version"]);
    else expect(got).toBeNull();
  });
});

describe("runArchiveUndo", () => {
  it("sends the version as If-Match and reads done", async () => {
    const io: ArchiveIo = { unarchive: vi.fn(async () => ({})) };
    expect(await runArchiveUndo(io, undo)).toBe("done");
    expect(io.unarchive).toHaveBeenCalledWith("task", TID, STAMP);
  });

  it("a 412 reads stale, so the member learns it changed since", async () => {
    // Mutation: treat a 412 as a failure that may retry -> red.
    expect(await runArchiveUndo(failing(412), undo)).toBe("stale");
  });

  it("another 4xx stops the Undo, and a 5xx or a network fault may retry", async () => {
    expect(await runArchiveUndo(failing(404), undo)).toBe("refused");
    expect(await runArchiveUndo(failing(503), undo)).toBe("failed");
    expect(await runArchiveUndo(failing(), undo)).toBe("failed");
  });
});

describe("archiveUndoEntry", () => {
  it("is a lib/undo.ts command: one write for two clicks, then spent", async () => {
    let release!: () => void;
    const io: ArchiveIo = {
      unarchive: vi.fn(() => new Promise<void>((r) => (release = r))),
    };
    const states: ArchiveUndoState[] = [];
    const entry = archiveUndoEntry(io, undo, "archived #7", (s) => states.push(s));
    const a = entry.undo();
    const b = entry.undo();
    expect(a).toBe(b);
    release();
    await a;
    await entry.undo();
    expect(io.unarchive).toHaveBeenCalledTimes(1);
    expect(states).toEqual(["busy", "done"]);
    expect(entry.label).toBe("archived #7");
  });

  it("after a 412 the entry is dead and writes nothing more", async () => {
    // Mutation: leave the entry live after a 412 -> a second write -> red.
    const io = failing(412);
    const spy = vi.spyOn(io, "unarchive");
    const states: ArchiveUndoState[] = [];
    const entry = archiveUndoEntry(io, undo, "x", (s) => states.push(s));
    await entry.undo();
    await entry.undo();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(states).toEqual(["busy", "stale"]);
  });

  it("after a network fault the member may try again", async () => {
    const io = failing();
    const spy = vi.spyOn(io, "unarchive");
    const entry = archiveUndoEntry(io, undo, "x", () => undefined);
    await entry.undo();
    await entry.undo();
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("browserArchiveIo", () => {
  it("posts to the session proxy with If-Match and no other credential", async () => {
    const fetchFn = vi.fn(async () => new Response("{}", { status: 200 }));
    await browserArchiveIo(fetchFn as unknown as typeof fetch).unarchive("project", TID, STAMP);
    expect(fetchFn).toHaveBeenCalledWith(`/api/projects/nodes/${TID}/unarchive`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "If-Match": STAMP },
    });
    expect(unarchivePath("task", TID)).toBe(`/api/projects/tasks/${TID}/unarchive`);
  });

  it("a 412 throws with its status, so the command reads stale", async () => {
    const fetchFn = vi.fn(async () => new Response("{}", { status: 412 }));
    const io = browserArchiveIo(fetchFn as unknown as typeof fetch);
    expect(await runArchiveUndo(io, undo)).toBe("stale");
  });
});

// ── The receipt line ─────────────────────────────────────────────────────

const card: PendingConfirmation = {
  key: RID,
  title: "Archive this task?",
  detail: "#7 Fix the extruder · 1 task archived",
  requestId: RID,
};
const run = (actions: ConfirmationAction[], from: ConfirmationQueueState = EMPTY_CONFIRMATIONS) =>
  actions.reduce(confirmationReducer, from);
const approved = run([
  { type: "requested", value: { request_id: RID, title: card.title, detail: card.detail } },
  { type: "answering", key: RID, label: card.detail!, intent: "approved", afterId: "m1" },
  settleAnswer(card, "ok"),
]);
const offered = run([{ type: "undoOffered", requestId: RID, undo }], approved);
const draw = (s: ConfirmationQueueState, onUndoArchive?: (key: string) => void) =>
  renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: s.receipts, onUndoArchive }));

describe("the receipt of an approved archive", () => {
  it("offers Undo only after the server named its row", () => {
    // Mutation: draw Undo on every approved line -> red.
    expect(draw(approved, () => undefined)).not.toContain("data-confirmation-undo");
    expect(draw(offered, () => undefined)).toContain(`data-confirmation-undo="${RID}"`);
    expect(draw(offered)).not.toContain("data-confirmation-undo");
  });

  it("a request id of another chat attaches nothing", () => {
    // Mutation: attach to the newest line -> red.
    const elsewhere = run([{ type: "undoOffered", requestId: "b".repeat(32), undo }], approved);
    expect(elsewhere).toEqual(approved);
    expect(run([{ type: "reset" }], offered).receipts).toEqual([]);
  });

  it("a rejected card never offers it", () => {
    const rejected = run([
      { type: "requested", value: { request_id: RID, title: card.title, detail: card.detail } },
      { type: "answering", key: RID, label: card.detail!, intent: "rejected", afterId: "m1" },
      settleAnswer(card, "ok"),
      { type: "undoOffered", requestId: RID, undo },
    ]);
    expect(rejected.receipts[0].archive).toBeUndefined();
  });

  it("says what the Undo did, and a 412 says it changed since", () => {
    const at = (state: ArchiveUndoState) =>
      run([{ type: "undoState", key: RID, state }], offered).receipts.map(receiptText)[0];
    expect(at("busy")).toBe(`Restoring… ${card.detail}`);
    expect(at("done")).toBe(`Restored · ${card.detail}`);
    expect(at("stale")).toBe(`Approved · ${card.detail}. ${STALE_MESSAGE}`);
    const stale = run([{ type: "undoState", key: RID, state: "stale" }], offered);
    expect(draw(stale, () => undefined)).not.toContain("data-confirmation-undo");
    const failed = run([{ type: "undoState", key: RID, state: "failed" }], offered);
    expect(draw(failed, () => undefined)).toContain("data-confirmation-undo");
  });
});
