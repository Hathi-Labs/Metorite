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

vi.mock("@/hooks/useActiveSessions", () => ({
  useActiveSessions: () => new Set<string>(),
  useUnreadIds: () => new Set<string>(),
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
