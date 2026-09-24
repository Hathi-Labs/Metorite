/**
 * A turn names the agent it came from, so a switch to the Projects assistant
 * does not redraw an earlier agent's answer with pills (WS-27bm S9 fix
 * round 3).
 *
 * `useAgentChat` stamps `agentAuthor(agentName)` on a turn when it starts to
 * stream, on the placeholder of a replay, and on a restored turn that has no
 * author. `pillsForTurn` then refuses any turn whose author is another agent.
 * The hook cannot run in this node-env suite, so the three stamp sites are
 * pinned by source, and the rendering is proven on the stamped messages.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import MessageBubble from "@/components/MessageBubble";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { OWNER_TOOL_EVENTS, TASK5_ID } from "@/lib/entityPills.fixture";
import { PROJECTS_AGENT, agentAuthor, pillsForTurn } from "@/lib/projectsAgent";

const TEXT = "Reply to alice.wong@acme.com about #5 «Notification engine for projects».";

function turn(extra: Partial<ChatMessage>): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    content: TEXT,
    timestamp: new Date("2026-09-24T10:00:00Z").getTime(),
    toolEvents: OWNER_TOOL_EVENTS,
    ...extra,
  } as unknown as ChatMessage;
}

/** The thread is now the Projects assistant's: AgentChat passes `true`. */
const inProjectsThread = (message: ChatMessage) =>
  renderToStaticMarkup(createElement(MessageBubble, { message, sessionId: "s1", entityPills: true }));

describe("after a switch to the Projects assistant", () => {
  it("an earlier answer from another agent keeps its mailto and «»", () => {
    const html = inProjectsThread(turn(agentAuthor("email-assistant")));
    expect(html).toContain('href="mailto:alice.wong@acme.com"');
    expect(html).toContain("«Notification engine for projects»");
    expect(html).not.toContain(`/projects?task=${TASK5_ID}`);
  });

  it("a live Projects turn has pills", () => {
    const html = inProjectsThread(turn({ ...agentAuthor(PROJECTS_AGENT), streaming: true }));
    expect(html).toContain(`href="/projects?task=${TASK5_ID}"`);
    expect(html).not.toContain("mailto:");
  });

  it("a saved Projects turn has pills after a reload", () => {
    // What `GET /chat/sessions/{id}/messages` returns for the row: the
    // server stamps `agent_name`, the session's agent, which is the same
    // string (`routes/chat.py::_attribute`).
    const html = inProjectsThread(turn({ authorKind: "agent", authorEmail: "projects-assistant" }));
    expect(html).toContain(`href="/projects?task=${TASK5_ID}"`);
  });
});

describe("pillsForTurn with an author", () => {
  it("needs the author to be the Projects assistant", () => {
    expect(pillsForTurn(true, agentAuthor(PROJECTS_AGENT))).toBe(true);
    expect(pillsForTurn(true, agentAuthor("email-assistant"))).toBe(false);
    expect(pillsForTurn(true, { authorEmail: "email-assistant" })).toBe(false);
    expect(pillsForTurn(true, { authorKind: "human", authorEmail: "a@x.io" })).toBe(false);
    // A row saved before attribution existed has no author at all.
    expect(pillsForTurn(true, {})).toBe(true);
  });

  it("stamps the agent it is given", () => {
    expect(agentAuthor("crm-assistant")).toEqual({ authorKind: "agent", authorEmail: "crm-assistant" });
  });
});

describe("useAgentChat stamps every assistant turn it starts", () => {
  const src = readFileSync(
    fileURLToPath(new URL("../hooks/useAgentChat.ts", import.meta.url)),
    "utf-8",
  );

  it("on a new turn, on a replay placeholder, and on a restored turn", () => {
    // Remove one stamp and this count drops.
    expect(src.match(/agentAuthor\(agentNameRef\.current\)/g) ?? []).toHaveLength(3);
  });

  it("stamps the new turn and the placeholder in their literals", () => {
    const literals = src.match(/role: "assistant", content: "",[\s\S]{0,400}?\n\s*\};/g) ?? [];
    expect(literals.length).toBeGreaterThanOrEqual(2);
    for (const lit of literals) expect(lit).toContain("...agentAuthor(agentNameRef.current)");
  });
});

describe("the name the server stamps", () => {
  it("is the session's agent name, the same string the rail uses", () => {
    const chat = readFileSync(
      fileURLToPath(
        new URL("../../../../apps/services/gateway/gateway/routes/chat.py", import.meta.url),
      ),
      "utf-8",
    );
    expect(chat).toContain('return "agent", (m.author_email or agent_name or None)');
    // And the first stamp wins, so a client stamp survives a later save.
    expect(chat).toContain("author_email   = COALESCE(chat_message.author_email, EXCLUDED.author_email)");
    expect(PROJECTS_AGENT).toBe("projects-assistant");
  });
});
