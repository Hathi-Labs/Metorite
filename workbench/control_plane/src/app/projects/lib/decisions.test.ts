/**
 * WS-ALN ALN-2b — the rules of the decision block (`decisions.ts`), and the
 * wiring of its panel. Spec: `project_alignment.md` §3.2.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  answerBody,
  answerFailure,
  expiredLine,
  hasDecisionBlock,
  readTaskDecisions,
  rowsToShow,
  stateNow,
  waitingLine,
} from "./decisions";

const OPTIONS = [
  { key: "yes", label: "Yes, go ahead" },
  { key: "no", label: "No, stop" },
];
const NOW = Date.parse("2026-11-04T10:00:00Z");

function row(over: Record<string, unknown> = {}) {
  return {
    id: "d-1",
    subject_id: "t-1",
    decider: "me@x.test",
    options: OPTIONS,
    question: "Ship it?",
    asked_by: "ravi@x.test",
    state: "open",
    created_at: "2026-11-04T08:00:00Z",
    expires_at: "2026-11-05T08:00:00Z",
    answered_by: null,
    answer_key: null,
    answer_reason: null,
    answered_at: null,
    ...over,
  };
}

describe("readTaskDecisions", () => {
  it("reads the rows and the people who are waited on", () => {
    const d = readTaskDecisions({
      decisions: [row()],
      waiting_on: [{ decider: "sam@x.test", expires_at: "2026-11-05T08:00:00Z", source: null }],
    });
    expect(d.decisions).toHaveLength(1);
    expect(d.decisions[0].options).toEqual(OPTIONS);
    expect(d.waiting_on).toEqual([
      { decider: "sam@x.test", expires_at: "2026-11-05T08:00:00Z", source: null },
    ]);
    expect(d.asks).toBeNull();
  });

  it("leaves out a bad row and never throws", () => {
    const d = readTaskDecisions({
      decisions: [row({ state: "weird" }), { nope: 1 }, row({ id: "d-2" })],
      waiting_on: [{ nope: 1 }],
    });
    expect(d.decisions.map((r) => r.id)).toEqual(["d-2"]);
    expect(d.waiting_on).toEqual([]);
    expect(readTaskDecisions(null)).toEqual({ decisions: [], waiting_on: [], asks: null });
  });
});

describe("the state now", () => {
  it("draws an open row past its expiry as expired", () => {
    const [open] = readTaskDecisions({ decisions: [row()] }).decisions;
    expect(stateNow(open, NOW)).toBe("open");
    expect(stateNow(open, Date.parse("2026-11-05T08:00:01Z"))).toBe("expired");
  });

  it("keeps an answered row answered", () => {
    const [done] = readTaskDecisions({ decisions: [row({ state: "answered", answer_key: "yes" })] })
      .decisions;
    expect(stateNow(done, Date.parse("2027-01-01T00:00:00Z"))).toBe("answered");
  });

  it("shows every open row first and the newest of the rest", () => {
    const d = readTaskDecisions({
      decisions: [
        row({ id: "a", state: "answered", answer_key: "no" }),
        row({ id: "b" }),
        row({ id: "c", state: "expired" }),
      ],
    });
    expect(rowsToShow(d.decisions, NOW).map((r) => r.id)).toEqual(["b", "a"]);
  });
});

describe("the block shows only when it has something to say", () => {
  it("is empty with no row and nobody waited on", () => {
    expect(hasDecisionBlock(null, NOW)).toBe(false);
    expect(hasDecisionBlock({ decisions: [], waiting_on: [], asks: null }, NOW)).toBe(false);
  });

  it("shows for a waiting decision on another member", () => {
    const d = {
      decisions: [],
      waiting_on: [{ decider: "sam@x.test", expires_at: null, source: null }],
      asks: null,
    };
    expect(hasDecisionBlock(d, NOW)).toBe(true);
  });
});

describe("the words", () => {
  const name = (e: string) => e.split("@")[0];

  it("says that a decision waits, and on whom", () => {
    const line = waitingLine({ decider: "sam@x.test", expires_at: "2026-11-05T08:00:00Z", source: null }, name);
    expect(line.startsWith("A decision waits on sam.")).toBe(true);
    expect(line).toContain("It closes");
  });

  it("says that a person has an ask, with no question in it", () => {
    const line = waitingLine({ decider: "sam@x.test", expires_at: null, source: "person" }, name);
    expect(line).toBe("sam has an ask.");
  });

  it("never names the question for a person who waits", () => {
    const line = waitingLine({ decider: "sam@x.test", expires_at: null, source: null }, name);
    expect(line).toBe("A decision waits on sam.");
  });

  it("says what happened and what to do next for an expired row", () => {
    const [r] = readTaskDecisions({ decisions: [row({ state: "expired" })] }).decisions;
    expect(expiredLine(r, name)).toContain("expired");
    expect(expiredLine(r, name)).toContain("Ask ravi");
  });

  it("gives failure copy with a next step for each refusal", () => {
    for (const [status, message] of [
      [409, "decision_expired"],
      [409, "decision_answered"],
      [404, "Decision not found"],
      [403, "A person answers a decision."],
      [500, "boom"],
    ] as const) {
      const f = answerFailure(status, message);
      expect(f.text.length).toBeGreaterThan(20);
      expect(f.text).not.toMatch(/Jev|boom|decision_/);
    }
    expect(answerFailure(409, "decision_expired").reload).toBe(true);
    expect(answerFailure(500, "x").reload).toBe(false);
  });
});

describe("the one answer route", () => {
  it("posts the key and the trimmed reason, and names nobody", () => {
    expect(answerBody("yes", "  ship it ")).toEqual({ answer_key: "yes", reason: "ship it" });
    expect(answerBody("no", "   ")).toEqual({ answer_key: "no" });
    expect(Object.keys(answerBody("yes", "r"))).toEqual(["answer_key", "reason"]);
  });

  it("is the only write the panel makes", () => {
    const here = join(__dirname, "..");
    const panel = readFileSync(join(here, "components", "DecisionPanel.tsx"), "utf8");
    const api = readFileSync(join(here, "lib", "api.ts"), "utf8");
    expect(panel).toContain("decisionsApi.answer(");
    expect(panel).not.toContain("fetch(");
    expect(api).toContain("`decisions/${encodeURIComponent(decisionId)}/answer`");
    // The block sits in the task body, so both hosts show it.
    const body = readFileSync(join(here, "components", "TaskBody.tsx"), "utf8");
    expect(body).toContain("<DecisionPanel");
  });
});
