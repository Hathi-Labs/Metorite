/**
 * `AgentQuestionCard` — the one answer component (WS-55 EA-18b,
 * `external_agent_access.md` §12.8). The run panel and the Questions list both
 * render it, and this file holds its five states.
 *
 * Mutations caught, by test:
 * - an option card draws for a reader the server says may not answer;
 * - a typed archive request draws the generic card with free text;
 * - another member sees Approve and Decline of a request;
 * - the answered state loses "Answered by <name>";
 * - `allow_multiple` draws one choice and not check boxes;
 * - the run panel keeps its own copy of the card (source test).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import type { AgentActivity } from "../lib/agentRuns";
import { AgentQuestionCard, type AgentQuestionCardProps } from "./AgentQuestionCard";

const ELICITATION: AgentActivity = {
  id: "e1",
  seq: 3,
  type: "elicitation",
  content: { body: "Which database?" },
  signal: "select",
  signal_metadata: {
    options: [
      { id: "pg", label: "Postgres", recommended: true },
      { id: "my", label: "MySQL" },
    ],
    allow_multiple: false,
  },
};

const ARCHIVE: AgentActivity = {
  id: "e2",
  seq: 4,
  type: "elicitation",
  content: { body: "Archive this task?" },
  content_metadata: {
    request: { kind: "archive_task", expires_at: new Date(Date.now() + 3_600_000).toISOString() },
  },
  signal: "select",
  signal_metadata: {
    options: [
      { id: "approve", label: "Approve" },
      { id: "decline", label: "Decline" },
    ],
  },
};

const noop = () => {};

function draw(over: Partial<AgentQuestionCardProps> = {}) {
  return renderToStaticMarkup(
    createElement(AgentQuestionCard, {
      elicitation: ELICITATION,
      run: { member_email: "priya.rao@example.com" },
      clientLabel: "Claude Code",
      open: true,
      archiveMine: false,
      onAnswer: noop,
      onDecision: noop,
      ...over,
    }),
  );
}

describe("an open question", () => {
  const html = draw();

  it("draws the chat's card with the agent's options in order and the recommended mark", () => {
    expect(html).toContain('data-elicitation-card="open"');
    expect(html.indexOf("Postgres")).toBeGreaterThan(-1);
    expect(html.indexOf("Postgres")).toBeLessThan(html.indexOf("MySQL"));
    expect(html.toLowerCase()).toContain("recommended");
  });

  it("offers a free-text field beside the options", () => {
    expect(html).toContain("<textarea");
  });

  it("names the agent in the line beside Submit", () => {
    expect(html).toContain("The agent reads your answer at its next step.");
  });

  it("draws check boxes for allow_multiple", () => {
    const multi = draw({
      elicitation: {
        ...ELICITATION,
        signal_metadata: { ...ELICITATION.signal_metadata, allow_multiple: true },
      },
    });
    // The shared card draws a circle for one choice and a box for many.
    expect(multi).toMatch(/lucide-square/);
    expect(multi).not.toMatch(/lucide-circle[^-]/);
    expect(html).toMatch(/lucide-circle/);
    expect(html).not.toMatch(/lucide-square/);
  });

  it("draws nothing once the question no longer waits", () => {
    expect(draw({ open: false })).toBe("");
  });
});

describe("a reader who may not answer", () => {
  const html = draw({ canAnswer: false });

  it("shows the question read-only with who can answer it", () => {
    expect(html).toContain('data-agent-question="readonly"');
    expect(html).toContain("Which database?");
    expect(html).toContain("Only Priya, the creator of the task, its assignees, or an owner, admin or manager can answer this");
  });

  it("draws no option and no Submit", () => {
    expect(html).not.toContain("data-elicitation-card");
    expect(html).not.toContain("Postgres");
    expect(html).not.toContain("<button");
  });

  it("treats an absent flag as the old rule, so the card is open", () => {
    expect(draw({ canAnswer: undefined })).toContain('data-elicitation-card="open"');
  });
});

describe("a typed archive request", () => {
  it("draws the server's two options and no free text for the run's member", () => {
    const html = draw({ elicitation: ARCHIVE, archiveMine: true });
    expect(html).toContain('data-archive-request="mine"');
    expect(html).toContain("Archive request");
    expect(html).toContain("Approve");
    expect(html).toContain("Decline");
    expect(html).not.toContain("<textarea");
  });

  it("shows every other member the request and who must answer it, with no button", () => {
    const html = draw({ elicitation: ARCHIVE, archiveMine: false });
    expect(html).toContain('data-archive-request="waiting"');
    expect(html).toContain("Waiting for Priya");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<textarea");
  });

  it("reads the list row's kind from the server and not from the activity", () => {
    // A list row has no request stamp. The kind says it is a request.
    const bare: AgentActivity = { ...ARCHIVE, content_metadata: null };
    const html = draw({ elicitation: bare, archiveMine: true, kind: "archive_request", expired: false });
    expect(html).toContain('data-archive-request="mine"');
  });

  it("an expired request lets the member Decline it and nobody else", () => {
    const old: AgentActivity = {
      ...ARCHIVE,
      content_metadata: { request: { kind: "archive_task", expires_at: "2020-01-01T00:00:00Z" } },
    };
    const mine = draw({ elicitation: old, archiveMine: true });
    const other = draw({ elicitation: old, archiveMine: false });
    expect(mine).toContain('data-archive-request="expired"');
    expect(mine).toContain("data-archive-decline");
    expect(other).toContain('data-archive-request="expired"');
    expect(other).not.toContain("data-archive-decline");
  });
});

describe("an answered question", () => {
  const prompt: AgentActivity = {
    id: "p1",
    seq: 5,
    type: "prompt",
    content: { selected: ["pg"] },
    actor: "asha.nair@example.com",
    in_reply_to: "e1",
  };

  it("reads 'Answered by <name>: <labels>' and nothing else", () => {
    const html = draw({ answer: prompt, open: false });
    expect(html).toContain("data-agent-answered");
    expect(html).toContain("Answered by Asha Nair: Postgres");
    expect(html).not.toContain("data-elicitation-card");
  });

  it("uses the directory name when the host has one", () => {
    const html = draw({
      answer: prompt,
      open: false,
      personLabels: new Map([["asha.nair@example.com", "Asha N."]]),
    });
    expect(html).toContain("Answered by Asha N.");
  });
});

describe("one component, no copy", () => {
  const dir = fileURLToPath(new URL(".", import.meta.url));
  const panel = readFileSync(`${dir}AgentRunPanel.tsx`, "utf8");
  const list = readFileSync(`${dir}QuestionsView.tsx`, "utf8");

  it("the run panel and the list both render AgentQuestionCard", () => {
    expect(panel).toContain("<AgentQuestionCard");
    expect(list).toContain("<AgentQuestionCard");
  });

  it("neither host draws the question card by hand", () => {
    // SW-43b: the panel's one `<ElicitationCard` is the "Change answer" form
    // (`key={`change-...`}`). The first answer and the archive form are the card.
    const hand = panel.match(/<ElicitationCard[^>]*>/g) ?? [];
    expect(hand.length).toBeLessThanOrEqual(1);
    for (const h of hand) expect(panel.slice(panel.indexOf(h), panel.indexOf(h) + 120)).toMatch(/change-/);
    expect(list).not.toContain("<ElicitationCard");
  });

  it("the run panel reads can_answer off the run, and reads an absent flag as true", () => {
    expect(panel).toContain("run.can_answer !== false");
  });

  it("a failed send resets the exit gate and never remounts the card", () => {
    // A remount would throw away the typed text and the picks (DESIGN_SYSTEM §9).
    for (const host of [panel, list]) {
      // No key may hold a failure or retry count.
      expect(host).not.toMatch(/<AgentQuestionCard[^>]*\bkey=\{[^}]*(retry|fail|attempt)/i);
      expect(host).toContain("retryToken=");
    }
    // A new question is a new card (review of #945, P1): the panel keys on the
    // question id. SW-43b's `cardNonce` joins the key: it moves only when an
    // "Are you sure?" step starts, never on a failed send.
    expect(panel).toMatch(/<AgentQuestionCard\s+key=\{`\$\{elicitation\.id\}:\$\{cardNonce\}`\}/);
    expect(list).toContain("attempts[row.activity_id]");
    const card = readFileSync(`${dir}../../../components/ElicitationCard.tsx`, "utf8");
    expect(card).toMatch(/\}, \[closed, retryToken\]\)/);
    expect(card).toMatch(/exit\.current = cardExit\(\)/);
  });

  it("the panel bumps the token for a failed answer only, not for a failed Stop", () => {
    const write = panel.slice(panel.indexOf("async function write("), panel.indexOf("async function submitAnswer"));
    expect(write).not.toContain("setRetryToken");
    const submit = panel.slice(panel.indexOf("async function submitAnswer"), panel.indexOf("async function withdraw"));
    expect(submit.match(/setRetryToken/g)?.length).toBe(2);
    const stop = panel.slice(panel.indexOf("async function stop()"), panel.indexOf("return (\n    <section"));
    expect(stop).not.toContain("setRetryToken");
  });

  it("the list dims old rows and clears them after a failed read of a changed query", () => {
    expect(list).toContain("rowsAreStale(");
    expect(list).toContain("aria-busy={stale || undefined}");
    expect(list).toContain("keepRowsAfterFailedRead(");
  });

  it("the list has a Retry for a failed read and passes the server's expiry", () => {
    expect(list).toContain("Retry");
    expect(list).not.toContain("expired={false}");
  });

  it("the list sends a typed request to archive-approval and not to the generic route", () => {
    expect(list).toContain("agentRunsApi.archiveApproval");
    expect(list).toContain("agentRunsApi.post");
  });
});
