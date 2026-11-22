/**
 * WS-42 PST-1 — every sentence about a status set comes from one module.
 * Spec: project-docs/specs/projects_settings.md §9.5 and §6.
 */

import { describe, expect, it } from "vitest";

import {
  SPACE_DEFAULT_STATUSES,
  type StatusSetRead,
  exceptionsNote,
  hiddenOverridesLine,
  openStatusesLabel,
  overridePath,
  overrideShared,
  overridesHeading,
  readOnlyLine,
  reachSentence,
  scopeLine,
  scopeSentence,
} from "./statusSetCopy";

const row = (name: string, used_by = 0, path: string[] = ["Metorite"]) => ({
  id: name.toLowerCase().replace(/\s+/g, "-"),
  name,
  path,
  used_by,
});

function read(over: Partial<StatusSetRead> = {}): StatusSetRead {
  return {
    owns: true,
    owner_name: "Metorite",
    owner_kind: "space",
    used_by: 0,
    reach: 0,
    overrides: [],
    hidden_overrides: 0,
    ...over,
  };
}

describe("the scope line, case by case (§9.5)", () => {
  it("case 2: a project that uses the space's set", () => {
    // A team space shows no board, so the server sends reach = used_by.
    const info = read({ owns: false, used_by: 27, reach: 27 });
    expect(scopeSentence(info)).toBe("Uses the statuses of Metorite (shared with 27)");
    expect(scopeLine(info)).toBe(
      "Uses the statuses of Metorite (shared with 27). A change here changes the lanes of 27 projects.",
    );
  });

  it("case 3: a project that uses an ancestor project's set, and the owner's board counts", () => {
    // Own one owns the set, and Own one a and Own one b use it. An edit from
    // Own one a changes all three boards (the PST-1 fix round).
    const info = read({ owns: false, owner_name: "Own one", owner_kind: "project", used_by: 2, reach: 3 });
    expect(scopeLine(info)).toBe(
      "Uses the statuses of Own one (shared with 2). A change here changes the lanes of 3 projects.",
    );
  });

  it("case 1, a project, used by none", () => {
    // Only its own board changes, so there is no second sentence.
    const info = read({ owner_kind: "project", owner_name: "Mobile App", reach: 1 });
    expect(scopeLine(info)).toBe("Uses its own statuses. No other project uses them.");
    expect(reachSentence(info)).toBe("");
  });

  it("case 1, a project, shared", () => {
    const info = read({ owner_kind: "project", owner_name: "Mobile App", used_by: 3, reach: 4 });
    expect(scopeSentence(info)).toBe("Uses its own statuses (shared with 3)");
    expect(scopeLine(info)).toBe(
      "Uses its own statuses (shared with 3). A change here changes the lanes of 4 projects.",
    );
  });

  it("case 1, a space, used by none, by one and by many", () => {
    expect(scopeLine(read())).toBe("No other project uses these statuses.");
    expect(scopeLine(read({ used_by: 1, reach: 1 }))).toBe(
      "1 project uses these statuses. A change here changes the lanes of 1 project.",
    );
    expect(scopeSentence(read({ used_by: 27, reach: 27 }))).toBe("27 projects use these statuses.");
    expect(scopeLine(read({ used_by: 27, reach: 27 }))).toBe(
      "27 projects use these statuses. A change here changes the lanes of 27 projects.",
    );
  });
});

describe("the reach counts every board that changes (PST-1 fix round)", () => {
  it("takes its number from the server's reach, never from used_by", () => {
    // used_by leaves the owner out. A project owner's own board changes too.
    expect(reachSentence(read({ owner_kind: "project", used_by: 2, reach: 3 }))).toBe(
      "A change here changes the lanes of 3 projects.",
    );
    // A folder holds no lanes: the server leaves it out of both numbers.
    expect(reachSentence(read({ used_by: 7, reach: 7 }))).toBe(
      "A change here changes the lanes of 7 projects.",
    );
  });
  it("keeps 'shared with N' at used_by", () => {
    expect(scopeSentence(read({ owns: false, owner_kind: "project", used_by: 2, reach: 3 }))).toBe(
      "Uses the statuses of Metorite (shared with 2)",
    );
  });
  it("falls back to used_by when the server sends no reach", () => {
    expect(reachSentence(read({ used_by: 4, reach: undefined }))).toBe(
      "A change here changes the lanes of 4 projects.",
    );
  });
});

describe("an old gateway, for a few minutes in a release", () => {
  // The read of a gateway before PST-1: no owner_kind, used_by, reach or
  // hidden_overrides, and each override is `{id, name}` only.
  const old = (over: Partial<StatusSetRead> = {}): StatusSetRead => ({
    owns: false,
    owner_name: "Metorite",
    overrides: [{ id: "a", name: "Mobile App" }],
    ...over,
  });
  it("draws each sentence with no count, and never 'undefined'", () => {
    expect(scopeLine(old())).toBe("Uses the statuses of Metorite.");
    expect(scopeLine(old({ owns: true }))).toBe("Uses its own statuses.");
    expect(reachSentence(old())).toBe("");
    expect(hiddenOverridesLine(old())).toBe("");
    expect(overridesHeading(old())).toBe("1 project keeps its own statuses");
    expect(exceptionsNote(old())).toBe("Mobile App keeps its own, so it will not change.");
    const said = [scopeLine(old()), overridesHeading(old()), exceptionsNote(old())].join(" ");
    expect(said).not.toMatch(/undefined|NaN|null/);
  });
  it("draws a row with no path and no count", () => {
    expect(overridePath({})).toBe("");
    expect(overrideShared({})).toBe("");
  });
  it("draws no list when the gateway sends no overrides at all", () => {
    const bare = { owns: true, owner_name: "Metorite" } as unknown as StatusSetRead;
    expect(() => overridesHeading(bare)).not.toThrow();
    expect(overridesHeading(bare)).toBe("No project keeps its own statuses.");
    expect(exceptionsNote(bare)).toBe("");
  });
});

describe("no possessive of an owner name", () => {
  const owners = ["Client Projects", "James", "Ops's Board", "Mobile App"];
  const shapes: Partial<StatusSetRead>[] = [
    { owns: false, used_by: 4 },
    { owns: false, owner_kind: "project", used_by: 1 },
    { owns: true, owner_kind: "project", used_by: 0 },
    { owns: true, owner_kind: "project", used_by: 2 },
    { owns: true, owner_kind: "space", used_by: 9 },
  ];
  it.each(owners)("never writes a possessive of %s", (owner) => {
    for (const shape of shapes) {
      const info = read({ ...shape, owner_name: owner });
      const said = [scopeLine(info), readOnlyLine(info), exceptionsNote(info)].join(" ");
      expect(said).not.toMatch(new RegExp(`${owner}['’]s\\b`));
      expect(said).not.toContain("'s set");
    }
  });
  it("names the owner after 'the statuses of'", () => {
    expect(scopeLine(read({ owns: false, owner_name: "Client Projects", used_by: 2 }))).toContain(
      "the statuses of Client Projects",
    );
  });
});

describe("the pane's list of projects that keep their own set", () => {
  it("says so when there is none", () => {
    expect(overridesHeading(read())).toBe("No project keeps its own statuses.");
    expect(hiddenOverridesLine(read())).toBe("");
  });

  it("counts one and many, with the agreement right", () => {
    expect(overridesHeading(read({ overrides: [row("Mobile App")] }))).toBe(
      "1 project keeps its own statuses",
    );
    expect(overridesHeading(read({ overrides: [row("A"), row("B")] }))).toBe(
      "2 projects keep their own statuses",
    );
  });

  it("counts the hidden ones in the heading and names none of them", () => {
    const info = read({ overrides: [row("Mobile App")], hidden_overrides: 2 });
    expect(overridesHeading(info)).toBe("3 projects keep their own statuses");
    expect(hiddenOverridesLine(info)).toBe("and 2 more that you cannot see");
  });

  it("does not say 'more' with no visible row above it", () => {
    expect(hiddenOverridesLine(read({ hidden_overrides: 1 }))).toBe("You cannot see it.");
    expect(hiddenOverridesLine(read({ hidden_overrides: 4 }))).toBe("You cannot see them.");
    expect(overridesHeading(read({ hidden_overrides: 4 }))).toBe("4 projects keep their own statuses");
  });

  it("draws a row's path, its share and its link name", () => {
    expect(overridePath(row("X", 0, ["…", "Product"]))).toBe("… / Product");
    expect(overridePath(row("X", 0, ["Metorite", "Product"]))).toBe("Metorite / Product");
    expect(overrideShared(row("X", 0))).toBe("");
    expect(overrideShared(row("X", 1))).toBe("Shared with 1 project");
    expect(overrideShared(row("X", 5))).toBe("Shared with 5 projects");
    expect(openStatusesLabel(row("Mobile App"))).toBe("Open the statuses of Mobile App");
  });
});

describe("the dialog's note on the exceptions", () => {
  it("names the visible ones and counts the hidden ones", () => {
    expect(exceptionsNote(read())).toBe("");
    expect(exceptionsNote(read({ overrides: [row("Mobile App")] }))).toBe(
      "Mobile App keeps its own, so it will not change.",
    );
    expect(exceptionsNote(read({ overrides: [row("Mobile App")], hidden_overrides: 2 }))).toBe(
      "Mobile App keeps its own, so it will not change. So do 2 projects that you cannot see.",
    );
    expect(exceptionsNote(read({ hidden_overrides: 1 }))).toBe(
      "1 project that you cannot see keeps its own, so it will not change.",
    );
  });
});

describe("the member who may not edit", () => {
  it("is told where the statuses come from and who to ask", () => {
    expect(readOnlyLine(read({ owner_name: "Client Projects" }))).toBe(
      "Statuses come from Client Projects. Changing them needs the projects:settings:write permission. Ask an organization admin.",
    );
  });
});

describe("the name of the pane section", () => {
  it("is Space default statuses", () => {
    expect(SPACE_DEFAULT_STATUSES).toBe("Space default statuses");
  });
});
