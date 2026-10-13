/**
 * A bare `#171` in an assistant answer, rendered (owner report, 2026-10-13).
 *
 * The owner's screenshot: "#172 one surface" drew as a task pill, and "#171"
 * and "#87" in the same paragraph stayed text. The fenced form linked because
 * the plugin knew it. The bare form did not reach the plugin's arm at all.
 *
 * vitest here is node-env with no DOM. `ControlLink` is a stub that keeps its
 * `onActivate`, and the test calls it: that is the plain left click, as in
 * `ui/EntityPill.test.ts`.
 */
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const activations: Array<{ href: string; go: () => void }> = [];

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/ControlLink", () => ({
  ControlLink: ({
    href,
    onActivate,
    children,
    ...rest
  }: { href: string; onActivate: () => void; children?: ReactNode } & Record<string, unknown>) => {
    activations.push({ href, go: onActivate });
    return createElement("a", { href, ...rest }, children);
  },
}));

import MessageBubble from "@/components/MessageBubble";
import { MarkdownBody } from "@/components/MarkdownMessage";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { buildEntityIndex } from "@/lib/entityIndex";
import { OWNER_TOOL_EVENTS, TASK5_ID } from "@/lib/entityPills.fixture";
import { agentAuthor, PROJECTS_AGENT, taskRefsForTurn } from "@/lib/projectsAgent";
import { taskRefResolver } from "@/lib/taskRefs";

const SCOPE = "chatTaskRef.test";
const T171 = "00000000-0000-4000-8000-000000000171";
const T87 = "00000000-0000-4000-8000-000000000087";

/** The lookup knows #171 and #87, and nothing else. */
beforeAll(async () => {
  vi.stubGlobal("fetch", async () =>
    new Response(JSON.stringify({
      rows: [
        { id: T171, task_number: 171, title: "Composer always steers", status_name: "In progress", category: "in_progress", archived: false },
        { id: T87, task_number: 87, title: "Card answers after restart", status_name: "To do", category: "todo", archived: false },
      ],
    }), { status: 200 }),
  );
  for (const n of [171, 87, 404]) taskRefResolver.request(SCOPE, n);
  await taskRefResolver.flush();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  push.mockClear();
  activations.length = 0;
});

function body(content: string, extra: Record<string, unknown> = {}): string {
  return renderToStaticMarkup(
    createElement(MarkdownBody, {
      content,
      entityPills: true,
      fences: true,
      entityIndex: buildEntityIndex(OWNER_TOOL_EVENTS),
      sessionId: SCOPE,
      ...extra,
    }),
  );
}

/** The pill markup alone: from its anchor to the anchor's end. */
const pill = (html: string) => html.match(/<a\b[^>]*>.*?<\/a>/)?.[0] ?? "";

describe("a bare #N in an assistant answer", () => {
  it("draws the SAME pill as the fenced form, when the message's tools read the task", () => {
    const fenced = pill(body("See #5 «Notification engine for projects» today."));
    const bare = pill(body("See #5 today."));
    expect(fenced).toContain(`href="/projects?task=${TASK5_ID}"`);
    expect(bare).toBe(fenced);
  });

  it("draws the pill from the lookup when no tool of this message read it", () => {
    const html = body("Both #171 and #87 are open.");
    expect(html).toContain(`href="/projects?task=${T171}"`);
    expect(html).toContain(`href="/projects?task=${T87}"`);
    // Number and title inline, as the fenced pill draws them.
    expect(pill(html)).toMatch(/#171<\/span><span[^>]*>Composer always steers<\/span>/);
  });

  it("opens the task card on a click", () => {
    body("Look at #171.");
    const link = activations.find((a) => a.href === `/projects?task=${T171}`);
    expect(link).toBeDefined();
    link!.go();
    expect(push).toHaveBeenCalledWith(`/projects?task=${T171}`);
  });

  it("stays text for a number that is no visible task, and draws no dead chip", () => {
    const html = body("Ticket #404 is gone, and #999 was never asked for.");
    expect(html).toContain("Ticket #404 is gone, and #999 was never asked for.");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("ListChecks");
  });

  it("shows the number only when the answer wrote the title right after it (review round 1)", () => {
    const html = body("- #171 Composer always steers (In progress)");
    const a = pill(html);
    expect(a).toContain(`href="/projects?task=${T171}"`);
    expect(a).not.toContain("Composer always steers</span>");
    // The title is said once, in the text after the pill.
    expect(html.match(/Composer always steers/g)?.length).toBe(1);
    expect(html).toContain("</a> Composer always steers (In progress)");
  });

  it("stays text when the turn's own tools read two tasks with that number", () => {
    const twice = buildEntityIndex([
      { status: "done", result: ["- #171 «One» · status «To do»", "  full_id: 11111111-1111-4111-8111-111111111111"].join("\n") },
      { status: "done", result: ["- #171 «Two» · status «To do»", "  full_id: 22222222-2222-4222-8222-222222222222"].join("\n") },
    ]);
    expect(twice.tasks).toHaveLength(2);
    const html = body("See #171.", { entityIndex: twice });
    expect(html).not.toContain("<a ");
  });

  it("stays text while the answer streams", () => {
    const html = body("Both #171 and", { caret: true });
    expect(html).not.toContain(`/projects?task=${T171}`);
    expect(html).toContain("#171");
  });

  it("upgrades a fenced pill that this message's tools did not read", () => {
    const html = body("Done: #171 «Composer always steers».");
    expect(html).toContain(`href="/projects?task=${T171}"`);
    // A wrong title draws the neutral chip it always drew.
    expect(body("Done: #171 «Something else».")).not.toContain(`/projects?task=${T171}`);
  });
});

// ── The surfaces ─────────────────────────────────────────────────────────────

function turn(extra: Partial<ChatMessage>): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "Next: #171, then #87.",
    timestamp: new Date("2026-10-13T10:00:00Z").getTime(),
    toolEvents: [],
    ...extra,
  } as unknown as ChatMessage;
}

function bubble(message: ChatMessage, agent: string): string {
  return renderToStaticMarkup(
    createElement(MessageBubble, {
      message,
      sessionId: SCOPE,
      entityPills: agent === PROJECTS_AGENT,
      taskRefAgent: agent,
    }),
  );
}

describe("which chats link a bare #N", () => {
  it("the Projects assistant's answer, in the main chat and in the Projects rail", () => {
    expect(bubble(turn(agentAuthor(PROJECTS_AGENT)), PROJECTS_AGENT)).toContain(`/projects?task=${T171}`);
  });

  it("the My Tasks assistant's answer", () => {
    const html = bubble(turn(agentAuthor("task-manager")), "task-manager");
    expect(html).toContain(`/projects?task=${T171}`);
    expect(html).toContain(`/projects?task=${T87}`);
  });

  it("not another agent's answer: its #4521 is an invoice", () => {
    expect(bubble(turn(agentAuthor("email-assistant")), "email-assistant")).not.toContain("/projects?task=");
    // Nor another agent's turn inside a Projects thread.
    expect(bubble(turn(agentAuthor("email-assistant")), PROJECTS_AGENT)).not.toContain("/projects?task=");
  });

  it("taskRefsForTurn", () => {
    expect(taskRefsForTurn(PROJECTS_AGENT, {})).toBe(true);
    expect(taskRefsForTurn("task-manager", agentAuthor("task-manager"))).toBe(true);
    expect(taskRefsForTurn("orchestrator", {})).toBe(false);
    expect(taskRefsForTurn(undefined, {})).toBe(false);
    expect(taskRefsForTurn(PROJECTS_AGENT, { authorKind: "human", authorEmail: "a@x.io" })).toBe(false);
  });
});
