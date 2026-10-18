/**
 * WS-55 EA-13e: oversight (`external_agent_access.md` §12.2 item 4, EA3-43,
 * EA3-49).
 *
 * The filter, the group-by lanes, the count buttons of the strip, and the
 * bulk "Ask an agent".
 *
 * R7, the mutations. Each one turned the named test red (run 2026-10-18, each
 * one after a commit, because `git checkout` restores HEAD):
 *
 * - M10 `groupTasks` keeps an empty agent lane -> "the agent lanes".
 * - M11 `dropRefusal` lets `agent` through (`WRITABLE` names it), or `REASONS` loses the agent
 *   reason -> "a drop on an agent lane".
 * - M12 `matchesAgentFocus("working")` drops `stale` -> "the count buttons".
 * - M13 `startMany` sends a 21st task -> "a 21st task".
 * - M14 `startMany` reuses one idempotency key -> "one start for each task".
 * - M15 `startMany` stops at the first refusal -> "a 409 shows on its own row".
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentWorkStrip } from "../components/AgentWorkStrip";
import {
  AGENT_LANES,
  AGENT_STRIP,
  type AgentSummary,
  agentLaneKey,
  isAgentWork,
  matchesAgentFocus,
  stripVisible,
} from "./agentRuns";
import { ProjectsApiError } from "./api";
import type { TaskRow } from "./api";
import { MAX_BULK_ASK, bulkLimitText, outcomeLine, startMany } from "./askAgent";
import { dropRefusal } from "./board";
import { groupTasks } from "./grouping";

const NOW = Date.parse("2026-10-18T12:00:00Z");

const row = (over: Partial<TaskRow> = {}): TaskRow =>
  ({
    id: "t1",
    project_id: "p1",
    root_project_id: "p1",
    status_id: "s1",
    title: "Ship it",
    ...over,
  }) as TaskRow;

describe("the agent lanes", () => {
  const t = (id: string, state: string | null) =>
    row({ id, agent_state: state as TaskRow["agent_state"] });

  it("reads in the order of a person's attention, and draws only a lane that holds a task", () => {
    const groups = groupTasks(
      [
        t("a", "failed"),
        t("b", null),
        t("c", "awaiting"),
        t("d", "stale"),
        t("e", "working"),
        t("f", "awaiting"),
      ],
      "agent",
      { statuses: [] },
    );
    expect(groups.map((g) => [g.label, g.tasks.map((x) => x.id)])).toEqual([
      ["Needs you", ["c", "f"]],
      ["Working", ["e"]],
      ["Idle", ["d"]],
      ["Failed", ["a"]],
      ["No agent", ["b"]],
    ]);
  });

  it("holds the eight lanes of the spec, and an unknown state reads as no agent", () => {
    expect(AGENT_LANES.map((l) => l.label)).toEqual([
      "Needs you",
      "Answered",
      "Working",
      "Queued",
      "Idle",
      "Stopping",
      "Failed",
      "No agent",
    ]);
    expect(agentLaneKey("mystery")).toBe("none");
    expect(agentLaneKey(undefined)).toBe("none");
  });

  it("a drop on an agent lane writes nothing, and says why", () => {
    expect(dropRefusal("agent")).toMatch(/agent state/i);
    expect(dropRefusal("status", "agent")).toMatch(/agent state/i);
    expect(dropRefusal("status")).toBeNull();
  });
});

describe("the count buttons", () => {
  const me = "ravi@x.test";
  const task = (over: Record<string, unknown>) => ({ ...over }) as Parameters<typeof matchesAgentFocus>[0];

  it("each button stands for the rows that its count reads", () => {
    expect(matchesAgentFocus(task({ agent_state: "queued" }), "queued", me, NOW)).toBe(true);
    expect(matchesAgentFocus(task({ agent_state: "working" }), "queued", me, NOW)).toBe(false);
    for (const state of ["working", "answered", "stale"]) {
      expect(matchesAgentFocus(task({ agent_state: state }), "working", me, NOW)).toBe(true);
    }
    expect(matchesAgentFocus(task({ agent_state: "queued" }), "working", me, NOW)).toBe(false);
    expect(
      matchesAgentFocus(
        task({ agent_state: "awaiting", agent_member_email: me }),
        "waiting",
        me,
        NOW,
      ),
    ).toBe(true);
    expect(
      matchesAgentFocus(task({ agent_state: "awaiting", agent_member_email: "x@y" }), "waiting", me, NOW),
    ).toBe(false);
  });

  it("in review is an open task with an open or draft pull request", () => {
    expect(matchesAgentFocus(task({ pr: { state: "open" } }), "review", me, NOW)).toBe(true);
    expect(matchesAgentFocus(task({ pr: { state: "draft" } }), "review", me, NOW)).toBe(true);
    expect(matchesAgentFocus(task({ pr: { state: "merged" } }), "review", me, NOW)).toBe(false);
    expect(
      matchesAgentFocus(
        task({ pr: { state: "open" }, completed_at: "2026-10-01T00:00:00Z" }),
        "review",
        me,
        NOW,
      ),
    ).toBe(false);
  });

  it("merged this week stops at 7 days", () => {
    const merged = (iso: string) => task({ pr: { state: "merged", merged_at: iso } });
    expect(matchesAgentFocus(merged("2026-10-12T12:00:01Z"), "merged", me, NOW)).toBe(true);
    expect(matchesAgentFocus(merged("2026-10-11T11:59:59Z"), "merged", me, NOW)).toBe(false);
    expect(matchesAgentFocus(task({ pr: { state: "merged" } }), "merged", me, NOW)).toBe(false);
  });

  it("the Agent work toggle keeps only the rows that the gateway flags", () => {
    const rows = [
      row({ id: "a", agent_worked: true }),
      row({ id: "b", agent_worked: false }),
      row({ id: "c" }),
    ];
    expect(rows.filter(isAgentWork).map((r) => r.id)).toEqual(["a"]);
  });
});

describe("the strip", () => {
  const counts = (over: Partial<AgentSummary> = {}): AgentSummary => ({
    queued: 0,
    working: 0,
    waiting_on_you: 0,
    in_review: 0,
    merged_this_week: 0,
    ...over,
  });

  it("draws nothing while every count is zero, and keeps the buttons while a filter is on", () => {
    expect(stripVisible(counts())).toBe(false);
    expect(stripVisible(null)).toBe(false);
    expect(stripVisible(counts({ in_review: 1 }))).toBe(true);
    const off = renderToStaticMarkup(
      createElement(AgentWorkStrip, { summary: counts(), focus: null, onFocus: () => {} }),
    );
    expect(off).toBe("");
    const on = renderToStaticMarkup(
      createElement(AgentWorkStrip, { summary: counts(), focus: "working", onFocus: () => {} }),
    );
    expect(on).toContain('data-agent-count="working"');
  });

  it("draws five count buttons, each with its words, and a zero count is disabled", () => {
    const html = renderToStaticMarkup(
      createElement(AgentWorkStrip, {
        summary: counts({ working: 3, in_review: 1 }),
        focus: "working",
        onFocus: () => {},
      }),
    );
    expect(AGENT_STRIP.map((i) => i.focus)).toEqual([
      "waiting",
      "working",
      "queued",
      "review",
      "merged",
    ]);
    for (const item of AGENT_STRIP) {
      expect(html).toContain(`data-agent-count="${item.focus}"`);
      expect(html).toContain(item.label);
    }
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
    // Three buttons have a zero count and are not pressed: waiting, queued, merged.
    expect(html.match(/disabled=""/g)).toHaveLength(3);
  });
});

describe("one start for each task (EA-13e)", () => {
  const ok = (taskId: string) => ({
    run: { id: `r-${taskId}`, task_id: taskId } as never,
    created: true,
  });

  it("one start for each task, each with its own idempotency key", async () => {
    const sent: { taskId: string; key: string; connection: string; body: string }[] = [];
    const outcomes = await startMany(
      [{ id: "t1" }, { id: "t2" }, { id: "t3" }],
      "c1",
      "  Fix it  ",
      async (taskId, body) => {
        sent.push({
          taskId,
          key: body.idempotency_key,
          connection: body.connection_id,
          body: body.body,
        });
        return ok(taskId);
      },
    );
    expect(sent.map((s) => s.taskId)).toEqual(["t1", "t2", "t3"]);
    expect(new Set(sent.map((s) => s.key)).size).toBe(3);
    expect(sent.every((s) => s.connection === "c1" && s.body === "Fix it")).toBe(true);
    expect(outcomes.every((o) => o.ok)).toBe(true);
    expect(outcomeLine(outcomes)).toBe("3 requests sent.");
  });

  it("a 409 shows on its own row, and the other tasks still go", async () => {
    const outcomes = await startMany(
      [{ id: "t1" }, { id: "t2" }, { id: "t3" }],
      "c1",
      "Fix it",
      async (taskId) => {
        if (taskId === "t2") {
          throw new ProjectsApiError("That agent has not picked up its last request here.", 409);
        }
        return ok(taskId);
      },
    );
    expect(outcomes.map((o) => o.ok)).toEqual([true, false, true]);
    const refused = outcomes[1];
    expect(refused.ok === false && refused.error).toBe(
      "That agent has not picked up its last request here.",
    );
    expect(outcomeLine(outcomes)).toBe("2 requests sent. 1 task needs a look.");
  });

  it("a 21st task: nothing is sent, and the words say the cap", async () => {
    let calls = 0;
    const many = Array.from({ length: MAX_BULK_ASK + 1 }, (_, i) => ({ id: `t${i}` }));
    const outcomes = await startMany(many, "c1", "Fix it", async (taskId) => {
      calls += 1;
      return ok(taskId);
    });
    expect(calls).toBe(0);
    expect(outcomes).toEqual([]);
    expect(MAX_BULK_ASK).toBe(20);
    expect(bulkLimitText(21)).toBe("Select at most 20 tasks. You selected 21.");
    // Twenty is the cap and goes through.
    const twenty = await startMany(many.slice(0, 20), "c1", "Fix it", async (taskId) => ok(taskId));
    expect(twenty).toHaveLength(20);
  });

  it("sends nothing for an empty instruction or no connection", async () => {
    let calls = 0;
    const send = async (taskId: string) => {
      calls += 1;
      return ok(taskId);
    };
    const blank = await startMany([{ id: "t1" }], "c1", "   ", send);
    const none = await startMany([{ id: "t1" }], "", "Fix it", send);
    expect(calls).toBe(0);
    expect(blank.every((o) => !o.ok)).toBe(true);
    expect(none.every((o) => !o.ok)).toBe(true);
  });
});
