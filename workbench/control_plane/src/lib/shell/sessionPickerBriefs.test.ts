/**
 * The daily briefs fold into one closed group at the end of the chat list
 * (task 3564, owner 2026-11-16).
 *
 * R7 fence. Part 1 is the pure rule (`isFoldedBrief`, `foldBriefs`). Part 2
 * renders the list to a string: the group sits after the day groups and
 * before Archived, it is closed with a count, and the chat on screen opens it.
 * The source of the marker is the server (`ChatSession.isBrief`), never the title.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ChatSession } from "@/lib/sessions";

vi.mock("@/hooks/useActiveSessions", () => ({
  useActiveSessions: () => new Set<string>(),
  useUnreadIds: () => new Set<string>(),
}));

import { readFileSync } from "node:fs";
import { BRIEFS_GROUP, type BriefsOpenState, foldBriefs, isFoldedBrief, nextBriefsOpen } from "./chatHistory";
import { SessionPicker } from "./SessionPicker";

const NOW = Date.now();
const iso = (ms: number) => new Date(NOW - ms).toISOString();

function chat(id: string, over: Partial<ChatSession> = {}): ChatSession {
  return {
    id,
    name: id,
    agentName: "orchestrator",
    createdAt: iso(0),
    updatedAt: iso(60_000),
    messageCount: 2,
    title: `Chat ${id}`,
    ...over,
  };
}
const brief = (id: string, over: Partial<ChatSession> = {}) =>
  chat(id, { isBrief: true, hasUserMessage: false, isOwner: true, title: `Your day, ${id}`, ...over });

describe("which chats fold into Daily briefs", () => {
  const cases: [string, Partial<ChatSession>, boolean][] = [
    ["owner, brief, nobody replied", {}, true],
    ["the member replied", { hasUserMessage: true }, false],
    ["a guest of the room (not the owner)", { isOwner: false }, false],
    ["isOwner is not sent", { isOwner: undefined }, false],
    ["isBrief false", { isBrief: false }, false],
    ["isBrief is not sent", { isBrief: undefined }, false],
    ["hasUserMessage is not sent", { hasUserMessage: undefined }, false],
    ["archived", { archivedAt: iso(1000) }, false],
    ["pinned", { pinnedAt: iso(1000) }, false],
  ];
  for (const [name, over, want] of cases) {
    it(`${name}: ${want ? "folds" : "stays"}`, () => {
      expect(isFoldedBrief(brief("b", over))).toBe(want);
    });
  }

  it("a chat that the member titled 'Your day, ...' is not a brief", () => {
    const typed = chat("t", { title: "Your day, plan", isBrief: false, hasUserMessage: true, isOwner: true });
    expect(isFoldedBrief(typed)).toBe(false);
    const untyped = chat("u", { title: "Your day, plan", isOwner: true, hasUserMessage: false });
    expect(isFoldedBrief(untyped)).toBe(false);
  });

  it("splits the open list and keeps the order of each side", () => {
    const f = foldBriefs([chat("a"), brief("b1"), chat("c"), brief("b2", { hasUserMessage: true }), brief("b3")]);
    expect(f.main.map((s) => s.id)).toEqual(["a", "c", "b2"]);
    expect(f.briefs.map((s) => s.id)).toEqual(["b1", "b3"]);
  });
});

function draw(sessions: ChatSession[], activeId = ""): string {
  return renderToStaticMarkup(
    createElement(SessionPicker, {
      sessions,
      activeId,
      onSelect: () => {},
      onRename: () => {},
      onArchive: () => {},
      onRemove: () => {},
      variant: "column",
    }),
  );
}
const groups = (html: string) => [...html.matchAll(/data-chat-group="([^"]+)"/g)].map((m) => m[1]);

describe("the Daily briefs group in the list", () => {
  const SESSIONS = [
    chat("plain"),
    brief("b1"),
    brief("b2", { updatedAt: iso(120_000) }),
    brief("replied", { hasUserMessage: true, updatedAt: iso(90_000) }),
    chat("old", { archivedAt: iso(1000) }),
  ];

  it("sits after the day groups and before Archived, closed, with its count", () => {
    const html = draw(SESSIONS);
    expect(groups(html)).toEqual(["Today", BRIEFS_GROUP]);
    expect(html.indexOf(BRIEFS_GROUP)).toBeGreaterThan(html.indexOf("Chat plain"));
    expect(html.indexOf(BRIEFS_GROUP)).toBeLessThan(html.indexOf("Archived chats"));
    const head = html.slice(html.indexOf(`data-chat-group="${BRIEFS_GROUP}"`));
    expect(head).toContain('aria-expanded="false"');
    expect(head).toMatch(/Daily briefs<\/span><span[^>]*>2<\/span>/);
    expect(html).not.toContain('data-session-row="b1"');
    expect(html).not.toContain('data-session-row="b2"');
  });

  it("a replied brief stays in the day groups", () => {
    expect(draw(SESSIONS)).toContain('data-session-row="replied"');
  });

  it("the chat on screen opens the group, and its rows show", () => {
    const html = draw(SESSIONS, "b2");
    const head = html.slice(html.indexOf(`data-chat-group="${BRIEFS_GROUP}"`));
    expect(head).toContain('aria-expanded="true"');
    expect(html).toContain('data-session-row="b1"');
    expect(html).toContain('data-session-row="b2"');
  });

  it("a chat on screen outside the group leaves it closed", () => {
    expect(draw(SESSIONS, "plain")).not.toContain('data-session-row="b1"');
  });

  it("no folded chat, no group", () => {
    const html = draw([chat("plain"), brief("r", { hasUserMessage: true })]);
    expect(html).not.toContain(BRIEFS_GROUP);
  });

  it("a list of only briefs draws the group, not the empty line", () => {
    const html = draw([brief("b1")]);
    expect(html).toContain(BRIEFS_GROUP);
    expect(html).not.toContain("Your chats will show here.");
  });
});

describe("when the Daily briefs group opens by itself", () => {
  const shut: BriefsOpenState = { open: false, handled: null };
  const step = (st: BriefsOpenState, active: string, ids: string[]) => nextBriefsOpen(st, active, ids);

  it("opens at mount when the active chat is a brief", () => {
    expect(step(shut, "b1", ["b1", "b2"])).toEqual({ open: true, handled: "b1" });
  });
  it("opens when the list arrives AFTER the picker mounted on a brief", () => {
    const empty = step(shut, "b1", []);
    expect(empty.open).toBe(false);
    expect(step(empty, "b1", ["b1"])).toEqual({ open: true, handled: "b1" });
  });
  it("opens when the member moves into a brief", () => {
    const on = step(shut, "plain", ["b1"]);
    expect(on.open).toBe(false);
    expect(step(on, "b1", ["b1"]).open).toBe(true);
  });
  it("stays as it is when the active chat is outside the group", () => {
    const open = { open: true, handled: null };
    expect(step(open, "plain", ["b1"])).toBe(open);
    expect(step(shut, "plain", ["b1"])).toBe(shut);
  });
  it("keeps it closed after the member closed it, for the same active chat", () => {
    const opened = step(shut, "b1", ["b1"]);
    const closed = { ...opened, open: false };
    expect(step(closed, "b1", ["b1", "b2"])).toBe(closed);
  });
  it("opens again for a NEW active brief, and for a return after a chat outside", () => {
    const closed: BriefsOpenState = { open: false, handled: "b1" };
    expect(step(closed, "b2", ["b1", "b2"])).toEqual({ open: true, handled: "b2" });
    const away = step(closed, "plain", ["b1", "b2"]);
    expect(away.handled).toBeNull();
    expect(step(away, "b1", ["b1", "b2"]).open).toBe(true);
  });
});

describe("the picker wires the open rule and names the list it controls", () => {
  const src = readFileSync(new URL("./SessionPicker.tsx", import.meta.url), "utf-8").split(String.fromCharCode(13)).join("");
  it("derives the open state from nextBriefsOpen on every render", () => {
    expect(src).toMatch(/nextBriefsOpen\(briefsState, activeId, folded\.briefs\.map/);
    expect(src).toMatch(/if \(nextBriefs !== briefsState\) setBriefsState\(nextBriefs\)/);
  });
  it("gives the toggle aria-controls and the list the same id", () => {
    expect(src).toMatch(/aria-controls=\{briefsListId\}/);
    expect(src).toMatch(/<ul id=\{briefsListId\}/);
  });
});
