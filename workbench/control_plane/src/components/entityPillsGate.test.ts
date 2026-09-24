/**
 * Entity pills are for the Projects assistant only (WS-27bm S9 fix round 1).
 *
 * The first cut turned them on in `MessageBubble` for every agent. In the
 * email assistant, "alice.wong@acme.com" became an "alice.wong" chip that no
 * one could copy or mail, and the index read email bodies. `AgentChat`
 * now passes `entityPills` only when its agent is `PROJECTS_AGENT`, and a
 * generative-UI `markdown` node draws pills only inside that turn's provider.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { EntityIndexContext } from "@/components/ChatEntityPill";
import { GenUiMarkdown } from "@/components/GenerativeUINode";
import MessageBubble from "@/components/MessageBubble";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { buildEntityIndex } from "@/lib/entityIndex";
import { OWNER_TOOL_EVENTS, TASK5_ID } from "@/lib/entityPills.fixture";
import { PROJECTS_AGENT, pillsForTurn } from "@/lib/projectsAgent";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const TEXT = "Reply to alice.wong@acme.com about #5 «Notification engine for projects».";

function bubble(entityPills: boolean, extra: Partial<ChatMessage> = {}): string {
  const message = {
    id: "m1",
    role: "assistant",
    content: TEXT,
    timestamp: new Date("2026-09-24T10:00:00Z").getTime(),
    toolEvents: OWNER_TOOL_EVENTS,
    ...extra,
  } as unknown as ChatMessage;
  return renderToStaticMarkup(
    createElement(MessageBubble, { message, sessionId: "s1", entityPills }),
  );
}

describe("another agent's reply", () => {
  const html = bubble(false);

  it("keeps an email as a mailto link", () => {
    expect(html).toContain('href="mailto:alice.wong@acme.com"');
    expect(html).toContain(">alice.wong@acme.com</a>");
  });

  it("keeps «» as plain text and draws no pill", () => {
    expect(html).toContain("«Notification engine for projects»");
    expect(html).not.toContain(`/projects?task=${TASK5_ID}`);
  });
});

describe("the Projects assistant's reply", () => {
  it("gets pills", () => {
    const html = bubble(true);
    expect(html).toContain(`href="/projects?task=${TASK5_ID}"`);
    expect(html).not.toContain("mailto:");
    expect(html).not.toContain("«");
  });

  it("draws none for a turn another agent wrote in the same thread", () => {
    const html = bubble(true, { authorKind: "agent", authorEmail: "email-assistant" } as Partial<ChatMessage>);
    expect(html).toContain('href="mailto:alice.wong@acme.com"');
  });
});

describe("pillsForTurn", () => {
  it("is true for a Projects turn only", () => {
    expect(pillsForTurn(true, {})).toBe(true);
    expect(pillsForTurn(true, { authorKind: "agent", authorEmail: PROJECTS_AGENT })).toBe(true);
    expect(pillsForTurn(true, { authorKind: "agent", authorEmail: "crm-assistant" })).toBe(false);
    expect(pillsForTurn(false, {})).toBe(false);
  });
});

describe("AgentChat gates the prop by agent name", () => {
  it("passes entityPills only for PROJECTS_AGENT", () => {
    const src = readFileSync(
      fileURLToPath(new URL("./AgentChat.tsx", import.meta.url)),
      "utf-8",
    );
    expect(src).toContain("entityPills={currentAgentName === PROJECTS_AGENT}");
  });

  it("MessageBubble's memo compares the prop", () => {
    const src = readFileSync(
      fileURLToPath(new URL("./MessageBubble.tsx", import.meta.url)),
      "utf-8",
    );
    expect(src).toContain("a.entityPills === b.entityPills");
  });
});

describe("a generative-UI markdown node", () => {
  it("draws no pill outside a Projects provider", () => {
    const html = renderToStaticMarkup(createElement(GenUiMarkdown, { content: TEXT }));
    expect(html).toContain("«Notification engine for projects»");
    expect(html).toContain("mailto:alice.wong@acme.com");
  });

  it("draws pills inside one", () => {
    const html = renderToStaticMarkup(
      createElement(
        EntityIndexContext.Provider,
        { value: buildEntityIndex(OWNER_TOOL_EVENTS) },
        createElement(GenUiMarkdown, { content: TEXT }),
      ),
    );
    expect(html).toContain(`href="/projects?task=${TASK5_ID}"`);
    expect(html).not.toContain("«");
  });
});
