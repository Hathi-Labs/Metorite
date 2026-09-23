/**
 * Selection parity with /projects — the fence for the rule this slice
 * introduces (R7).
 *
 * The rule, stated once: **selection is always available on /tasks, and
 * `selectMode` decides only whether the bulk bar is up.** It is no longer a
 * mode you enter, and it never again changes what a click means. A row's click
 * opens the task; the checkbox beside it selects. That is /projects' grammar,
 * which the owner ruled canonical on 2026-08-10.
 *
 * Two halves, because the rule can be broken from two directions:
 *
 *  1. **The store** can let `selectMode` drift from "something is selected",
 *     at which point it is a mode again and the bulk bar starts lying about
 *     what a bulk action would hit.
 *  2. **A surface** can start consulting `selectMode` again — which is exactly
 *     how the modal behaviour was written the first time, one `selectMode ? …`
 *     at a time. So the three list surfaces are asserted not to read it at all.
 *
 * The second half is a source-text check for the same reason `conformance.test`
 * and `sharedTaskUi.test` are: there is no DOM test harness in this tree, and a
 * regression here is invisible in review — it renders fine, it just means
 * something different.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { beforeEach, describe, expect, it } from "vitest";

import { useTaskStore } from "./taskStore";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

/** The surfaces this slice owns: every one draws rows and a selection. */
const SURFACES = {
  "components/ItemList.tsx": read("../components/ItemList.tsx"),
  "components/TaskListGrouped.tsx": read("../components/TaskListGrouped.tsx"),
  "components/FlatList.tsx": read("../components/FlatList.tsx"),
};

const ROWS = ["a", "b", "c", "d"];

describe("the store cannot turn selectMode back into a mode", () => {
  beforeEach(() => {
    useTaskStore.getState().clearSelection();
  });

  /** The whole invariant, checked after every transition below. */
  const invariant = () => {
    const s = useTaskStore.getState();
    expect(
      s.selectMode,
      "selectMode must be exactly `selectedIds.size > 0` — it means 'the bulk " +
        "bar is up', nothing else. Route the write through applySelection().",
    ).toBe(s.selectedIds.size > 0);
  };

  it("has no way to enter a mode: the setter is gone", () => {
    expect(
      "setSelectMode" in useTaskStore.getState(),
      "setSelectMode is the entry to a mode that no longer exists. Selection " +
        "is available unconditionally; nothing turns it on.",
    ).toBe(false);
  });

  it("rises with the first pick and falls with the last", () => {
    const { toggleSelected } = useTaskStore.getState();
    invariant();
    expect(useTaskStore.getState().selectMode).toBe(false);

    toggleSelected("b", false, ROWS);
    invariant();
    expect(useTaskStore.getState().selectMode).toBe(true);

    toggleSelected("b", false, ROWS);
    invariant();
    expect(useTaskStore.getState().selectMode).toBe(false);
  });

  it("shift-click still sweeps the shared range, with nothing to enter first", () => {
    const { toggleSelected } = useTaskStore.getState();
    toggleSelected("b", false, ROWS);
    toggleSelected("d", true, ROWS);
    expect([...useTaskStore.getState().selectedIds].sort()).toEqual(["b", "c", "d"]);
    // The anchor stays put, so a second shift-click widens the same range.
    expect(useTaskStore.getState().selectAnchor).toBe("b");
    invariant();
  });

  it("select-all takes the VISIBLE set, and unticks to nothing", () => {
    const { selectAllVisible } = useTaskStore.getState();
    // The filtered view, not the store: two of the four rows are on screen.
    selectAllVisible(["a", "c"]);
    expect([...useTaskStore.getState().selectedIds].sort()).toEqual(["a", "c"]);
    invariant();

    selectAllVisible(["a", "c"]);
    expect(useTaskStore.getState().selectedIds.size).toBe(0);
    invariant();
  });

  it("prunes a selection that outlived its filter", () => {
    const { toggleSelected, pruneSelection } = useTaskStore.getState();
    toggleSelected("a", false, ROWS);
    toggleSelected("d", true, ROWS);
    expect(useTaskStore.getState().selectedIds.size).toBe(4);

    // The search box narrowed the view to one row: a later "Archive" must not
    // reach the three that scrolled out of the filter.
    pruneSelection(["c"]);
    expect([...useTaskStore.getState().selectedIds]).toEqual(["c"]);
    invariant();

    pruneSelection([]);
    expect(useTaskStore.getState().selectedIds.size).toBe(0);
    invariant();
  });

  it("keeps the selection identity stable when nothing left the screen", () => {
    const { toggleSelected, pruneSelection } = useTaskStore.getState();
    toggleSelected("a", false, ROWS);
    const before = useTaskStore.getState().selectedIds;
    // This runs from an effect on every filter keystroke; a fresh Set each time
    // would re-render every subscriber for no change.
    pruneSelection(ROWS);
    expect(useTaskStore.getState().selectedIds).toBe(before);
  });
});

describe("no list surface consults selectMode", () => {
  it.each(Object.entries(SURFACES))("%s never reads it", (_file, source) => {
    expect(
      source.includes("s.selectMode") || /\bselectMode\b\s*(\?|&&)/.test(source),
      "A surface gating an affordance on selectMode is the modal behaviour " +
        "coming back. The checkbox is unconditional and the row's click opens " +
        "the task — see the note at the top of ItemList.tsx.",
    ).toBe(false);
  });

  it("every row surface draws a checkbox with no condition on it", () => {
    for (const file of ["components/TaskListGrouped.tsx", "components/FlatList.tsx"]) {
      const source = SURFACES[file as keyof typeof SURFACES];
      // The checkbox is a plain sibling of the row content, so the JSX around
      // it is a label — not a ternary picking between a box and a grip.
      // S6e: the box is the house `<Checkbox>` (`components/ui/Checkbox.tsx`),
      // not a raw `<input type="checkbox">` — the raw one paints as a solid
      // black square in light mode.
      expect(source, `${file} lost its row checkbox`).toContain("<Checkbox");
      expect(source, `${file} draws a raw checkbox`).not.toContain('type="checkbox"');
      expect(
        /\{\s*select\w*\s*\?[\s\S]{0,200}<Checkbox/.test(source),
        `${file} draws its checkbox behind a condition again`,
      ).toBe(false);
    }
  });

  it("the header offers select-all", () => {
    expect(SURFACES["components/ItemList.tsx"]).toContain("selectAllVisible");
  });
});

describe("the bulk bar is the one /projects draws", () => {
  const CHROME = "border-b border-border bg-muted px-3 py-2";

  it("both apps mount it at the top with the same chrome", () => {
    // Same string in both files: a bar that moves or re-tints on one side has
    // to move on the other, which is the whole point of "one product".
    expect(read("../../projects/components/BulkBar.tsx")).toContain(CHROME);
    expect(
      SURFACES["components/ItemList.tsx"],
      "The /tasks bulk bar was bottom-mounted (`border-t`) with hand-rolled " +
        "buttons. It is top-mounted on the shared chrome now.",
    ).toContain(CHROME);
  });

  it("its controls are primitives, not hand-rolled buttons", () => {
    const source = SURFACES["components/ItemList.tsx"];
    expect(source).toContain('from "@/components/ui/Button"');
    expect(source).toContain('from "@/components/ui/Badge"');
    expect(
      source,
      "`BulkAction` was a hand-rolled <button> — AGENTS.md rule 3, and the " +
        "conformance suite cannot see it because it is outline, not solid.",
    ).not.toContain("function BulkAction");
  });
});
