import { describe, expect, it } from "vitest";

import {
  assigneeLabel,
  classify,
  normalize,
  parseAssignees,
  withAssignee,
  withoutAssignee,
  describePickerRow,
  pickerGroups,
} from "./assignees";

describe("normalize", () => {
  it("matches the server: trimmed and lowercased", () => {
    expect(normalize("  Priya@X.com ")).toBe("priya@x.com");
  });
});

describe("classify", () => {
  it("recognises the agent prefix", () => {
    expect(classify("agent:researcher")).toBe("agent");
    expect(classify("Agent:Researcher")).toBe("agent");
  });

  it("does not call a bare prefix an agent", () => {
    // `agent:` names nobody, and treating it as an agent would show a chip for
    // a dispatch target that cannot exist.
    expect(classify("agent:")).toBe("unknown");
  });

  it("recognises an email", () => {
    expect(classify("priya@fracktal.in")).toBe("person");
  });

  it("flags something that is neither, without rejecting it", () => {
    // A hint, not a rule — the server accepts any non-empty string, and the
    // failure worth surfacing is a typo that assigns work to nobody.
    expect(classify("priya")).toBe("unknown");
    expect(classify("priya@fracktal")).toBe("unknown");
  });
});

describe("parseAssignees", () => {
  it("splits on commas, semicolons and newlines", () => {
    expect(parseAssignees("a@x.com, b@x.com; c@x.com\nd@x.com")).toEqual([
      "a@x.com",
      "b@x.com",
      "c@x.com",
      "d@x.com",
    ]);
  });

  it("does NOT split on spaces", () => {
    // A pasted list arrives as `Priya <priya@x.com>` often enough that
    // splitting on whitespace would shred it into tokens assigning nobody.
    expect(parseAssignees("priya <priya@x.com>")).toEqual(["priya <priya@x.com>"]);
  });

  it("drops empties left by trailing separators", () => {
    expect(parseAssignees("a@x.com,,  ,\n")).toEqual(["a@x.com"]);
  });

  it("dedupes after normalising, keeping first-seen order", () => {
    expect(parseAssignees("B@x.com, a@x.com, b@X.com")).toEqual([
      "b@x.com",
      "a@x.com",
    ]);
  });

  it("keeps agent targets alongside people", () => {
    expect(parseAssignees("agent:Researcher, priya@x.com")).toEqual([
      "agent:researcher",
      "priya@x.com",
    ]);
  });
});

describe("withAssignee", () => {
  it("appends a new assignee", () => {
    expect(withAssignee(["a@x.com"], "B@x.com")).toEqual(["a@x.com", "b@x.com"]);
  });

  it("returns the SAME array when the assignee is already there", () => {
    // Identity is the signal a caller uses to skip the PUT — and skipping it
    // is what stops a re-assert emitting pm.task.assigned and re-dispatching
    // an agent run.
    const current = ["a@x.com"];
    expect(withAssignee(current, "A@X.com")).toBe(current);
  });

  it("ignores blank input", () => {
    const current = ["a@x.com"];
    expect(withAssignee(current, "   ")).toBe(current);
  });
});

describe("withoutAssignee", () => {
  it("removes case-insensitively", () => {
    expect(withoutAssignee(["a@x.com", "b@x.com"], "A@X.com")).toEqual(["b@x.com"]);
  });

  it("is a no-op for somebody absent", () => {
    expect(withoutAssignee(["a@x.com"], "z@x.com")).toEqual(["a@x.com"]);
  });
});

describe("assigneeLabel", () => {
  it("shows an agent by name, without the prefix", () => {
    expect(assigneeLabel("agent:Researcher")).toBe("researcher");
  });

  it("shows a person by address", () => {
    expect(assigneeLabel("Priya@x.com")).toBe("priya@x.com");
  });
});

describe("describePickerRow (WS-28e)", () => {
  const base = {
    assignee: "priya@fracktal.in",
    name: "Priya",
    kind: "person" as const,
    has_login: true,
    top_skills: [],
    warnings: [],
  };

  it("says when the task will not notify them — BEFORE assigning", () => {
    // D-PC-12: a contractor can hold the task and cannot sign in to see it.
    // Silence here becomes "why didn't they do it" a week later.
    expect(describePickerRow({ ...base, has_login: false })).toContain(
      "no login — cannot see the task"
    );
  });

  it("an agent without a login line is not called out", () => {
    expect(
      describePickerRow({ ...base, kind: "agent", has_login: false })
    ).toBe("");
  });

  it("reads the load with its unestimated caveat", () => {
    expect(
      describePickerRow({
        ...base,
        load: { open_tasks: 4, estimated_hours: 50, unestimated: 2 },
        contracted_hours: 40,
      })
    ).toBe("4 open · 50h of 40h · 2 unestimated");
  });

  it("carries the warnings verbatim — shown, never enforced", () => {
    expect(
      describePickerRow({ ...base, warnings: ["Away (holiday) until 2026-08-20"] })
    ).toContain("Away (holiday) until 2026-08-20");
  });

  it("is empty when there is nothing to say", () => {
    expect(describePickerRow(base)).toBe("");
  });
});

describe("pickerGroups — the crash that blanked the whole panel", () => {
  const row = (name: string) =>
    ({ assignee: `${name}@x.io`, name, kind: "person", has_login: true,
       top_skills: [], warnings: [] }) as never;

  it("groups people and agents", () => {
    const groups = pickerGroups({
      people: [row("Ana")], agents: [row("triage")],
    } as never);
    expect(groups.map((g) => g.heading)).toEqual(["People", "Agents"]);
  });

  it("drops an empty group rather than drawing an empty heading", () => {
    const groups = pickerGroups({ people: [row("Ana")], agents: [] } as never);
    expect(groups.map((g) => g.heading)).toEqual(["People"]);
  });

  it("🔴 survives a response missing its keys entirely", () => {
    // The measured crash: the component read `res.people.length`, a response
    // of the wrong shape threw, and the throw escaped to the layout boundary
    // so the ENTIRE task panel rendered empty. An older server, a proxied
    // error page or a shape change all produce this.
    expect(pickerGroups({} as never)).toEqual([]);
    expect(pickerGroups({ people: null, agents: undefined } as never)).toEqual([]);
  });

  it("survives a response whose groups are not arrays", () => {
    expect(pickerGroups({ people: "nope", agents: 7 } as never)).toEqual([]);
  });

  it("returns nothing before anything has loaded", () => {
    expect(pickerGroups(null)).toEqual([]);
  });
});
