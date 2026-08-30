/**
 * Projects · the tree and the Center slice.
 *
 * Spec: `project-docs/specs/project_management_app.md` §5 · ticket WS-27d
 * done-when 4 — including the one claim that must not be misread as a security
 * boundary.
 */
import { describe, expect, it } from "vitest";

import { hashSlot } from "@/lib/categorical";
import { ICON_REGISTRY } from "@/lib/theme/icon-registry";

import {
  LEVEL_ICONS,
  type ProjectNode,
  SPACE_ICON_CHOICES,
  canMoveUnder,
  childCreationOptions,
  effectiveState,
  filterByCenter,
  flatten,
  hasRunState,
  levelOf,
  moreRestrictive,
  nodeKind,
  nodeLevel,
  ownState,
  pathTo,
  showsDashboard,
  spaceMarker,
  subtreeIds,
} from "./tree";

const FOREST: ProjectNode[] = [
  {
    id: "sales",
    name: "Sales",
    children: [{ id: "q3", name: "Q3 campaign", children: [{ id: "lp", name: "Landing page" }] }],
  },
  { id: "finance", name: "Finance", children: [{ id: "payroll", name: "Payroll" }] },
  { id: "loose", name: "Unmapped space" },
];

const GRANTS = [
  { project_id: "sales", subject: "group:sales" },
  { project_id: "finance", subject: "group:finance" },
];

describe("subtreeIds / flatten / pathTo", () => {
  it("collects a subtree including its own root", () => {
    expect(subtreeIds(FOREST[0])).toEqual(["sales", "q3", "lp"]);
  });

  it("flattens with depth so a sidebar can indent", () => {
    const rows = flatten(FOREST);
    expect(rows.map((r) => [r.node.id, r.depth])).toEqual([
      ["sales", 0], ["q3", 1], ["lp", 2],
      ["finance", 0], ["payroll", 1],
      ["loose", 0],
    ]);
  });

  it("builds a breadcrumb to a nested project", () => {
    expect(pathTo(FOREST, "lp").map((n) => n.id)).toEqual(["sales", "q3", "lp"]);
  });

  it("returns nothing for a project that is not in the forest", () => {
    expect(pathTo(FOREST, "nope")).toEqual([]);
  });
});

describe("filterByCenter", () => {
  it("narrows the forest to the roots granted to that group", () => {
    const shown = filterByCenter(FOREST, GRANTS, "sales");
    expect(shown.map((n) => n.id)).toEqual(["sales"]);
  });

  it("matches the slug case-insensitively", () => {
    expect(filterByCenter(FOREST, GRANTS, "SALES").map((n) => n.id)).toEqual(["sales"]);
  });

  it("shows the whole forest when no Center is named", () => {
    expect(filterByCenter(FOREST, GRANTS, null)).toHaveLength(FOREST.length);
    expect(filterByCenter(FOREST, GRANTS, "")).toHaveLength(FOREST.length);
  });

  it("treats an unknown Center as 'no filter', never as 'you have nothing'", () => {
    // A typo'd slug must not look like an empty portfolio.
    expect(filterByCenter(FOREST, GRANTS, "sails")).toHaveLength(FOREST.length);
  });

  it("is presentation only — it can never widen what the server returned", () => {
    // ⚠️ The claim that matters: this filters a list the server already
    // scoped. Given a forest of one project, no `?center=` value can produce
    // a second one, so a hand-edited URL reveals nothing.
    const scoped: ProjectNode[] = [{ id: "finance", name: "Finance" }];
    for (const slug of ["sales", "people", "company", "finance", "anything"]) {
      const shown = filterByCenter(scoped, GRANTS, slug);
      expect(shown.every((n) => scoped.some((s) => s.id === n.id))).toBe(true);
      expect(shown.length).toBeLessThanOrEqual(scoped.length);
    }
  });

  it("keeps a root whose GRANT sits on a descendant", () => {
    // A Center granted a subproject must still see the branch that reaches it,
    // or the project would be unreachable from the tree it lives in.
    const shown = filterByCenter(FOREST, [{ project_id: "lp", subject: "group:sales" }], "sales");
    expect(shown.map((n) => n.id)).toEqual(["sales"]);
  });
});

describe("canMoveUnder", () => {
  it("allows a move to the root", () => {
    expect(canMoveUnder(FOREST, "q3", null)).toBe(true);
  });

  it("allows a move under an unrelated project", () => {
    expect(canMoveUnder(FOREST, "q3", "finance")).toBe(true);
  });

  it("refuses a project becoming its own parent", () => {
    expect(canMoveUnder(FOREST, "sales", "sales")).toBe(false);
  });

  it("refuses a move into the project's own subtree", () => {
    // The server refuses this too; checking here stops the UI optimistically
    // rendering a tree that cannot exist and then snapping back on the 422.
    expect(canMoveUnder(FOREST, "sales", "lp")).toBe(false);
  });
});

/* ── Effective run state (WS-27bg / D-PM-26) ──────────────────────────────── */

describe("effective run state", () => {
  const node = (status?: string | null) => ({ id: "x", name: "n", status });

  it("is a node's own state when nothing above it is more restrictive", () => {
    expect(effectiveState(node("active"), "active")).toEqual({
      state: "active",
      inherited: false,
    });
  });

  it("defaults a missing state to active rather than blank", () => {
    // Every existing row has `active` as its column DEFAULT; a null here is an
    // older row or a partial payload, not a project in limbo.
    expect(ownState(node(null))).toBe("active");
    expect(ownState(node("  "))).toBe("active");
  });

  it("takes the ancestor's state when the ancestor is more restrictive", () => {
    // The whole D-PM-26 property: the child's own column still says `active`
    // and NOTHING was written to it.
    expect(effectiveState(node("active"), "on_hold")).toEqual({
      state: "on_hold",
      inherited: true,
    });
  });

  it("keeps a child's own state when the child is the more restrictive one", () => {
    // A firmware sub-project may stop while its department keeps running.
    expect(effectiveState(node("stopped"), "active")).toEqual({
      state: "stopped",
      inherited: false,
    });
  });

  it("reports inherited=false when both agree, so the tooltip does not lie", () => {
    // Same word, but the reason is the node's own — a reader must not be sent
    // hunting up the tree for a pause that is right here.
    expect(effectiveState(node("on_hold"), "on_hold")).toEqual({
      state: "on_hold",
      inherited: false,
    });
  });

  it("ranks stopped above on_hold above queued above done above active", () => {
    expect(moreRestrictive("on_hold", "stopped")).toBe("stopped");
    expect(moreRestrictive("queued", "on_hold")).toBe("on_hold");
    expect(moreRestrictive("done", "queued")).toBe("queued");
    expect(moreRestrictive("active", "done")).toBe("done");
  });

  it("treats an UNKNOWN state as active, never as maximally restrictive", () => {
    // A client that has not learned a newer state must not grey out a subtree
    // it does not understand.
    expect(moreRestrictive("active", "some-future-state")).toBe("active");
    expect(effectiveState(node("active"), "some-future-state").state).toBe("active");
  });

  it("is case- and whitespace-insensitive on both sides", () => {
    expect(effectiveState(node(" ACTIVE "), "ON_HOLD")).toEqual({
      state: "on_hold",
      inherited: true,
    });
  });
});

describe("childCreationOptions — the grammar's UI half (migration 193)", () => {
  // space (root) → [folder] → project → [folder] → subproject, and stop.
  // The server refuses what these rows never offer (assert_node_grammar);
  // this table decides which buttons exist and the words on them.

  it("a space offers a project and a folder", () => {
    expect(childCreationOptions("project", 1)).toEqual([
      { kind: "project", label: "New project" },
      { kind: "folder", label: "New folder" },
    ]);
  });

  it("a project offers a subproject and a folder", () => {
    expect(childCreationOptions("project", 2)).toEqual([
      { kind: "project", label: "New subproject" },
      { kind: "folder", label: "New folder" },
    ]);
  });

  it("a subproject offers NOTHING — it is the floor", () => {
    expect(childCreationOptions("project", 3)).toEqual([]);
  });

  it("a folder offers only the one kind its level holds", () => {
    expect(childCreationOptions("folder", 1)).toEqual([
      { kind: "project", label: "New project" },
    ]);
    expect(childCreationOptions("folder", 2)).toEqual([
      { kind: "project", label: "New subproject" },
    ]);
  });

  it("a folder at the floor offers nothing, same as the server refuses", () => {
    expect(childCreationOptions("folder", 3)).toEqual([]);
  });
});

describe("nodeKind — NULL reads as project (R6)", () => {
  it("resolves absent, null and unknown to project", () => {
    expect(nodeKind({})).toBe("project");
    expect(nodeKind({ kind: null })).toBe("project");
    expect(nodeKind({ kind: "folder" })).toBe("folder");
  });
});

describe("nodeLevel / levelOf — the four levels (migration 194)", () => {
  it("maps kind plus generation onto a level", () => {
    expect(nodeLevel("project", 1)).toBe("space");
    expect(nodeLevel("project", 2)).toBe("project");
    expect(nodeLevel("project", 3)).toBe("subproject");
    expect(nodeLevel("folder", 2)).toBe("folder");
  });

  // space → folder → project → folder → subproject: the deepest legal
  // shape, and the one where folders MUST be transparent or every level
  // below the first folder reads one step too deep.
  const forest = [
    {
      id: "space",
      name: "Space",
      children: [
        {
          id: "f1",
          name: "Folder",
          kind: "folder",
          children: [
            {
              id: "proj",
              name: "Project",
              children: [
                {
                  id: "f2",
                  name: "Phases",
                  kind: "folder",
                  children: [{ id: "sub", name: "Phase 1" }],
                },
              ],
            },
          ],
        },
      ],
    },
  ];

  it("reads a level off the forest, folders transparent", () => {
    expect(levelOf(forest, "space")).toBe("space");
    expect(levelOf(forest, "f1")).toBe("folder");
    expect(levelOf(forest, "proj")).toBe("project");
    expect(levelOf(forest, "f2")).toBe("folder");
    expect(levelOf(forest, "sub")).toBe("subproject");
  });

  it("an unknown id reads as space — the safest wrong answer", () => {
    // A space offers no run state and no destructive control, so guessing
    // it cannot expose an action the row does not have.
    expect(levelOf(forest, "nope")).toBe("space");
  });

  it("only a project and a subproject own a run state", () => {
    expect(hasRunState("project")).toBe(true);
    expect(hasRunState("subproject")).toBe(true);
    expect(hasRunState("space")).toBe(false);
    expect(hasRunState("folder")).toBe(false);
  });

  it("only a space and a folder show a dashboard instead of views", () => {
    expect(showsDashboard("space")).toBe(true);
    expect(showsDashboard("folder")).toBe(true);
    // A parent project keeps its views and folds the subtree INTO them.
    expect(showsDashboard("project")).toBe(false);
    expect(showsDashboard("subproject")).toBe(false);
  });
});

describe("spaceMarker — a name and a SLOT, never a colour", () => {
  it("uses what the space chose, converting 1-based to 0-based", () => {
    expect(spaceMarker({ name: "Ops", icon: "Cpu", icon_slot: 3 })).toEqual({
      icon: "Cpu",
      slot: 2,
    });
  });

  it("falls back to the default glyph and a slot hashed from the name", () => {
    const marker = spaceMarker({ name: "Operations" });
    expect(marker.icon).toBe(LEVEL_ICONS.space);
    expect(marker.slot).toBe(hashSlot("Operations"));
    // Stable: the same name must never repaint between renders or users.
    expect(spaceMarker({ name: "Operations" })).toEqual(marker);
  });

  it("ignores a slot outside the ramp rather than emitting a dead class", () => {
    // `bg-cat-9` has no custom property behind it, and a declaration that
    // resolves to nothing takes the whole rule with it.
    for (const bad of [0, 9, -1, 99]) {
      expect(spaceMarker({ name: "X", icon_slot: bad }).slot).toBe(
        hashSlot("X")
      );
    }
  });

  it("every offered icon exists in the themed registry", () => {
    // A name absent from the registry renders as a hole, and the theme
    // system has no way to warn about one.
    for (const name of SPACE_ICON_CHOICES) {
      expect(ICON_REGISTRY[name], `${name} missing from the registry`).toBeDefined();
    }
    expect(ICON_REGISTRY[LEVEL_ICONS.space]).toBeDefined();
    expect(ICON_REGISTRY[LEVEL_ICONS.folder]).toBeDefined();
  });
});
