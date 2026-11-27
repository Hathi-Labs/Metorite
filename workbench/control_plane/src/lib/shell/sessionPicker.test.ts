/**
 * The chat list draws the pin of a member (WS-CO CO-11,
 * `chat_organization.md` §18).
 *
 * The runner is `environment: "node"`, so the list renders to a string. What
 * this file holds (R7):
 *
 * 1. With the switch on, the pinned chats come first under "Pinned", and a
 *    row offers Pin, or Unpin when it is pinned.
 * 2. With the switch off, the list has no Pinned group and no pin act, even
 *    when a stored chat holds a pin.
 * 3. A list with no `onPin` (the chat list of Artifacts) offers no pin.
 * 4. The dock passes the switch and the act, draws the header pin on a
 *    desktop, and puts it in the "…" control on a phone.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ChatSession } from "@/lib/sessions";

const live = vi.hoisted(() => ({
  running: new Set<string>(),
  unread: new Set<string>(),
  needs: new Set<string>(),
}));

vi.mock("@/hooks/useActiveSessions", () => ({
  useActiveSessions: () => live.running,
  useUnreadIds: () => live.unread,
  useNeedsInputIds: () => live.needs,
}));

import { SessionPicker } from "./SessionPicker";

const NOW = new Date();
const iso = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

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

const SESSIONS = [
  chat("plain"),
  chat("pinned", { updatedAt: iso(40 * 86_400_000), pinnedAt: iso(3_600_000) }),
];

function draw(props: { pinsOn?: boolean; onPin?: (id: string, pinned: boolean) => void }): string {
  return renderToStaticMarkup(
    createElement(SessionPicker, {
      sessions: SESSIONS,
      activeId: "",
      onSelect: () => {},
      onRename: () => {},
      onArchive: () => {},
      onRemove: () => {},
      variant: "column",
      ...props,
    }),
  );
}

/** The labels of the groups, in the order the list draws them. */
function groups(html: string): string[] {
  return [...html.matchAll(/data-chat-group="([^"]+)"/g)].map((m) => m[1]);
}

describe("the pin in the chat list (CO-11)", () => {
  it("with the switch on, Pinned comes first and each row offers its act", () => {
    const html = draw({ pinsOn: true, onPin: () => {} });
    expect(groups(html)[0]).toBe("Pinned");
    const pinnedGroup = html.split('data-chat-group="Pinned"')[1].split("</section>")[0];
    expect(pinnedGroup).toContain("Chat pinned");
    expect(pinnedGroup).not.toContain("Chat plain");
    expect(html).toContain('aria-label="Unpin Chat pinned"');
    expect(html).toContain('aria-label="Pin Chat plain"');
    // A pinned chat shows once.
    expect(html.match(/data-session-row="pinned"/g)).toHaveLength(1);
  });

  it("with the switch off there is no Pinned group and no pin act", () => {
    const html = draw({ pinsOn: false, onPin: () => {} });
    expect(groups(html)).not.toContain("Pinned");
    expect(html).not.toMatch(/aria-label="(Un)?[Pp]in /);
    expect(html).toContain("Chat pinned");
  });

  it("a list with no pin act offers none, as in Artifacts", () => {
    const html = draw({ pinsOn: true });
    expect(groups(html)).not.toContain("Pinned");
    expect(html).not.toMatch(/aria-label="(Un)?[Pp]in /);
  });
});

describe("the amber dot in the chat list (task 3600)", () => {
  /** The dot inside the row of one chat. */
  const rowOf = (html: string, id: string) =>
    html.split(`data-session-row="${id}"`)[1].split("data-session-row=")[0];

  it("a chat that waits on the member wears the amber dot, and loses it when the row closes", () => {
    live.running = new Set(["plain", "pinned"]);
    live.unread = new Set(["pinned"]);
    live.needs = new Set(["plain"]);
    const open = draw({});
    expect(rowOf(open, "plain")).toContain('aria-label="Needs your answer"');
    expect(rowOf(open, "plain")).toContain("bg-warning");
    expect(rowOf(open, "plain")).not.toContain("Assistant is working");
    expect(rowOf(open, "pinned")).toContain('aria-label="Assistant is working"');
    live.needs = new Set();
    const closed = draw({});
    expect(closed).not.toContain("Needs your answer");
    expect(rowOf(closed, "plain")).toContain('aria-label="Assistant is working"');
    live.running = new Set();
    live.unread = new Set();
  });
});

describe("the dock wires the pin (CO-11)", () => {
  const dock = readFileSync(fileURLToPath(new URL("./AssistantDock.tsx", import.meta.url)), "utf8");

  it("passes the switch and the act to the list", () => {
    expect(dock).toContain("const pinsOn = useChatPinOn();");
    expect(dock).toMatch(/onPin: chat\.pin,\s*pinsOn,/);
  });

  it("draws the header pin on a desktop only, and the phone's in the more control", () => {
    expect(dock).toContain("pinnable && !phone ?");
    expect(dock).toContain('label: activePinned ? "Unpin this chat" : "Pin this chat"');
    expect(dock).toMatch(/const pinnable = pinsOn && active != null && active\.messageCount > 0 && !active\.archivedAt;/);
  });
});
