/**
 * Task 3852, slice 1 — a node under a parent that the member cannot see.
 *
 * The gateway sends `parent_hidden` on every node. Slice 2 (task 3885) then
 * sends a null `parent_project_id` for such a node, so a client that reads
 * "no parent id" as "a root" would call it a space, offer it the lifecycle
 * policy, and let a top-level drag pull it out of its parent.
 *
 * Three shapes, and each helper must agree on all three:
 *
 * * slice 2: `{ parent_project_id: null, parent_hidden: true }` — not a root;
 * * an old gateway: a parent id and no flag — not a root, as before;
 * * a team node under the member's OWN personal root (migration 297): a
 *   parent id and `parent_hidden: false`. The member sees the parent, but the
 *   team tree does not draw it. Not a root either.
 */

import { describe, expect, it } from "vitest";

import { assistantLevel } from "./assistantPersona";
import { destinations, isTeamSpace } from "./destinations";
import { type ProjectNode, isTrueRoot } from "./tree";
import { hasHiddenParent, planTreeDrop } from "./treeDrop";

const node = (id: string, position: number, extra: Partial<ProjectNode> = {}): ProjectNode => ({
  id,
  name: id,
  kind: "project",
  position,
  ...extra,
});

const SLICE_TWO = { parent_project_id: null, parent_hidden: true } as const;
const OLD_GATEWAY = { parent_project_id: "unseen-parent" } as const;
const OWN_PERSONAL_PARENT = { parent_project_id: "my-root", parent_hidden: false } as const;

describe("isTrueRoot", () => {
  it("is true only with no parent id and no hidden parent", () => {
    expect(isTrueRoot({ parent_project_id: null })).toBe(true);
    expect(isTrueRoot({ parent_project_id: null, parent_hidden: false })).toBe(true);
    expect(isTrueRoot({})).toBe(true);
    expect(isTrueRoot(SLICE_TWO)).toBe(false);
    expect(isTrueRoot(OLD_GATEWAY)).toBe(false);
    expect(isTrueRoot(OWN_PERSONAL_PARENT)).toBe(false);
  });
});

describe("isTeamSpace and the destination list", () => {
  it("a top row under a hidden parent is a project, not a space, with no parent id", () => {
    expect(isTeamSpace(node("ghost", 1, SLICE_TWO), 0)).toBe(false);
    expect(isTeamSpace(node("ghost", 1, OLD_GATEWAY), 0)).toBe(false);
    expect(isTeamSpace(node("team", 1, OWN_PERSONAL_PARENT), 0)).toBe(false);
    expect(isTeamSpace(node("space", 1, { parent_project_id: null, parent_hidden: false }), 0)).toBe(
      true,
    );
  });

  it("a task may land on a top row under a hidden parent, and not on a space", () => {
    const rows = destinations([node("space", 1), node("ghost", 2, SLICE_TWO)]);
    const legal = Object.fromEntries(rows.map((row) => [row.node.id, row.legal]));
    expect(legal).toEqual({ space: false, ghost: true });
  });
});

describe("assistantLevel", () => {
  it("tells the assistant that a top row under a hidden parent is a project", () => {
    expect(assistantLevel("space", { ...SLICE_TWO, kind: "project" })).toBe("project");
    expect(assistantLevel("space", { parent_project_id: null, kind: "project" })).toBe("space");
  });
});

describe("H-257 with the flag · a top-level drag", () => {
  const withHidden = (): ProjectNode[] => [
    node("space", 100),
    node("ghost", 200, SLICE_TWO),
    node("other", 300),
    node("loose", 400),
  ];

  it("hasHiddenParent reads the flag, the id, and both together", () => {
    expect(hasHiddenParent(node("ghost", 1, SLICE_TWO))).toBe(true);
    expect(hasHiddenParent(node("ghost", 1, OLD_GATEWAY))).toBe(true);
    expect(hasHiddenParent(node("team", 1, OWN_PERSONAL_PARENT))).toBe(true);
    expect(hasHiddenParent(node("space", 1))).toBe(false);
  });

  it("refuses a top-level drop of a root under a hidden parent with a null parent id", () => {
    const planned = planTreeDrop(withHidden(), "ghost", { kind: "between", parentId: null, index: 0 });
    expect(planned).toHaveProperty("refusal");
  });

  it("does not rank a true root against it", () => {
    // Drawn: space, ghost, other, loose. `loose` at index 2 lands between the
    // anchors space (100) and other (300). Ghost is not an anchor.
    const planned = planTreeDrop(withHidden(), "loose", { kind: "between", parentId: null, index: 2 });
    expect(planned).toEqual({ plan: { parentId: null, position: 200 } });
  });

  it("still lets a true root reorder among true roots", () => {
    const planned = planTreeDrop(withHidden(), "other", { kind: "between", parentId: null, index: 0 });
    expect(planned).toEqual({ plan: { parentId: null, position: 50 } });
  });
});
