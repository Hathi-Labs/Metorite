/**
 * The touch menu of a chat row (task 3566). The runner is `environment: "node"`,
 * so the list renders to a string and the menu items are read as data.
 * `sessionPickerStrip.test.ts` holds the markup fence.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatSession } from "@/lib/sessions";

vi.mock("@/hooks/useActiveSessions", () => ({
  useActiveSessions: () => new Set<string>(),
  useUnreadIds: () => new Set<string>(),
  useNeedsInputIds: () => new Set<string>(),
}));

import { SessionPicker, sessionRowMenuItems } from "./SessionPicker";

const chat = (over: Partial<ChatSession> = {}): ChatSession => ({
  id: "c1",
  name: "c1",
  agentName: "orchestrator",
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  messageCount: 2,
  title: "Plan",
  ...over,
});

const labels = (items: ReturnType<typeof sessionRowMenuItems>) =>
  items.flatMap((i) => (i.kind === "item" ? [i.label] : []));
const noop = () => {};

afterEach(() => vi.unstubAllGlobals());

describe("sessionRowMenuItems", () => {
  // Mutation: drop a prop guard and the act shows without its prop. Fails here.
  it("holds exactly the acts the row's props give, in the strip's order", () => {
    const items = sessionRowMenuItems({ session: chat(), title: "Plan", onRenameStart: noop, onPin: noop, onArchive: noop, onRemove: noop });
    expect(labels(items)).toEqual(["Rename", "Pin to the top", "Archive", "Delete for good"]);
    expect(labels(sessionRowMenuItems({ session: chat(), title: "Plan", onRenameStart: noop }))).toEqual(["Rename"]);
  });
  it("offers Unpin for a pinned chat", () => {
    const s = chat({ pinnedAt: new Date().toISOString() });
    expect(labels(sessionRowMenuItems({ session: s, title: "Plan", onRenameStart: noop, onPin: noop }))).toEqual(["Rename", "Unpin"]);
  });
  // Mutation: remove the isOwner guard and a guest sees Rename. Fails here.
  it("shows no Rename to a guest of a room", () => {
    const items = sessionRowMenuItems({ session: chat({ isOwner: false }), title: "Plan", onRenameStart: noop, onPin: noop });
    expect(labels(items)).toEqual(["Pin to the top"]);
  });
  it("shows Restore and Delete on an archived row", () => {
    const items = sessionRowMenuItems({ session: chat(), title: "Plan", onRenameStart: noop, onRestore: noop, onRemove: noop });
    expect(labels(items)).toEqual(["Rename", "Restore", "Delete for good"]);
  });
  // Mutation: call onRemove without the confirm and the cancelled case fails.
  it("keeps the confirm on Delete", () => {
    const removed: string[] = [];
    const items = sessionRowMenuItems({ session: chat(), title: "Plan", onRenameStart: noop, onRemove: () => removed.push("x") });
    const del = items.find((i) => i.kind === "item" && i.label === "Delete for good");
    if (!del || del.kind !== "item") throw new Error("no delete");
    vi.stubGlobal("window", { confirm: () => false });
    del.onSelect();
    expect(removed).toEqual([]);
    vi.stubGlobal("window", { confirm: () => true });
    del.onSelect();
    expect(removed).toEqual(["x"]);
  });
  it("runs Rename through onRenameStart", () => {
    let started = 0;
    const items = sessionRowMenuItems({ session: chat(), title: "Plan", onRenameStart: () => (started += 1) });
    (items[0] as { onSelect: () => void }).onSelect();
    expect(started).toBe(1);
  });
});

describe("the row in a list", () => {
  const draw = (over: Partial<ChatSession> = {}, props: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(SessionPicker, {
        sessions: [chat(over)],
        activeId: "",
        onSelect: noop,
        onRename: noop,
        onArchive: noop,
        onRemove: noop,
        variant: "column",
        ...props,
      }),
    );
  // Mutation: render the button only for some rows, or drop its label.
  it("draws the More button in the row, hidden until touch", () => {
    const html = draw();
    expect(html).toContain('aria-label="More actions for Plan"');
    expect(html).toContain('data-session-more=""');
    expect(html).toMatch(/data-session-more="" class="hidden [^"]*pointer-coarse:flex/);
    expect(html).toMatch(/rail-row-actions[^"]*pointer-coarse:hidden/);
  });
  it("draws the desktop strip with the same icon acts as before", () => {
    const html = draw();
    for (const l of ["Rename Plan", "Archive Plan"]) expect(html).toContain(`aria-label="${l}"`);
  });
  it("does not open the menu at rest, so no menu is in the DOM", () => {
    expect(draw()).not.toContain('role="menu"');
  });
});
