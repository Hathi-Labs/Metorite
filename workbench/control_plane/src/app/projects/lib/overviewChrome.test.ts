/**
 * MOB-1 (phone UX audit, 2026-11-03): the Overview was a one-way door on a
 * phone. The tabs live in the View sheet, and the Overview hid the whole tool
 * row with it. These rules decide what an Overview keeps and where a mode
 * command lands.
 */
import { describe, expect, it } from "vitest";

import { availableCommands, type CommandContext } from "./commands";
import { chromeKind } from "./compactChrome";
import { myTasksModeFor } from "./myTasks";

const CTX: CommandContext = {
  mode: "overview",
  hasProject: true,
  isRoot: false,
  filtered: false,
  panelOpen: false,
  panelMode: "side",
  canToggleRail: false,
};

describe("chromeKind", () => {
  it("draws the whole tool row on every canvas but the Overview", () => {
    expect(chromeKind({ overview: false, phone: true, compact: true })).toBe("full");
    expect(chromeKind({ overview: false, phone: false, compact: false })).toBe("full");
  });

  it("keeps the View button on a phone Overview, and nothing else", () => {
    expect(chromeKind({ overview: true, phone: true, compact: true })).toBe("view-only");
  });

  it("keeps the desktop Overview bare, as the action row holds the tabs there", () => {
    expect(chromeKind({ overview: true, phone: false, compact: true })).toBe("none");
    expect(chromeKind({ overview: true, phone: false, compact: false })).toBe("none");
  });

  it("draws no View button when the compact chrome is off", () => {
    expect(chromeKind({ overview: true, phone: true, compact: false })).toBe("none");
  });
});

describe("myTasksModeFor", () => {
  it("maps a project canvas to the same My tasks tab", () => {
    expect(myTasksModeFor("list")).toBe("list");
    expect(myTasksModeFor("overview")).toBe("overview");
  });

  it("has no tab for the Timeline", () => {
    expect(myTasksModeFor("timeline")).toBeNull();
  });
});

describe("view commands in My tasks", () => {
  it("offers List view while the context names the My tasks tabs", () => {
    const ids = availableCommands({ ...CTX, modes: ["overview", "board", "list"] }).map((c) => c.id);
    expect(ids).toContain("view.list");
    expect(ids).not.toContain("view.timeline");
  });

  it("offers List view in My tasks with no project selected", () => {
    const ids = availableCommands({ ...CTX, hasProject: false, modes: ["overview", "list"] }).map((c) => c.id);
    expect(ids).toContain("view.list");
  });

  it("offers no view command with no project and no My tasks tabs", () => {
    const ids = availableCommands({ ...CTX, hasProject: false }).map((c) => c.id);
    expect(ids).not.toContain("view.list");
  });

  it("offers every canvas when the context names no list", () => {
    const ids = availableCommands(CTX).map((c) => c.id);
    expect(ids).toContain("view.timeline");
  });
});
