/**
 * Projects · the palette action registry (WS-27ab item 4).
 *
 * The done-when list names three of these outright — every action carries a
 * label and a section, every go-sequence resolves to a route that exists, and
 * no two actions share a key sequence — because each is the kind of defect
 * that ships green: a shortcut into a route that was renamed, two commands
 * fighting over `g t`, a sheet advertising something the keyboard forgot.
 */

import { describe, expect, it, vi } from "vitest";

import { isKnownIcon } from "@/lib/icons";
import { PANES } from "@/lib/nav";

import { PANEL_MODE_ICONS } from "./panelMode";

import {
  COMMANDS,
  COMMAND_SECTIONS,
  type Command,
  type CommandActions,
  type CommandContext,
  VIEW_MODES,
  honoursGroupBy,
  honoursLanes,
  availableCommands,
  isSequenceKey,
  isTypingTarget,
  matchCommands,
  paletteList,
  sequenceLabel,
  shortcutSections,
  stepSequence,
} from "./commands";
import type { Hit } from "./search";

const ctx = (over: Partial<CommandContext> = {}): CommandContext => ({
  mode: "board",
  hasProject: true,
  isRoot: true,
  filtered: false,
  panelOpen: false,
  panelMode: "side",
  canToggleRail: true,
  ...over,
});

/** Every action, recorded rather than performed. */
function spyActions(): CommandActions & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    navigate: (href) => calls.push(`navigate:${href}`),
    setMode: (mode) => calls.push(`setMode:${mode}`),
    setPanelMode: (mode) => calls.push(`setPanelMode:${mode}`),
    clearFilters: () => calls.push("clearFilters"),
    toggleRail: () => calls.push("toggleRail"),
    manage: (what) => calls.push(`manage:${what}`),
    showShortcuts: () => calls.push("showShortcuts"),
  };
}

describe("the registry itself", () => {
  it("every action carries a label, a section and a glyph", () => {
    for (const command of COMMANDS) {
      expect(command.label.trim(), command.id).not.toBe("");
      expect(COMMAND_SECTIONS, command.id).toContain(command.section);
      expect(command.icon.trim(), command.id).not.toBe("");
      expect(typeof command.run, command.id).toBe("function");
    }
  });

  it("every glyph is a real Lucide icon", () => {
    // An unknown name resolves to the `Zap` fallback, so a typo ships as a
    // lightning bolt in the palette rather than as a failure. (Until
    // 2026-08-31 this asserted a mapping in the multi-pack registry; with one
    // pack, existence is the stronger check — the registry could not tell a
    // real name from an invented one.)
    const names = [
      ...COMMANDS.map((c) => c.icon),
      ...Object.values(PANEL_MODE_ICONS),
    ];
    const unknown = names.filter((name) => !isKnownIcon(name));
    expect(unknown, "these are not Lucide icon names").toEqual([]);
  });

  it("every id is unique", () => {
    const ids = COMMANDS.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("no two actions share a key sequence", () => {
    const printed = COMMANDS.filter((c) => c.sequence?.length).map(sequenceLabel);
    expect(printed.length).toBeGreaterThan(0);
    expect(new Set(printed).size, `duplicate: ${printed.join(" · ")}`).toBe(
      printed.length,
    );
  });

  it("no sequence is a prefix of another, so the first one can never be reached", () => {
    const sequences = COMMANDS.filter((c) => c.sequence?.length).map(
      (c) => c.sequence!,
    );
    for (const a of sequences)
      for (const b of sequences) {
        if (a === b || a.length >= b.length) continue;
        expect(
          a.every((step, i) => step === b[i]),
          `${a.join(" ")} is a prefix of ${b.join(" ")}`,
        ).toBe(false);
      }
  });

  it("every go-sequence resolves to a route that exists", () => {
    const hrefs = new Set(PANES.map((p) => p.href));
    const go = COMMANDS.filter((c) => c.section === "Go");
    expect(go.length).toBeGreaterThan(0);
    for (const command of go) {
      expect(command.href, `${command.id} has no route`).toBeTruthy();
      expect(hrefs, `${command.id} → ${command.href}`).toContain(command.href!);
      expect(command.sequence?.[0], command.id).toBe("g");
    }
  });

  it("declares a go-key for every route it means to offer", () => {
    // The safety net in `goCommands()` drops a pane that has been removed from
    // the nav. This is what makes that drop LOUD instead of a quietly shorter
    // help sheet.
    expect(COMMANDS.filter((c) => c.section === "Go")).toHaveLength(8);
  });

  it("carries one view command per mode, and no more", () => {
    const views = COMMANDS.filter((c) => c.section === "View");
    expect(views.map((c) => c.id).sort()).toEqual(
      VIEW_MODES.map((m) => `view.${m.id}`).sort(),
    );
  });

  it("offers each grouping axis only where the canvas draws it", () => {
    // Measured against the canvases on 2026-08-31: TaskBoard reads `lanes`,
    // and TaskBoard/TaskList/TableView/TimelineView read `groupBy` for their
    // columns, section headers, quick-add prefill and bands. Calendar reads
    // neither. Offering a control that cannot act is a dead click — and here a
    // worse one, because setting it still writes the view's config from a
    // surface that shows no effect.
    //
    // ⚠️ **Timeline joined the grouped list on 2026-08-31 (S5)** and did NOT
    // join the laned one. Its x-axis is already spent on time, which is the
    // only reason the canvas exists, so a second grouping axis would nest rows
    // inside rows and say nothing a second level could not say more plainly.
    // `commands.ts` carries the argument.
    expect(VIEW_MODES.map((m) => m.id).filter(honoursLanes)).toEqual(["board"]);
    expect(VIEW_MODES.map((m) => m.id).filter(honoursGroupBy)).toEqual([
      "board",
      "list",
      "table",
      "timeline",
    ]);
  });

  it("never offers lanes where it does not offer grouping", () => {
    // A second axis with no first axis would be a grid with one column.
    for (const { id } of VIEW_MODES) {
      if (honoursLanes(id)) expect(honoursGroupBy(id), id).toBe(true);
    }
  });

  it("labels a go command exactly as the sidebar does", () => {
    const projects = COMMANDS.find((c) => c.href === "/projects");
    expect(projects?.label).toBe(PANES.find((p) => p.href === "/projects")?.label);
  });

  it("runs the action it advertises", () => {
    const actions = spyActions();
    for (const command of COMMANDS) command.run(actions, ctx({ panelMode: "side" }));
    expect(actions.calls).toContain("navigate:/tasks");
    expect(actions.calls).toContain("setMode:table");
    expect(actions.calls).toContain("setPanelMode:full");
    expect(actions.calls).toContain("setPanelMode:peek");
    expect(actions.calls).toContain("clearFilters");
    expect(actions.calls).toContain("manage:lifecycle");
    expect(actions.calls).toContain("showShortcuts");
  });
});

describe("what is offered where", () => {
  it("offers no My work entry — /tasks is the personal lens (D52-D54)", () => {
    const ids = COMMANDS.map((c) => c.id);
    expect(ids).not.toContain("project.mywork");
    expect(ids).not.toContain("project.board");
  });

  it("hides everything project-scoped when no project is selected", () => {
    const ids = availableCommands(ctx({ hasProject: false })).map((c) => c.id);
    expect(ids).not.toContain("project.fields");
    expect(ids).not.toContain("project.tags");
    expect(ids).not.toContain("project.statuses");
    expect(ids).not.toContain("project.lifecycle");
    // ⚠️ `project.import` was the exception here — "import is how an empty
    // workspace stops being empty, so it stays". D52 (2026-08-24) removed the
    // command with the ClickUp integration, so the rule now holds without an
    // exception: nothing project-scoped is offered with no project selected.
    expect(ids).not.toContain("project.import");
  });

  it("offers the lifecycle policy on a root and nowhere else", () => {
    expect(availableCommands(ctx({ isRoot: false })).map((c) => c.id)).not.toContain(
      "project.lifecycle",
    );
  });

  it("offers Clear filters only when something is filtering", () => {
    expect(availableCommands(ctx()).map((c) => c.id)).not.toContain(
      "project.clearFilters",
    );
    expect(availableCommands(ctx({ filtered: true })).map((c) => c.id)).toContain(
      "project.clearFilters",
    );
  });

  it("offers the panel widths only while a panel is open, and only where they move", () => {
    expect(availableCommands(ctx()).map((c) => c.id)).not.toContain("panel.wider");
    const atPeek = availableCommands(
      ctx({ panelOpen: true, panelMode: "peek" }),
    ).map((c) => c.id);
    expect(atPeek).toContain("panel.wider");
    expect(atPeek).not.toContain("panel.narrower");
    const atFull = availableCommands(
      ctx({ panelOpen: true, panelMode: "full" }),
    ).map((c) => c.id);
    expect(atFull).toContain("panel.narrower");
    expect(atFull).not.toContain("panel.wider");
  });

  it("hides the rail toggle where there is no rail", () => {
    expect(availableCommands(ctx({ canToggleRail: false })).map((c) => c.id)).not.toContain(
      "project.rail",
    );
  });
});

describe("matching a typed query", () => {
  const all = availableCommands(ctx());

  it("returns everything applicable for an empty query", () => {
    expect(matchCommands(all, "")).toHaveLength(all.length);
    expect(matchCommands(all, "   ")).toHaveLength(all.length);
  });

  it("puts a label match above a keyword match", () => {
    const hits = matchCommands(all, "tags");
    expect(hits[0].id).toBe("project.tags");
  });

  it("matches the keywords, the section and the sequence", () => {
    expect(matchCommands(all, "cheatsheet").map((c) => c.id)).toContain(
      "help.shortcuts",
    );
    expect(
      matchCommands(availableCommands(ctx({ panelOpen: true })), "panel").map(
        (c) => c.id,
      ),
    ).toContain("panel.wider");
    expect(matchCommands(all, "g p").map((c) => c.id)).toContain("go.p");
  });

  it("is case-insensitive", () => {
    expect(matchCommands(all, "CHEATSHEET").map((c) => c.id)).toContain(
      "help.shortcuts",
    );
  });

  it("returns nothing for a query nothing answers", () => {
    expect(matchCommands(all, "zzzzz")).toEqual([]);
  });
});

describe("key sequences", () => {
  const all = availableCommands(ctx());

  it("walks a two-key sequence and fires on the second", () => {
    const first = stepSequence([], "g", all);
    expect(first).toEqual({ pending: ["g"], command: null, claimed: true });
    const second = stepSequence(first.pending, "t", all);
    expect(second.command?.href).toBe("/tasks");
    expect(second.pending).toEqual([]);
    expect(second.claimed).toBe(true);
  });

  it("fires a one-key sequence immediately", () => {
    const step = stepSequence([], "?", all);
    expect(step.command?.id).toBe("help.shortcuts");
  });

  it("claims nothing for a key that starts no sequence", () => {
    expect(stepSequence([], "q", all)).toEqual({
      pending: [],
      command: null,
      claimed: false,
    });
  });

  it("lets a key restart after a dead prefix, so one typo does not eat the next try", () => {
    const dead = stepSequence(["g"], "z", all);
    expect(dead).toEqual({ pending: [], command: null, claimed: false });
    // …and the restart case: `g` after a dead `g` begins again rather than
    // being swallowed clearing the prefix.
    const restarted = stepSequence(["g"], "g", all);
    expect(restarted).toEqual({ pending: ["g"], command: null, claimed: true });
  });

  it("only runs sequences whose command is applicable here", () => {
    const noProject = availableCommands(ctx({ hasProject: false }));
    expect(stepSequence(["v"], "b", noProject).command).toBeNull();
    expect(stepSequence([], "v", noProject).claimed).toBe(false);
  });

  it("takes single characters without Meta/Ctrl/Alt, and Shift is fine", () => {
    expect(isSequenceKey({ key: "g" })).toBe(true);
    expect(isSequenceKey({ key: "?" })).toBe(true);
    expect(isSequenceKey({ key: "Enter" })).toBe(false);
    expect(isSequenceKey({ key: "k", metaKey: true })).toBe(false);
    expect(isSequenceKey({ key: "k", ctrlKey: true })).toBe(false);
    expect(isSequenceKey({ key: "g", altKey: true })).toBe(false);
  });

  it("keeps its hands off anything being typed into", () => {
    expect(isTypingTarget({ tagName: "INPUT" })).toBe(true);
    expect(isTypingTarget({ tagName: "TEXTAREA" })).toBe(true);
    expect(isTypingTarget({ tagName: "SELECT" })).toBe(true);
    expect(isTypingTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
    expect(isTypingTarget({ tagName: "DIV" })).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe("the palette's list", () => {
  const hit = (id: string): Hit => ({
    id,
    title: id,
    project_id: "p1",
    rank: 1,
  });

  it("heads every section it draws and nothing it does not", () => {
    const { rows } = paletteList(availableCommands(ctx()), []);
    const headings = rows.filter((r) => r.kind === "section").map((r) => r.label);
    expect(headings).toEqual(["Go", "View", "Panel", "Project", "Help"].filter((s) =>
      availableCommands(ctx()).some((c) => c.section === s),
    ));
  });

  it("never lets the cursor land on a heading", () => {
    const { rows, pickable } = paletteList(availableCommands(ctx()), [hit("t1")]);
    for (const index of pickable) expect(rows[index].kind).not.toBe("section");
    expect(pickable).toHaveLength(
      rows.filter((r) => r.kind !== "section").length,
    );
  });

  it("puts the task hits last, under their own heading", () => {
    const { rows } = paletteList(availableCommands(ctx()), [hit("t1"), hit("t2")]);
    expect(rows[rows.length - 1]).toMatchObject({ kind: "task" });
    expect(rows.filter((r) => r.kind === "section").pop()?.label).toBe("Tasks");
  });

  it("draws no Tasks heading when there are no hits", () => {
    const { rows } = paletteList(availableCommands(ctx()), []);
    expect(rows.some((r) => r.kind === "section" && r.label === "Tasks")).toBe(false);
  });

  it("is empty when nothing matched and nothing was found", () => {
    expect(paletteList([], [])).toEqual({ rows: [], pickable: [] });
  });

  it("gives every row a unique key", () => {
    const { rows } = paletteList(availableCommands(ctx()), [hit("t1"), hit("t2")]);
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
  });
});

describe("the shortcuts sheet is generated, never written", () => {
  it("prints every command in the registry exactly once", () => {
    const printed = shortcutSections().flatMap((s) => s.rows.map((r) => r.label));
    expect(printed.sort()).toEqual(COMMANDS.map((c) => c.label).sort());
  });

  it("prints the sequence a command actually carries", () => {
    const go = shortcutSections().find((s) => s.section === "Go")!;
    const projects = go.rows.find((r) => r.label === "Projects")!;
    expect(projects.keys).toBe("g p");
  });

  it("leaves the keys blank for a palette-only command rather than inventing one", () => {
    const panel = shortcutSections().find((s) => s.section === "Panel")!;
    expect(panel.rows.every((r) => r.keys === "")).toBe(true);
  });

  it("cannot drift from the registry, because it IS the registry", () => {
    const fake: Command[] = [
      {
        id: "x",
        label: "Something new",
        section: "Help",
        keywords: [],
        icon: "Star",
        sequence: ["x"],
        run: vi.fn(),
      },
    ];
    expect(shortcutSections(fake)).toEqual([
      { section: "Help", rows: [{ label: "Something new", keys: "x", icon: "Star" }] },
    ]);
  });
});
