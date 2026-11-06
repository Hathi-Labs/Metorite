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
    overrides: [],
    hidden_overrides: 0,
    ...over,
  };
}

describe("the scope line, case by case (§9.5)", () => {
  it("case 2: a project that uses the space's set", () => {
    const info = read({ owns: false, used_by: 27 });
    expect(scopeSentence(info)).toBe("Uses the statuses of Metorite (shared with 27)");
    expect(scopeLine(info)).toBe(
      "Uses the statuses of Metorite (shared with 27). A change here changes the lanes of 27 projects.",
    );
  });

  it("case 3: a project that uses an ancestor project's set", () => {
    const info = read({ owns: false, owner_name: "Mobile App", owner_kind: "project", used_by: 1 });
    expect(scopeLine(info)).toBe(
      "Uses the statuses of Mobile App (shared with 1). A change here changes the lanes of 1 project.",
    );
  });

  it("case 1, a project, used by none", () => {
    const info = read({ owner_kind: "project", owner_name: "Mobile App" });
    expect(scopeLine(info)).toBe("Uses its own statuses. No other project uses them.");
    expect(reachSentence(info)).toBe("");
  });

  it("case 1, a project, shared", () => {
    const info = read({ owner_kind: "project", owner_name: "Mobile App", used_by: 3 });
    expect(scopeSentence(info)).toBe("Uses its own statuses (shared with 3)");
    expect(scopeLine(info)).toBe(
      "Uses its own statuses (shared with 3). A change here changes the lanes of 3 projects.",
    );
  });

  it("case 1, a space, used by none, by one and by many", () => {
    expect(scopeLine(read())).toBe("No other project uses these statuses.");
    expect(scopeLine(read({ used_by: 1 }))).toBe(
      "1 project uses these statuses. A change here changes the lanes of 1 project.",
    );
    expect(scopeSentence(read({ used_by: 27 }))).toBe("27 projects use these statuses.");
    expect(scopeLine(read({ used_by: 27 }))).toBe(
      "27 projects use these statuses. A change here changes the lanes of 27 projects.",
    );
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
