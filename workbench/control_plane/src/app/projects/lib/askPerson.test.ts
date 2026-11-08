import { describe, expect, it } from "vitest";

import {
  ASK_COPY,
  EMPTY_DRAFT,
  askBody,
  askFailure,
  draftProblem,
  dueAt,
  refusalOf,
  withdrawFailure,
} from "./askPerson";

const ok = { ...EMPTY_DRAFT, addressee: "priya@x.test", question: "Confirm supplier X?" };

describe("the ask draft", () => {
  it("needs a person, a need, and 0, 2 or 3 choices", () => {
    expect(draftProblem(EMPTY_DRAFT)).toMatch(/person/);
    expect(draftProblem({ ...ok, addressee: "agent:bot@x" })).toMatch(/agent/);
    expect(draftProblem({ ...ok, question: "  " })).toMatch(/need/);
    expect(draftProblem({ ...ok, question: "q".repeat(141) })).toMatch(/140/);
    expect(draftProblem({ ...ok, choices: ["Yes"] })).toMatch(/choices/);
    expect(draftProblem({ ...ok, choices: ["a", "b", "c", "d"] })).toMatch(/choices/);
    expect(draftProblem({ ...ok, choices: ["a", " ", "b"] })).toBeNull();
    expect(draftProblem(ok)).toBeNull();
  });

  it("builds a body that names nobody who asks", () => {
    const body = askBody({ ...ok, choices: ["Supplier X", "", "Supplier Y"], due: "2099-01-05" });
    expect(Object.keys(body).sort()).toEqual(["addressee", "choices", "due_at", "question"]);
    expect(body.choices).toEqual([
      { key: "c1", label: "Supplier X" },
      { key: "c2", label: "Supplier Y" },
    ]);
    expect(body.due_at).toMatch(/Z$/);
    expect(askBody(ok)).toEqual({ addressee: "priya@x.test", question: "Confirm supplier X?" });
  });

  it("reads a day as the end of that day, with a zone", () => {
    expect(dueAt("")).toBeNull();
    expect(dueAt("not a date")).toBeNull();
    const at = new Date(dueAt("2099-01-05") as string);
    expect(at.getHours()).toBe(23);
    expect(at.getMinutes()).toBe(59);
  });
});

describe("each refusal shows its text", () => {
  const cases: [number, unknown, RegExp][] = [
    [422, { error: "addressee_cannot_open", message: "x" }, /Share the project/],
    [422, { error: "self_ask", message: "x" }, /yourself/],
    [409, { error: "ask_open", message: "x" }, /already wait/],
    [429, { error: "ask_limit", message: "x" }, /enough people today/],
    [422, { error: "bad_due", message: "x" }, /day that is ahead/],
    [403, "A person asks a person.", /Metorite/],
    [404, "Task not found", /Reload/],
    [500, null, /Could not send/],
  ];
  it.each(cases)("%s", (status, detail, pattern) => {
    expect(askFailure(status, detail)).toMatch(pattern);
  });

  it("says the personal case before any send", () => {
    expect(ASK_COPY.personal).toBe("Move it to a project to ask someone");
  });

  it("reads the gateway detail in both shapes", () => {
    expect(refusalOf({ error: "a", message: "b" })).toEqual({ code: "a", message: "b" });
    expect(refusalOf("text")).toEqual({ code: "", message: "text" });
    expect(refusalOf(undefined)).toEqual({ code: "", message: "" });
  });

  it("explains a withdraw that cannot run", () => {
    expect(withdrawFailure(409, "ask_closed").reload).toBe(true);
    expect(withdrawFailure(404, null).text).toMatch(/not yours/);
    expect(withdrawFailure(500, null).reload).toBe(false);
  });
});
