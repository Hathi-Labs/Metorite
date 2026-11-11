/**
 * Projects · Questions for me — the rules of the list (WS-55 EA-18b,
 * `external_agent_access.md` §12.8).
 *
 * Mutations caught, by test:
 * - the "Everyone" tab shows without the server's count (tab test);
 * - the badge reads the "everyone" count and not "mine" (badge test);
 * - a typed archive request routes to `/activities` (route test);
 * - the browser derives `kind` from the option ids (kind test);
 * - a row with a hidden parent shows a made-up name (group test);
 * - the query names an organization or an address (query test);
 * - the old order of the rows changes inside a group (group test).
 */
import { describe, expect, it } from "vitest";

import { answerPayload } from "./agentRuns";
import {
  QUESTIONS_COPY,
  type QuestionRow,
  ageText,
  answerRoute,
  askedLine,
  badgeCount,
  cappedLine,
  durationText,
  emptyCopy,
  everyoneTabShown,
  groupByProject,
  pathLabel,
  questionActivity,
  questionsCount,
  questionsPage,
  questionsQuery,
  readOnlyLine,
  runLine,
  tabLabel,
  whyTag,
} from "./agentQuestions";

const NOW = Date.parse("2026-11-11T12:00:00Z");

function wire(over: Record<string, unknown> = {}) {
  return {
    run_id: "r1",
    activity_id: "a1",
    task_id: "t1",
    task_number: 7,
    task_title: "Pick a database",
    agent_name: "Claude Code",
    client_via: "mcp:claude-code",
    asked_at: "2026-11-11T09:00:00Z",
    run_started_at: "2026-11-11T08:30:00Z",
    project_path: [
      { id: "p1", name: "Metorite" },
      { id: "p2", name: "AI Studio" },
    ],
    question: {
      kind: "question",
      body: "Which database?",
      options: [
        { id: "pg", label: "Postgres", recommended: true },
        { id: "my", label: "MySQL" },
      ],
      allow_multiple: false,
    },
    can_answer: true,
    why: "created",
    ...over,
  };
}

describe("the wire", () => {
  it("reads a full page", () => {
    const page = questionsPage({
      rows: [wire()],
      counts: { mine: 3, everyone: 8 },
      total: 3,
      offset: 0,
      limit: 100,
      capped: true,
    });
    expect(page.rows).toHaveLength(1);
    expect(page.counts).toEqual({ mine: 3, everyone: 8 });
    expect(page.capped).toBe(true);
    expect(page.rows[0].why).toBe("created");
    expect(page.rows[0].question.options.map((o) => o.id)).toEqual(["pg", "my"]);
  });

  it("reads a shape it cannot read as an empty page", () => {
    for (const bad of [null, undefined, "x", 3, {}, { rows: "no" }]) {
      const page = questionsPage(bad);
      expect(page.rows).toEqual([]);
      expect(page.counts).toEqual({ mine: 0, everyone: null });
      expect(page.capped).toBe(false);
    }
  });

  it("drops a row with no run, no question or no activity", () => {
    const page = questionsPage({
      rows: [wire(), { run_id: "x" }, { ...wire(), question: null }, { ...wire(), activity_id: 3 }],
    });
    expect(page.rows).toHaveLength(1);
  });

  it("keeps the server's kind and never reads it off the options", () => {
    // M: a browser that derives `kind` from option ids calls this a request.
    const row = questionsPage({
      rows: [
        wire({
          question: {
            kind: "question",
            body: "x",
            options: [{ id: "approve", label: "Approve" }, { id: "decline", label: "Decline" }],
            allow_multiple: false,
          },
        }),
      ],
    }).rows[0];
    expect(row.question.kind).toBe("question");
    expect(answerRoute(row)).toBe("activities");
  });

  it("reads an unknown kind and an unknown why as the safe values", () => {
    const row = questionsPage({
      rows: [wire({ why: "boss", question: { kind: "weird", body: null, options: null } })],
    }).rows[0];
    expect(row.why).toBeNull();
    expect(row.question.kind).toBe("question");
    expect(row.question.options).toEqual([]);
    expect(row.can_answer).toBe(true);
  });

  it("treats a missing can_answer as false", () => {
    const raw = wire();
    delete (raw as Record<string, unknown>).can_answer;
    expect(questionsPage({ rows: [raw] }).rows[0].can_answer).toBe(false);
  });
});

describe("the route of an answer", () => {
  it("sends a typed archive request to archive-approval", () => {
    const row = questionsPage({
      rows: [wire({ question: { kind: "archive_request", body: "Archive?", options: [], allow_multiple: false } })],
    }).rows[0];
    expect(answerRoute(row)).toBe("archive-approval");
  });

  it("sends any other question to the activities route", () => {
    expect(answerRoute(questionsPage({ rows: [wire()] }).rows[0])).toBe("activities");
  });
});

describe("tabs and counts", () => {
  it("words the tabs with their counts", () => {
    expect(tabLabel("mine", 3)).toBe("For me · 3");
    expect(tabLabel("everyone", 8)).toBe("Everyone · 8");
    expect(tabLabel("mine", undefined)).toBe("For me");
  });

  it("shows the Everyone tab only when the server sent its count", () => {
    expect(everyoneTabShown({ mine: 1, everyone: null })).toBe(false);
    expect(everyoneTabShown({ mine: 1, everyone: 0 })).toBe(true);
    expect(everyoneTabShown(null)).toBe(false);
  });

  it("feeds the badge from the mine count and hides zero", () => {
    expect(badgeCount({ counts: { mine: 4, everyone: 9 } })).toBe(4);
    expect(badgeCount({ counts: { mine: 0, everyone: 9 } })).toBeUndefined();
    expect(badgeCount(null)).toBeUndefined();
  });

  it("says the capped note under either sort", () => {
    const page = { capped: true, rows: new Array(200).fill(null) as QuestionRow[] };
    expect(cappedLine(page, "oldest")).toBe("Showing the oldest 200");
    expect(cappedLine(page, "newest")).toBe("Showing the newest 200");
    expect(cappedLine({ ...page, capped: false }, "oldest")).toBeNull();
  });

  it("holds the two empty states", () => {
    expect(emptyCopy("mine")).toEqual({
      message: "No question waits on you.",
      hint: "When an agent asks, it shows here.",
    });
    expect(emptyCopy("everyone").message).toBe("No agent waits on anyone.");
  });
});

describe("the query", () => {
  it("names the scope, the sort and the limit, and the project when one is picked", () => {
    expect(questionsQuery({})).toBe("scope=mine&sort=oldest&limit=200");
    expect(questionsQuery({ scope: "everyone", project: "p9", sort: "newest", limit: 0 })).toBe(
      "scope=everyone&project=p9&sort=newest&limit=0",
    );
  });

  it("never names an organization or a person (R5)", () => {
    const q = questionsQuery({ scope: "mine", project: "p1" });
    expect(q).not.toMatch(/org|tenant|email|member|me=/i);
  });
});

describe("the row words", () => {
  it("tags why the reader sees a row", () => {
    expect(whyTag("created")).toBe("You created this task");
    expect(whyTag("assigned")).toBe("Assigned to you");
    expect(whyTag("my_agent")).toBe("Your agent asked");
    expect(whyTag(null)).toBeNull();
  });

  it("words the ages", () => {
    expect(ageText("2026-11-11T09:00:00Z", NOW)).toBe("3 h");
    expect(ageText("2026-11-11T11:48:00Z", NOW)).toBe("12 m");
    expect(ageText("2026-11-11T11:59:50Z", NOW)).toBe("just now");
    expect(ageText("2026-11-08T12:00:00Z", NOW)).toBe("3 d");
    expect(ageText(null, NOW)).toBeNull();
    expect(durationText("2026-11-11T09:19:00Z", NOW)).toBe("2 h 41 m");
    expect(durationText("2026-11-11T11:59:50Z", NOW)).toBe("under a minute");
  });

  it("words the two lines of a row", () => {
    const row = questionsPage({ rows: [wire()] }).rows[0];
    expect(askedLine(row, NOW)).toBe("Claude Code asked 3 h ago");
    expect(runLine(row, NOW)).toBe("Run started 3 h ago · waiting 3 h 0 m");
    expect(askedLine({ agent_name: null, asked_at: null }, NOW)).toBe("An agent asked");
  });

  it("words the read-only line and the counts", () => {
    expect(readOnlyLine("Priya")).toBe(
      "Only Priya, the creator of the task, its assignees, or an owner, admin or manager can answer this",
    );
    expect(questionsCount(1)).toBe("1 question");
    expect(questionsCount(2)).toBe("2 questions");
    expect(QUESTIONS_COPY.allProjects).toBe("All projects");
  });
});

describe("the groups", () => {
  it("groups rows under their project path, in the server's order", () => {
    const rows = questionsPage({
      rows: [
        wire({ activity_id: "a1" }),
        wire({ activity_id: "a2", run_id: "r2", project_path: [{ id: "p1", name: "Metorite" }] }),
        wire({ activity_id: "a3", run_id: "r3" }),
      ],
    }).rows;
    const groups = groupByProject(rows);
    expect(groups.map((g) => g.key)).toEqual(["p1/p2", "p1"]);
    expect(groups[0].rows.map((r) => r.activity_id)).toEqual(["a1", "a3"]);
    expect(pathLabel(groups[0].path)).toBe("Metorite › AI Studio");
  });

  it("gathers rows with an empty path in one group", () => {
    const rows = questionsPage({
      rows: [wire({ project_path: [] }), wire({ activity_id: "a2", run_id: "r2", project_path: [] })],
    }).rows;
    const groups = groupByProject(rows);
    expect(groups).toHaveLength(1);
    expect(pathLabel(groups[0].path)).toBe("");
  });
});

describe("the shared answer", () => {
  it("builds the elicitation the card reads, with the options in the agent's order", () => {
    const row = questionsPage({ rows: [wire()] }).rows[0];
    const act = questionActivity(row);
    expect(act.id).toBe("a1");
    expect(act.type).toBe("elicitation");
    expect(act.signal_metadata?.options?.map((o) => o.id)).toEqual(["pg", "my"]);
    expect(act.signal_metadata?.allow_multiple).toBe(false);
  });

  it("answers by option id through the one payload of the run panel", () => {
    const row = questionsPage({
      rows: [wire({ question: { kind: "question", body: "x", allow_multiple: true, options: [
        { id: "pg", label: "Postgres" }, { id: "my", label: "MySQL" },
      ] } })],
    }).rows[0];
    const body = answerPayload(
      questionActivity(row),
      { Question: { selected: ["Postgres", "MySQL"], freeform: " also SQLite " } },
      "key-1",
    );
    expect(body).toEqual({
      type: "prompt",
      idempotency_key: "key-1",
      selected: ["pg", "my"],
      body: "also SQLite",
    });
  });
});

describe("the expiry of a typed request", () => {
  const req = (expires_at?: string) =>
    questionsPage({
      rows: [wire({ question: { kind: "archive_request", body: "x", options: [], allow_multiple: false, expires_at } })],
    }).rows[0];

  it("reaches the card as the stamp the run panel reads", () => {
    const act = questionActivity(req("2999-01-01T00:00:00Z"));
    expect(act.content_metadata?.request).toEqual({ kind: "archive_task", expires_at: "2999-01-01T00:00:00Z" });
  });

  it("a request with no stamp reads as expired, as the gateway reads it", () => {
    expect(questionActivity(req()).content_metadata?.request?.expires_at).toBeNull();
  });

  it("a free question carries no request", () => {
    expect(questionActivity(questionsPage({ rows: [wire()] }).rows[0]).content_metadata).toBeUndefined();
  });
});
