/**
 * The project right-click menu (WS-27bg slice 2).
 *
 * Pure assertions, because every rule worth pinning here is
 * wrong-but-plausible rather than visual: ticking the effective state instead
 * of the project's own, offering both Archive and Unarchive, or building the
 * state list from a hand-written array that drifts from the vocabulary.
 */

import { describe, expect, it, vi } from "vitest";

import { PROJECT_STATE_ORDER } from "@/lib/statusAccent";
import type { ProjectRow } from "./api";
import { projectMenuItems } from "./projectMenu";

const project = (over: Partial<ProjectRow> = {}): ProjectRow => ({
  id: "p1",
  name: "Delivery",
  ...over,
});

const handlers = () => ({
  onSetState: vi.fn(),
  onArchive: vi.fn(),
  onUnarchive: vi.fn(),
  onRename: vi.fn(),
});

const labels = (items: ReturnType<typeof projectMenuItems>) =>
  items.filter((i) => i.kind === "item").map((i) => (i as { label: string }).label);

describe("projectMenuItems", () => {
  it("offers every run state, from the vocabulary rather than a copy of it", () => {
    // Built from PROJECT_STATE_ORDER, so a state added to the vocabulary
    // appears without an edit here and one removed cannot linger as an entry
    // that PATCHes a value the server now refuses.
    const items = projectMenuItems(project(), handlers());
    expect(labels(items)).toEqual([
      "Queued",
      "Ongoing",
      "Paused",
      "Stopped",
      "Done",
      "Archive",
    ]);
    expect(PROJECT_STATE_ORDER).toHaveLength(5);
  });

  it("never offers `archived` as a run state — that is the other axis", () => {
    expect(labels(projectMenuItems(project(), handlers()))).not.toContain(
      "Archived"
    );
  });

  it("ticks the project's OWN state, not an inherited one", () => {
    // The menu writes this row's `status`. Ticking a state the project only
    // inherits would present a department's pause as this project's own
    // setting — and the next click would write it as one, turning a derived
    // fact into a stored one (exactly what D-PM-26 prevents).
    const items = projectMenuItems(project({ status: "on_hold" }), handlers());
    const ticked = items.filter(
      (i) => i.kind === "item" && (i as { checked?: boolean }).checked
    );
    expect(ticked).toHaveLength(1);
    expect((ticked[0] as { label: string }).label).toBe("Paused");
  });

  it("ticks Ongoing when the row carries no status at all", () => {
    const items = projectMenuItems(project({ status: null }), handlers());
    const ticked = items.find(
      (i) => i.kind === "item" && (i as { checked?: boolean }).checked
    );
    expect((ticked as { label: string }).label).toBe("Ongoing");
  });

  it("offers Archive OR Unarchive, never both", () => {
    expect(labels(projectMenuItems(project(), handlers()))).toContain("Archive");
    expect(labels(projectMenuItems(project(), handlers()))).not.toContain(
      "Unarchive"
    );

    const filed = project({ archived_at: "2026-08-13T00:00:00Z" });
    expect(labels(projectMenuItems(filed, handlers()))).toContain("Unarchive");
    expect(labels(projectMenuItems(filed, handlers()))).not.toContain("Archive");
  });

  it("does not offer Delete", () => {
    // Not an oversight. DELETE is an unrecoverable cascade over the subtree,
    // every task and every grant, and it has never had a control. Archive is
    // the reversible twin and is the affordance this slice adds; putting both
    // in one new menu is how somebody loses a department.
    expect(labels(projectMenuItems(project(), handlers()))).not.toContain(
      "Delete"
    );
  });

  it("passes the project under the pointer to every handler, and nothing else", () => {
    // The disjointness that actually matters: this menu can reach the project
    // it opened on — never a task, never the page.
    const h = handlers();
    const row = project({ id: "p9" });
    for (const item of projectMenuItems(row, h)) {
      if (item.kind === "item") item.onSelect();
    }
    expect(h.onArchive).toHaveBeenCalledWith(row);
    expect(h.onSetState).toHaveBeenCalledTimes(5);
    for (const call of h.onSetState.mock.calls) expect(call[0]).toBe(row);
  });

  it("sends the STORED value, not the label, to the handler", () => {
    // "Paused" is a display name (D-PM-25); `on_hold` is what the column
    // accepts. Sending the label would 422 on every use.
    const h = handlers();
    const items = projectMenuItems(project(), h);
    const paused = items.find(
      (i) => i.kind === "item" && (i as { label: string }).label === "Paused"
    );
    (paused as { onSelect: () => void }).onSelect();
    expect(h.onSetState).toHaveBeenCalledWith(expect.anything(), "on_hold");
  });

  it("gives every entry a glyph, so the menu is not five identical rows", () => {
    const icons = projectMenuItems(project(), handlers())
      .filter((i) => i.kind === "item")
      .map((i) => (i as { icon?: string }).icon);
    expect(icons.every(Boolean)).toBe(true);
    expect(new Set(icons).size).toBe(icons.length);
  });

  // ── Rename (WS-27bg slice 2 remainder) ────────────────────────────────────

  it("offers Rename only when the surface can raise a rename field", () => {
    // A read-only tree passes no `ui`, and offering an edit nothing can open
    // would be a menu entry that does nothing on click.
    expect(labels(projectMenuItems(project(), handlers()))).not.toContain(
      "Rename"
    );
    const ui = { onBeginRename: vi.fn() };
    expect(labels(projectMenuItems(project(), handlers(), ui))).toContain(
      "Rename"
    );
  });

  it("puts Rename first — it is the only entry that edits the project", () => {
    const items = projectMenuItems(project(), handlers(), {
      onBeginRename: vi.fn(),
    });
    expect(labels(items)[0]).toBe("Rename");
    // And it is separated from the state list, so it does not read as a state.
    expect(items[1].kind).toBe("sep");
  });

  it("Rename raises the field and writes nothing by itself", () => {
    // The menu opens the editor; the WRITE happens when the field is
    // submitted. An entry that PATCHed on click would rename a project to the
    // name it already has, on every accidental selection.
    const h = handlers();
    const ui = { onBeginRename: vi.fn() };
    const items = projectMenuItems(project(), h, ui);
    const rename = items.find(
      (i) => i.kind === "item" && (i as { label: string }).label === "Rename"
    );
    (rename as { onSelect: () => void }).onSelect();
    expect(ui.onBeginRename).toHaveBeenCalledTimes(1);
    expect(h.onRename).not.toHaveBeenCalled();
    expect(h.onSetState).not.toHaveBeenCalled();
  });

  it("offers Rename on an archived project too", () => {
    // Filing a project does not make its name wrong, and the endpoint has
    // never refused the write. Hiding it here would be a rule invented by the
    // menu rather than one the server holds.
    const filed = project({ archived_at: "2026-08-13T00:00:00Z" });
    const items = projectMenuItems(filed, handlers(), {
      onBeginRename: vi.fn(),
    });
    expect(labels(items)).toContain("Rename");
  });
});
