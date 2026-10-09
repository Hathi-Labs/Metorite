/**
 * The edit of the last message, as drawn (owner, 2026-10-09).
 *
 * Mutations this suite catches (R7):
 * - Edit is offered on a message that is not the last one;
 * - the edit composer comes back as a second hand-rolled box: an emoji icon,
 *   a warning-coloured border, or a Send button of its own;
 * - the "Edited" label disappears after a reload.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MessageBubble from "@/components/MessageBubble";
import type { ChatMessage } from "@/lib/chatStore";
import { editedMarker } from "@/lib/chatEdit";

const mine: ChatMessage = { id: "u2", role: "user", content: "create tasks", timestamp: 0 };
const render = (props: Record<string, unknown>) =>
  renderToStaticMarkup(createElement(MessageBubble, { message: mine, sessionId: "s1", ...props }));

describe("Edit on a user message", () => {
  it("is offered only when the bubble is the last user message", () => {
    expect(render({ onEditLast: () => {} })).toContain('aria-label="Edit message"');
    expect(render({})).not.toContain('aria-label="Edit message"');
    expect(render({})).not.toContain("Double-click to edit");
  });

  it("shows an Edited label beside the time, from the persisted marker", () => {
    const edited = { ...mine, customEvents: [editedMarker("u1")] };
    const html = renderToStaticMarkup(createElement(MessageBubble, { message: edited, sessionId: "s1" }));
    expect(html).toContain(">Edited<");
    expect(render({})).not.toContain(">Edited<");
  });
});

describe("the edit composer is a variant of the chat composer", () => {
  const src = readFileSync(join(__dirname, "MessageBubble.tsx"), "utf8");
  const start = src.indexOf("Edit mode — a variant of the chat composer");
  const block = src.slice(start, src.indexOf("Normal user bubble", start));

  it("uses the shared Send button, the icon set and tokens only", () => {
    expect(start).toBeGreaterThan(0);
    expect(block).toContain("<ChatSendButton");
    expect(block).toContain('<Icon name="Pencil"');
    expect(block).not.toMatch(/✏|warning/);
    expect(block).toContain("rounded-2xl border border-primary/40 bg-secondary/50");
    // One header row: icon, "Editing", Cancel. The key hint is desktop only.
    expect(block).toContain(">Editing<");
    expect(block).toMatch(/hidden lg:inline pointer-coarse:hidden/);
  });

  it("the composer and the edit draw the same Send button", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf8");
    expect(chat).toContain("<ChatSendButton disabled={!input.trim() || loadingHistory} />");
  });
});
