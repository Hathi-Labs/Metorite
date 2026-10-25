/**
 * Link chips in the chat (KB-4b): the answer's own links, resolved against
 * the same message's tool results. Fence for the acceptance line: "An answer
 * that cites a page, a task and a deal draws three chips, and a link absent
 * from the tool results draws none."
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { MarkdownBody } from "@/components/MarkdownMessage";
import MessageBubble from "@/components/MessageBubble";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { buildLinkChipIndex } from "@/lib/linkChips";

const SEARCH = [
  "- «Onboarding guide» (page, 2026-10-01) /knowledge/pages/p-1",
  "  full_id: page:p-1",
  "- «Fix the login bug» (task, 2026-10-02) /projects?task=t-9",
  "  full_id: task:t-9",
  "- «Acme renewal» (deal, 2026-10-03) /crm?tab=deals&deal=d-3",
  "  full_id: deal:d-3",
].join("\n");

const TOOLS = [{ id: "t1", name: "search_company", result: SEARCH, status: "done" as const }];

const ANSWER =
  "See [the guide](/knowledge/pages/p-1), [the task](/projects?task=t-9) and [the deal](/crm?tab=deals&deal=d-3).";

function bubble(extra: Partial<ChatMessage> = {}): string {
  const message = {
    id: "m1",
    role: "assistant",
    content: ANSWER,
    timestamp: new Date("2026-10-20T10:00:00Z").getTime(),
    toolEvents: TOOLS,
    ...extra,
  } as unknown as ChatMessage;
  return renderToStaticMarkup(createElement(MessageBubble, { message, sessionId: "s1" }));
}

const labels = (html: string) => [...html.matchAll(/aria-label="(Open [^"]*)"/g)].map((m) => m[1]);

describe("an assistant answer that cites a page, a task and a deal", () => {
  it("draws three chips, each an anchor named for its app", () => {
    const html = bubble();
    expect(labels(html)).toEqual([
      "Open Onboarding guide in Knowledge",
      "Open Fix the login bug in Projects",
      "Open Acme renewal in CRM",
    ]);
    expect(html).toContain('href="/knowledge/pages/p-1"');
  });

  it("draws a link absent from the tool results as a plain link", () => {
    const html = bubble({ content: `${ANSWER} Also [other](/projects?task=zzz).` });
    expect(labels(html)).toHaveLength(3);
    expect(html).toContain('href="/projects?task=zzz"');
    expect(html).toContain(">other<");
  });

  it("draws no chip when the tool is still running or failed", () => {
    expect(labels(bubble({ toolEvents: [{ ...TOOLS[0], status: "running" }] } as Partial<ChatMessage>))).toEqual([]);
    expect(labels(bubble({ toolEvents: [{ ...TOOLS[0], status: "error" }] } as Partial<ChatMessage>))).toEqual([]);
  });

  it("finds a search run by a sub-agent", () => {
    const html = bubble({
      toolEvents: [{ id: "c1", name: "call_agent", status: "done", subAgentTools: TOOLS }],
    } as unknown as Partial<ChatMessage>);
    expect(labels(html)).toHaveLength(3);
  });

  it("leaves a bare path and a link in a code span as text", () => {
    const html = bubble({ content: "Bare /projects?task=t-9 and `[x](/projects?task=t-9)`" });
    expect(labels(html)).toEqual([]);
  });
});

describe("a user turn", () => {
  it("draws no chip", () => {
    expect(labels(bubble({ role: "user" } as Partial<ChatMessage>))).toEqual([]);
  });
});

describe("the flow-block site", () => {
  it("draws chips in text that follows a card", () => {
    const html = bubble({
      content: "",
      segments: [
        { id: "s0", text: "Looking." },
        { id: "s1", text: ANSWER },
      ],
      customEvents: [
        { name: "generative_ui", segmentCutoff: 1, value: { type: "Text", props: { text: "card" } } },
      ],
    } as unknown as Partial<ChatMessage>);
    expect(labels(html)).toHaveLength(3);
  });
});

describe("MarkdownBody", () => {
  it("draws chips from the index and leaves the source as [title](/path)", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, { content: ANSWER, linkChips: buildLinkChipIndex(TOOLS) }),
    );
    expect(labels(html)).toHaveLength(3);
    expect(ANSWER).toContain("[the guide](/knowledge/pages/p-1)");
  });

  it("draws no chip without the prop", () => {
    const html = renderToStaticMarkup(createElement(MarkdownBody, { content: ANSWER }));
    expect(labels(html)).toEqual([]);
  });
});
