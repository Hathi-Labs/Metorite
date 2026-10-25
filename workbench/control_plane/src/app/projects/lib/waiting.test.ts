/**
 * WS-27bn RA-3 — "Waiting on people", the pure rules of the panel.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 C and E, §8 RA-3. The
 * server decides each row (`analytics_waiting.py`). These tests pin how the
 * panel, its table and the email word what the server sent.
 */
import { describe, expect, it } from "vitest";

import type { RenderedReportBody, WaitingReport, WaitingRow } from "./api";
import { overviewShown } from "./overviewLive";
import {
  agentLine,
  answerLine,
  handoffWhy,
  listRows,
  reviewWhy,
  taskLabel,
  waitedFor,
  waitingHidden,
  waitingIsClear,
  waitingLists,
} from "./waiting";

const ROW: WaitingRow = {
  id: "t1",
  task_number: 12,
  title: "Choose the nozzle",
  project_id: "p1",
  project_name: "Rig",
  since: "2026-10-25T06:00:00+00:00",
  age_hours: 3,
  agent: "Claude Code",
  client_via: "mcp:claude.ai:claude-code",
  member: "ana@example.test",
  member_name: "Ana Rao",
};

function section(over: Partial<WaitingReport> = {}): WaitingReport {
  return {
    summary: "Nobody is waiting on a person.",
    agent_runs_seen: true,
    questions: [],
    questions_total: 0,
    reviews: [],
    reviews_total: 0,
    handoffs: [],
    handoffs_total: 0,
    answer_time: { asked: 0, answered: 0, unanswered: 0, median_hours: null, p90_hours: null },
    max_rows: 20,
    ...over,
  };
}

describe("waitedFor: the words of the server's summary", () => {
  it.each([
    [null, "under an hour"],
    [0.5, "under an hour"],
    [1.2, "1 hour"],
    [5, "5 hours"],
    [47.4, "47 hours"],
    [50, "2 days"],
    [72, "3 days"],
  ])("%s hours reads %s", (hours, words) => {
    expect(waitedFor(hours as number | null)).toBe(words);
  });
});

describe("§6.8 E2: an agent never hides its person", () => {
  it("names the agent and the member it works for", () => {
    expect(agentLine(ROW)).toBe("Claude Code for Ana Rao");
  });

  it("names the member by address through the resolver when no name came", () => {
    expect(agentLine({ ...ROW, member_name: null }, (e) => e.split("@")[0])).toBe(
      "Claude Code for ana"
    );
  });

  it("names an in-app agent alone, because it works for no member", () => {
    expect(agentLine({ ...ROW, agent: "coder", member: null, member_name: null })).toBe("coder");
  });

  it("labels a task by its number and its title", () => {
    expect(taskLabel(ROW)).toBe("#12 Choose the nozzle");
    expect(taskLabel({ ...ROW, task_number: null })).toBe("Choose the nozzle");
  });
});

describe("why a row waits", () => {
  it("names the arm of a review", () => {
    expect(reviewWhy({ reasons: ["review_lane"] })).toBe("In a review lane");
    expect(reviewWhy({ reasons: ["pull_request"] })).toBe("An open pull request");
    expect(reviewWhy({ reasons: ["review_lane", "pull_request"] })).toBe(
      "In review, with an open pull request"
    );
  });

  it("names the side of a handoff and the person on the other side", () => {
    const blocked = {
      agent_side: "blocked" as const,
      blocker_title: "Order steel",
      person: "dee@example.test",
      person_name: "Dee",
    };
    expect(handoffWhy(blocked)).toBe("Blocked by Dee: Order steel");
    expect(handoffWhy({ ...blocked, agent_side: "blocker" })).toBe("The agent's task blocks Dee");
    expect(handoffWhy({ ...blocked, person: null, person_name: null })).toBe(
      "Blocked by a person: Order steel"
    );
  });
});

describe("the lists keep the server's order and say what they cut", () => {
  it("never re-sorts a list (§6.8 E1: no rank of people)", () => {
    const rows = [
      { ...ROW, id: "old", member_name: "Zed" },
      { ...ROW, id: "new", member_name: "Ana" },
    ];
    const lists = waitingLists(section({ questions: rows, questions_total: 2 }));
    expect(lists[0].rows.map((r) => r.id)).toEqual(["old", "new"]);
  });

  it("holds the three lists in the panel's order, with the server's totals", () => {
    const lists = waitingLists(section({ questions: [ROW], questions_total: 9 }));
    expect(lists.map((l) => l.kind)).toEqual(["questions", "reviews", "handoffs"]);
    expect(lists[0].total).toBe(9);
    expect(lists[0].rows[0]).toEqual({
      id: "t1",
      label: "#12 Choose the nozzle",
      detail: "Claude Code for Ana Rao · 3 hours · asks for an answer",
    });
  });

  it("counts the cut from the server's total, never from the rows sent", () => {
    expect(listRows([1, 2, 3, 4, 5, 6, 7], 30, 5)).toEqual({ shown: [1, 2, 3, 4, 5], more: 25 });
    expect(listRows([1, 2], undefined, 5)).toEqual({ shown: [1, 2], more: 0 });
    expect(listRows(undefined, 3, 5)).toEqual({ shown: [], more: 3 });
  });
});

describe("the answer time", () => {
  it("says the median, the p90, the denominator and the questions with no answer", () => {
    const a = section({
      answer_time: { asked: 4, answered: 3, unanswered: 1, median_hours: 3, p90_hours: 4.6 },
    });
    expect(answerLine(a)).toBe(
      "Answer time: median 3 hours, p90 5 hours, over 3 answered. 1 with no answer yet."
    );
  });

  it("says nothing when no question had an answer, never a zero", () => {
    expect(answerLine(section())).toBeNull();
    expect(
      answerLine(
        section({
          answer_time: { asked: 2, answered: 0, unanswered: 2, median_hours: null, p90_hours: null },
        })
      )
    ).toBeNull();
  });
});

describe("clear, and hidden in the Overview", () => {
  it("is clear only with no row and no question in the period", () => {
    expect(waitingIsClear(section())).toBe(true);
    expect(waitingIsClear(section({ reviews_total: 1 }))).toBe(false);
    expect(
      waitingIsClear(
        section({
          answer_time: { asked: 1, answered: 1, unanswered: 0, median_hours: 2, p90_hours: 2 },
        })
      )
    ).toBe(false);
    expect(waitingIsClear(undefined)).toBe(false);
  });

  it("hides only a clear section of a scope with no agent run", () => {
    expect(waitingHidden(section({ agent_runs_seen: false }))).toBe(true);
    expect(waitingHidden(section({ agent_runs_seen: true }))).toBe(false);
    expect(waitingHidden(section({ agent_runs_seen: false, handoffs_total: 1 }))).toBe(false);
  });

  it("the Overview drops a hidden section, and keeps it when it is alone", () => {
    const quiet = section({ agent_runs_seen: false });
    const stuck = { overdue: [], overdue_total: 0 };
    const both = { waiting: quiet, stuck } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(both))).toEqual(["stuck"]);
    const alone = { waiting: quiet } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(alone))).toEqual(["waiting"]);
    const busy = { waiting: section({ agent_runs_seen: true }), stuck } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(busy)).sort()).toEqual(["stuck", "waiting"]);
  });
});
