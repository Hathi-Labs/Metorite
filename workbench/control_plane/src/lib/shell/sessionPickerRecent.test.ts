/**
 * Recent, the topic groups and the Daily briefs fold side by side
 * (CO-2c, task 3647, and task 3564).
 *
 * R7 fence. The picker renders to a string with the topics switch on. The
 * folded Daily briefs sit in their own closed group at the end. They are not
 * in Recent and not in a topic group. Recent holds the five newest chats that
 * are not folded.
 *
 * Mutation: feed `pickerGroups` the list `lists.open` (with the briefs) in
 * place of `folded.main`, and the briefs show in Recent and fail these tests.
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
import { BRIEFS_GROUP, RECENT_GROUP } from "./chatHistory";
import { SessionPicker } from "./SessionPicker";

const NOW = Date.now();
const iso = (ms: number) => new Date(NOW - ms).toISOString();

function chat(id: string, ageMs: number, over: Partial<ChatSession> = {}): ChatSession {
  return {
    id,
    name: id,
    agentName: "orchestrator",
    createdAt: iso(ageMs),
    updatedAt: iso(ageMs),
    messageCount: 2,
    title: `Chat ${id}`,
    hasUserMessage: true,
    isOwner: true,
    ...over,
  };
}
// A daily brief: owner, no reply, and newer than every other chat.
const brief = (id: string, ageMs: number) =>
  chat(id, ageMs, { isBrief: true, hasUserMessage: false, title: `Your day, ${id}` });

function render(sessions: ChatSession[], activeId = ""): string {
  return renderToStaticMarkup(
    createElement(SessionPicker, {
      sessions,
      activeId,
      onSelect: () => {},
      onRename: () => {},
      onArchive: () => {},
      onRemove: () => {},
      variant: "column",
      topicsOn: true,
    }),
  );
}
const groups = (html: string) => [...html.matchAll(/data-chat-group="([^"]+)"/g)].map((m) => m[1]);
/** The row ids inside one group, in order. */
function rowsOf(html: string, label: string): string[] {
  const start = html.indexOf(`data-chat-group="${label}"`);
  expect(start).toBeGreaterThan(-1);
  const rest = html.slice(start);
  const end = rest.indexOf("</section>");
  const part = end === -1 ? rest : rest.slice(0, end);
  return [...part.matchAll(/data-session-row="([^"]+)"/g)].map((m) => m[1]);
}

const MAIN = [
  chat("c1", 10 * 60_000),
  chat("c2", 20 * 60_000),
  chat("c3", 30 * 60_000),
  chat("c4", 40 * 60_000),
  chat("c5", 50 * 60_000),
  chat("c6", 60 * 60_000),
  chat("c7", 70 * 60_000),
];
const BRIEFS = [brief("b1", 1_000), brief("b2", 2_000)];

describe("Recent with the Daily briefs fold (topics on)", () => {
  const html = render([...MAIN, ...BRIEFS]);

  it("Recent holds the five newest chats that are not folded briefs", () => {
    expect(groups(html)[0]).toBe(RECENT_GROUP);
    expect(rowsOf(html, RECENT_GROUP)).toEqual(["recent:c1", "recent:c2", "recent:c3", "recent:c4", "recent:c5"]);
  });

  it("a folded brief shows only in its own group, never in Recent or a topic group", () => {
    const all = rowsOf(html, RECENT_GROUP);
    expect(all.some((id) => id.includes("b1") || id.includes("b2"))).toBe(false);
    // The fold is closed, so its rows are not drawn. It names its count.
    expect(groups(html)).toContain(BRIEFS_GROUP);
    const fold = html.slice(html.indexOf(`data-chat-group="${BRIEFS_GROUP}"`));
    expect(fold).toMatch(/>\s*2\s*</);
    // No brief row is in the chat groups.
    expect(html).not.toMatch(/data-session-row="(recent:)?b[12]"/);
  });

  it("with the fold open, each brief shows once, inside the fold", () => {
    const open = renderToStaticMarkup(
      createElement(SessionPicker, {
        sessions: [...MAIN, ...BRIEFS],
        activeId: "b1",
        onSelect: () => {},
        onRename: () => {},
        onArchive: () => {},
        onRemove: () => {},
        variant: "column",
        topicsOn: true,
      }),
    );
    expect((open.match(/data-session-row="b1"/g) ?? []).length).toBe(1);
    expect(open).not.toMatch(/data-session-row="recent:b1"/);
  });

  it("the briefs group stays after the chat groups", () => {
    const g = groups(html);
    expect(g[g.length - 1]).toBe(BRIEFS_GROUP);
  });
});

describe("the picker feeds pickerGroups the folded list", () => {
  it("the source passes folded.main, never lists.open", () => {
    const src = readFileSync(new URL("./SessionPicker.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/pickerGroups\(folded\.main,/);
    expect(src).not.toMatch(/pickerGroups\(lists\.open,/);
  });
});
