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
import { type ProjectMenuUi, projectMenuItems } from "./projectMenu";

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

  // ── Delete (H-8) ─────────────────────────────────────────────

  it("does not offer Delete to a surface that supplied no handler", () => {
    // A read-only tree gets a menu of exactly what it can do — the rule every
    // optional in this module follows. The entry is dropped, not greyed.
    expect(labels(projectMenuItems(project(), handlers()))).not.toContain(
      "Delete"
    );
  });

  it("offers Delete LAST, and behind a separator", () => {
    // The ranking IS the design (spec §9.8.4): archive is the default
    // affordance and delete is the harder one to reach. An entry that drifted
    // up next to Archive would put an unrecoverable cascade beside its
    // reversible twin, which is the arrangement this ordering exists to avoid.
    const items = projectMenuItems(project(), { ...handlers(), onDelete: vi.fn() });
    const last = items[items.length - 1];
    expect(last.kind).toBe("item");
    expect((last as { label: string }).label).toBe("Delete");
    expect(items[items.length - 2]?.kind).toBe("sep");
  });

  it("⚠️ separates Delete even on a menu with no other optional groups", () => {
    // The failing arrangement this pins: a folder's menu has no create
    // options and no run state, so appending Delete to the archive GROUP
    // instead of its own would have drawn Archive and Delete adjacent, with
    // no rule between them — on the one menu where the mistake is invisible
    // in the common case.
    const items = projectMenuItems(
      project(),
      { ...handlers(), onDelete: vi.fn() },
      undefined,
      "folder"
    );
    const names = labels(items);
    expect(names).toEqual(["Archive", "Delete"]);
    const archiveAt = items.findIndex(
      (i) => i.kind === "item" && (i as { label: string }).label === "Archive"
    );
    const deleteAt = items.findIndex(
      (i) => i.kind === "item" && (i as { label: string }).label === "Delete"
    );
    expect(items[archiveAt + 1]?.kind).toBe("sep");
    expect(deleteAt).toBe(archiveAt + 2);
  });

  it("marks Delete as destructive, and marks nothing else", () => {
    // `ContextMenu` draws `danger` in the destructive colour. A second entry
    // carrying it would spend the one signal the menu has.
    const items = projectMenuItems(project(), { ...handlers(), onDelete: vi.fn() });
    const dangerous = items
      .filter((i) => i.kind === "item" && (i as { danger?: boolean }).danger)
      .map((i) => (i as { label: string }).label);
    expect(dangerous).toEqual(["Delete"]);
  });

  it("hands Delete the project under the pointer, and does not delete on build", () => {
    // Building a menu must never perform the act. The handler is called on
    // select and at no other time.
    const onDelete = vi.fn();
    const row = project({ id: "p9" });
    const items = projectMenuItems(row, { ...handlers(), onDelete });
    expect(onDelete).not.toHaveBeenCalled();
    const entry = items.find(
      (i) => i.kind === "item" && (i as { label: string }).label === "Delete"
    );
    (entry as { onSelect: () => void }).onSelect();
    expect(onDelete).toHaveBeenCalledWith(row, "project");
  });

  it("⚠️ hands Delete the row's LEVEL, so a space is not called a project", () => {
    // Owner directive 2026-08-31: a space is not a project. The row alone
    // cannot say which it is — a level is kind PLUS depth — so the menu, which
    // knows the depth, has to carry it. Without this the confirmation titles
    // the largest destructive act in the product "Delete project".
    for (const level of ["space", "folder", "project", "subproject"] as const) {
      const onDelete = vi.fn();
      const row = project();
      const items = projectMenuItems(row, { ...handlers(), onDelete }, undefined, level);
      const entry = items.find(
        (i) => i.kind === "item" && (i as { label: string }).label === "Delete"
      );
      (entry as { onSelect: () => void }).onSelect();
      expect(onDelete).toHaveBeenCalledWith(row, level);
    }
  });

  it("offers Delete on an archived project too", () => {
    // Filing a project is not a reason to lose the ability to remove it, and
    // an archived project is the likeliest one somebody wants gone.
    const filed = project({ archived_at: "2026-09-01T00:00:00Z" });
    const names = labels(projectMenuItems(filed, { ...handlers(), onDelete: vi.fn() }));
    expect(names).toContain("Unarchive");
    expect(names).toContain("Delete");
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

/**
 * The row menu, widened (owner directive 2026-09-06).
 *
 * The owner asked for the same list from a right-click AND from a three-dot
 * button, carrying the actions the product actually has. What is worth pinning
 * is not that the entries exist — it is the two rules that decide WHICH row
 * gets them, both of which are wrong-but-plausible in exactly the way a
 * hand-written menu gets wrong.
 */
describe("projectMenuItems · the row menu's scope", () => {
  const ui = (over: Partial<ProjectMenuUi> = {}): ProjectMenuUi => ({
    onBeginRename: vi.fn(),
    onMove: vi.fn(),
    onOpenSettings: vi.fn(),
    onCreate: vi.fn(),
    createOptions: [
      { kind: "project", label: "New project", level: "project" },
    ],
    onManageStatuses: vi.fn(),
    onManageFields: vi.fn(),
    onManageTags: vi.fn(),
    onManageLifecycle: vi.fn(),
    ...over,
  });

  // ⚠️ "Statuses" is NOT in this list any more, and that is the point.
  // Migration 196 (2026-09-06) let a project own its own lanes, so statuses
  // stopped being root-scoped while these four did not.
  const LEVELS = ["space", "folder", "project", "subproject"] as const;

  it("keeps Space settings and Lifecycle on a SPACE row, and nowhere else", () => {
    // These two really are properties OF the space — its name and icon, its
    // archive policy — rather than of a set it owns, so a subproject has
    // nothing to show.
    const spaceOnly = ["Space settings", "Lifecycle policy"];
    const onSpace = labels(projectMenuItems(project(), handlers(), ui(), "space"));
    for (const label of spaceOnly) expect(onSpace).toContain(label);

    for (const level of LEVELS.filter((l) => l !== "space")) {
      const elsewhere = labels(
        projectMenuItems(project(), handlers(), ui(), level)
      );
      for (const label of spaceOnly) expect(elsewhere).not.toContain(label);
    }
  });

  it("offers Tags and Custom fields on EVERY level, including a leaf", () => {
    // ⚠️ This reverses what this file asserted until 2026-09-20, so the old
    // reasoning is worth answering rather than deleting. It was: tags and
    // fields are one set per space, so offering their editors on a leaf
    // "would say the leaf has a set of its own, and the next person would
    // file a bug when editing one leaf changed its siblings."
    //
    // The scope claim is right and the conclusion did not follow. Hiding the
    // door does not teach the model — it teaches that the screen does not
    // exist, which is what the owner reported: they had been right-clicking
    // a subproject looking for somewhere to edit tags.
    //
    // What answers the ambiguity is the dialog, which both managers already
    // had: "Shared by <space> and everything under it". The page resolves
    // the space before opening (`manageRoot` + `spaceOf`), so a leaf's menu
    // opens the space's registry and the first line says whose it is.
    //
    // The same mistake in the same file, one vocabulary over: Statuses sat
    // in the space-only block until 2026-09-16 and was reported the same
    // way. Twice is a pattern, not a coincidence.
    for (const level of LEVELS) {
      const items = labels(projectMenuItems(project(), handlers(), ui(), level));
      expect(items, `Tags missing on a ${level}`).toContain("Tags");
      expect(items, `Custom fields missing on a ${level}`).toContain(
        "Custom fields"
      );
    }
  });

  it("offers Statuses on every level that can OWN a set, not just a space", () => {
    // ⚠️ The regression this test exists for. Statuses sat in the `isSpace`
    // block with the other four until 2026-09-16, ten days after migration 196
    // made `owns_statuses` a per-project flag. The header overflow menu and the
    // command palette both acted on the selected node, so the feature worked —
    // from two of its three doors. The row somebody actually right-clicks was
    // the one still refusing (owner report).
    for (const level of ["space", "project", "subproject"] as const) {
      const items = labels(projectMenuItems(project(), handlers(), ui(), level));
      expect(items, `Statuses missing on a ${level}`).toContain("Statuses");
    }
  });

  it("offers Statuses on a FOLDER never, because a folder holds no tasks", () => {
    // A folder groups. A lane set on one would describe a board that cannot
    // exist, which is the same line `hasRunState` draws.
    const onFolder = labels(
      projectMenuItems(project(), handlers(), ui(), "folder")
    );
    expect(onFolder).not.toContain("Statuses");
  });

  it("gives a subproject its vocabularies WITHOUT the space's own screens", () => {
    // The split, asserted in one place: a subproject reaches all three
    // vocabularies, and neither of the two screens that describe the SPACE
    // itself.
    const onSub = labels(
      projectMenuItems(project(), handlers(), ui(), "subproject")
    );
    expect(onSub).toContain("Statuses");
    expect(onSub).toContain("Tags");
    expect(onSub).toContain("Custom fields");
    expect(onSub).not.toContain("Space settings");
    expect(onSub).not.toContain("Lifecycle policy");
  });

  it("drops a screen the surface cannot open, rather than greying it", () => {
    // An absent handler means the page did not wire that dialog. A disabled
    // row would promise a screen no click can reach.
    const partial = labels(
      projectMenuItems(
        project(),
        handlers(),
        {
          onBeginRename: vi.fn(),
          onManageTags: vi.fn(),
        },
        "space"
      )
    );
    expect(partial).toContain("Tags");
    expect(partial).not.toContain("Statuses");
    expect(partial).not.toContain("Custom fields");
    expect(partial).not.toContain("Space settings");
    expect(partial).not.toContain("Lifecycle policy");
  });

  it("creates through the grammar's OWN option, never a kind it rebuilds", () => {
    // A project and a subproject are both `kind: "project"`, so the kind alone
    // cannot say which word the create form should show. Passing the whole
    // option through is what stops the form saying "New project" while it
    // creates a subproject — a bug this tree has already had once.
    const option = {
      kind: "folder",
      label: "New folder",
      level: "folder",
    } as const;
    const surface = ui({ createOptions: [option] });
    const items = projectMenuItems(project(), handlers(), surface, "project");
    const entry = items.find(
      (i) => i.kind === "item" && (i as { label: string }).label === "New folder"
    );
    (entry as { onSelect: () => void }).onSelect();
    expect(surface.onCreate).toHaveBeenCalledWith(option);
  });

  it("drops the Create heading when the grammar allows no child", () => {
    // A subproject is the grammar's floor (migration 193), so
    // `childCreationOptions` returns nothing for it. A heading over an empty
    // list is the shape this menu must not take.
    const items = projectMenuItems(
      project(),
      handlers(),
      ui({ createOptions: [] }),
      "subproject"
    );
    expect(
      items.some((i) => i.kind === "label" && i.label === "Create")
    ).toBe(false);
  });

  it("never draws a rule against nothing, at any level", () => {
    // Every group in this menu is conditional, so the separators cannot be
    // pushed inline after each block — a subproject (no create options, no
    // space screens) would open on a rule, and carry two more in a row.
    for (const level of LEVELS) {
      for (const surface of [undefined, ui(), ui({ createOptions: [] })]) {
        const items = projectMenuItems(project(), handlers(), surface, level);
        const where = `${level}/${surface ? "editable" : "read-only"}`;

        expect(items.length, where).toBeGreaterThan(0);
        expect(items[0].kind, where).not.toBe("sep");
        expect(items[items.length - 1].kind, where).not.toBe("sep");

        for (let i = 1; i < items.length; i += 1) {
          expect(
            items[i].kind === "sep" && items[i - 1].kind === "sep",
            `${where} — two rules in a row at ${i}`
          ).toBe(false);
        }

        // A label is a heading, and a heading with no entry under it names an
        // empty group.
        items.forEach((item, i) => {
          if (item.kind !== "label") return;
          expect(items[i + 1]?.kind, `${where} — empty heading ${item.label}`).toBe(
            "item"
          );
        });
      }
    }
  });

  it("gives the space menu no two entries the same glyph", () => {
    // The longest the menu ever gets, and the one where a duplicate would
    // actually mislead: "Lifecycle policy" is about archiving, and drawing it
    // with the Archive glyph would put two archive icons in one list.
    const icons = projectMenuItems(project(), handlers(), ui(), "space")
      .filter((i) => i.kind === "item")
      .map((i) => (i as { icon?: string }).icon);
    expect(icons.every(Boolean)).toBe(true);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("still refuses Delete, at every level and with every handler wired", () => {
    for (const level of LEVELS) {
      expect(
        labels(projectMenuItems(project(), handlers(), ui(), level))
      ).not.toContain("Delete");
    }
  });
});
