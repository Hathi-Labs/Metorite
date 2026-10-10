/**
 * The project picker's model (`pickerTree.ts`). Owner ask, 2026-10-10.
 */
import { describe, expect, it } from "vitest";

import { destinations } from "./destinations";
import {
  OPEN_ALL_BELOW,
  initialExpanded,
  matchesTokens,
  pathLabel,
  pickerNodes,
  searchRows,
  visibleRows,
} from "./pickerTree";
import type { ProjectNode } from "./tree";

/** The shape in the owner's screenshot: a space, a folder, its projects. */
const TREE: ProjectNode[] = [
  {
    id: "works",
    name: "Fracktal Works",
    children: [
      { id: "mfg", name: "Manufacturing" },
      { id: "qc", name: "Quality Control" },
    ],
  },
  {
    id: "fracktory",
    name: "Fracktory",
    children: [
      {
        id: "printing",
        name: "3d printing services",
        kind: "folder",
        children: [
          { id: "customers", name: "CUSTOMER LIST" },
          { id: "fdm", name: "FDM PRINTS" },
          { id: "sla", name: "SLA, SLS& MJF PRINTS" },
        ],
      },
      {
        id: "eng",
        name: "Engineering Services",
        kind: "folder",
        children: [
          {
            id: "booth",
            name: "Photo Booth",
            children: [{ id: "booth-fw", name: "Booth firmware" }],
          },
        ],
      },
    ],
  },
];

const NODES = pickerNodes(TREE);
const byId = (id: string) => NODES.find((n) => n.id === id)!;

describe("pickerNodes", () => {
  it("agrees with destinations() on every node, in the same order", () => {
    // One rule for "may a task land here". A second copy would drift.
    const rows = destinations(TREE);
    expect(NODES.map((n) => [n.id, n.pickable, n.depth])).toEqual(
      rows.map((r) => [r.node.id, r.legal, r.depth]),
    );
  });

  it("carries each node's ancestors as its path", () => {
    expect(byId("fdm").path).toEqual(["Fracktory", "3d printing services"]);
    expect(byId("fracktory").path).toEqual([]);
    expect(byId("fdm").parentId).toBe("printing");
  });

  it("names the level, with folders transparent", () => {
    expect(byId("fracktory").level).toBe("space");
    expect(byId("printing").level).toBe("folder");
    expect(byId("fdm").level).toBe("project");
    expect(byId("booth-fw").level).toBe("subproject");
  });
});

describe("visibleRows", () => {
  it("shows only the spaces when nothing is open", () => {
    expect(visibleRows(NODES, new Set()).map((n) => n.id)).toEqual(["works", "fracktory"]);
  });

  it("opens one level at a time, folders included", () => {
    const rows = visibleRows(NODES, new Set(["fracktory"])).map((n) => n.id);
    expect(rows).toEqual(["works", "fracktory", "printing", "eng"]);
  });

  it("hides a child whose parent is open but whose grandparent is closed", () => {
    expect(visibleRows(NODES, new Set(["printing"])).map((n) => n.id)).toEqual([
      "works",
      "fracktory",
    ]);
  });
});

describe("initialExpanded", () => {
  it("opens the path to the current pick, and not the pick itself", () => {
    const big = pickerNodes([...TREE, ...filler(OPEN_ALL_BELOW)]);
    expect([...initialExpanded(big, ["booth"])].sort()).toEqual(["eng", "fracktory"]);
  });

  it("opens everything when the tree is short", () => {
    const small = pickerNodes([TREE[0]]);
    expect([...initialExpanded(small, [])]).toEqual(["works"]);
  });

  it("opens nothing for an id the tree does not hold", () => {
    const big = pickerNodes([...TREE, ...filler(OPEN_ALL_BELOW)]);
    expect(initialExpanded(big, ["gone", null, undefined]).size).toBe(0);
  });
});

describe("searchRows", () => {
  const ids = (q: string) => searchRows(NODES, q).map((h) => h.node.id);

  it("finds a project by a word of its name, wherever it sits", () => {
    expect(ids("fdm")).toEqual(["fdm"]);
  });

  it("lets a word match an ancestor, so a space name lists its projects", () => {
    expect(ids("fracktory fdm")).toEqual(["fdm"]);
    // Folders never appear: they hold no tasks.
    expect(ids("fracktory")).toEqual([
      "fracktory",
      "customers",
      "fdm",
      "sla",
      "booth",
      "booth-fw",
    ]);
  });

  it("never returns a folder, even on an exact name", () => {
    expect(ids("3d printing services")).toEqual(["customers", "fdm", "sla"]);
  });

  it("ranks a name that starts with the word above one that contains it", () => {
    // "booth" starts both Booth names; "prints" starts FDM's and SLA's second word.
    expect(ids("prints")).toEqual(["fdm", "sla"]);
    const hits = searchRows(NODES, "control");
    expect(hits[0].node.id).toBe("qc");
    expect(hits[0].mark).toEqual([8, 15]);
  });

  it("puts a path-only match after every name match", () => {
    expect(ids("booth")).toEqual(["booth", "booth-fw"]);
    expect(ids("engineering")).toEqual(["booth", "booth-fw"]);
    expect(searchRows(NODES, "engineering")[0].mark).toBeNull();
  });

  it("is case-blind and returns nothing for a blank query", () => {
    expect(ids("CUSTOMER")).toEqual(["customers"]);
    expect(ids("   ")).toEqual([]);
  });
});

describe("helpers", () => {
  it("matchesTokens needs every word", () => {
    expect(matchesTokens(["fdm", "fracktory"], "FDM PRINTS", ["Fracktory"])).toBe(true);
    expect(matchesTokens(["fdm", "works"], "FDM PRINTS", ["Fracktory"])).toBe(false);
    expect(matchesTokens([], "anything")).toBe(true);
  });

  it("pathLabel joins the ancestors top first", () => {
    expect(pathLabel(byId("fdm").path)).toBe("Fracktory › 3d printing services");
    expect(pathLabel([])).toBe("");
  });
});

function filler(count: number): ProjectNode[] {
  return Array.from({ length: count }, (_, i) => ({ id: `f${i}`, name: `Filler ${i}` }));
}
