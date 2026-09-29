/** WS-42 PS-2 — the task-type manager's pure rules. */

import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { TYPE_ICON_CHOICES, iconChoiceFor, iconOptionsFor, iconValueFor, sortTypes, typeDeleteBody, typeOrgWide } from "./taskTypes";

describe("task types", () => {
  it("every icon choice resolves, so no card draws the fallback", () => {
    expect(TYPE_ICON_CHOICES.filter((name) => !isKnownIcon(name))).toEqual([]);
  });
  it("puts Epic first, then the rest by name, and ranks no default", () => {
    const rows = [
      { id: "3", name: "Story", project_id: "p" },
      { id: "2", name: "Bug", project_id: "p", is_default: true },
      { id: "1", name: "Epic", project_id: "p", is_system: true },
      { id: "4", name: "Chore", project_id: "p" },
    ];
    expect(sortTypes(rows).map((r) => r.name)).toEqual(["Epic", "Bug", "Chore", "Story"]);
  });
  it("keeps an unlisted icon as itself, so picking the first choice overwrites nothing", () => {
    expect(iconValueFor("star")).toBe("star");
    expect(iconOptionsFor("star")[0]).toBe("star");
    expect(iconValueFor("bug")).toBe("Bug");
    expect(iconOptionsFor("bug")).toEqual([...TYPE_ICON_CHOICES]);
  });
  it("knows an organization row from a space's own", () => {
    expect(typeOrgWide({ project_id: null })).toBe(true);
    expect(typeOrgWide({ project_id: "p" })).toBe(false);
  });
  it("reads a seeded kebab or lower-case icon as its choice (the real walk found it)", () => {
    expect(iconChoiceFor("bug")).toBe("Bug");
    expect(iconChoiceFor("check-square")).toBe("CheckSquare");
    expect(iconChoiceFor("CheckSquare")).toBe("CheckSquare");
    // The seed's own three: Task, Epic and Bug.
    expect(["circle-check", "layers", "bug"].map(iconChoiceFor)).toEqual(["CircleCheck", "Layers", "Bug"]);
    expect(iconChoiceFor(null)).toBe("Circle");
    expect(iconChoiceFor("not-an-icon")).toBe("Circle");
  });
  it("says what a delete does before it happens", () => {
    expect(typeDeleteBody("Bug")).toMatch(/stay, with no type/);
  });
});

describe("the manager offers no default it cannot keep", () => {
  it("has no make-default control and no default copy", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(__dirname, "..", "components", "TypeManager.tsx"), "utf-8");
    expect(src).not.toMatch(/is_default: true/);
    expect(src).not.toMatch(/New tasks (now )?start as/);
  });
});
