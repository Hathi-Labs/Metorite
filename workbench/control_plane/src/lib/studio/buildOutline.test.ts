/**
 * STU-G3a fence (Explorer): `buildOutline` makes the tree of the Studio IDE from the DRAFT manifest.
 *
 * Mutation 1: drop a branch from `buildOutline` (for example "Rules"). "always has the seven branches" turns red.
 * Mutation 2: return no "None yet" child for an empty branch. "shows None yet" turns red.
 * Mutation 3: let `buildOutline` throw on a non-object. "never throws" turns red.
 * Mutation 4: drop the fields of an object from its children. "an object holds its fields" turns red.
 */
import { describe, expect, it } from "vitest";

import { BRANCHES, EMPTY_HINT, NONE_YET, branchIds, buildOutline } from "./buildOutline";

const manifest = {
  studio: 1, id: "train", name: "Metorite Train", purpose: "Track milestones",
  objects: [
    { name: "milestone", label: "Milestones", fields: [
      { key: "title", type: "text", required: true }, { key: "status", type: "select", label: "Status", options: ["A"] },
    ], rules: [{ name: "close_needs_done", kind: "constraint" }] },
    { name: "station", fields: [] },
  ],
  views: [{ id: "track", block: "board", title: "Train track", object: "milestone" }],
  actions: [{ id: "list_m", uses: "object:milestone.list" }],
  workflows: [{ id: "weekly" }],
  skill: { description: "Answers about milestones" },
  contributes: { jobs: [{ verb: "x" }], empty: [] },
};

const labels = (o: ReturnType<typeof buildOutline>) => o.root?.children?.map((c) => c.label);

describe("buildOutline", () => {
  it("always has the seven branches, in order", () => {
    expect(labels(buildOutline(manifest))).toEqual(["Data", "Views", "Actions", "Rules", "Workflows", "Skill", "Code blocks"]);
    expect(labels(buildOutline({ studio: 1, id: "a", name: "A", purpose: "p" }))).toHaveLength(BRANCHES.length);
  });

  it("names the app at the root", () => {
    expect(buildOutline(manifest).root).toMatchObject({ kind: "app", label: "Metorite Train" });
    expect(buildOutline({ id: "only-id" }).root?.label).toBe("only-id");
    expect(buildOutline({}).root?.label).toBe("Untitled app");
  });

  it("an object holds its fields, and a click on it selects the object in the tabs", () => {
    const data = buildOutline(manifest).root!.children![0];
    const [m, s] = data.children!;
    expect(m).toMatchObject({ kind: "object", label: "Milestones", object: "milestone", tab: "preview", hint: "2 fields" });
    expect(m.children!.map((c) => c.label)).toEqual(["title", "Status"]);
    expect(m.children![0].hint).toBe("text · required");
    expect(s.children).toBeUndefined();
  });

  it("shows None yet in a branch with nothing in it", () => {
    const empty = buildOutline({ studio: 1, id: "a", name: "A", purpose: "p" });
    for (const b of empty.root!.children!) {
      expect(b.children).toHaveLength(1);
      expect(b.children![0]).toMatchObject({ kind: "none", label: NONE_YET });
    }
    // A branch with parts has no None-yet child.
    const full = buildOutline(manifest).root!.children!;
    expect(full[0].children!.some((c) => c.kind === "none")).toBe(false);
  });

  it("reads rules, workflows, the skill and the code blocks", () => {
    const [, views, actions, rules, workflows, skill, code] = buildOutline(manifest).root!.children!;
    expect(views.children![0]).toMatchObject({ label: "Train track", hint: "board", tab: "views" });
    expect(actions.children![0].hint).toBe("object:milestone.list");
    expect(rules.children![0].label).toBe("milestone · close_needs_done");
    expect(workflows.children![0].label).toBe("weekly");
    expect(skill.children![0].label).toBe("Answers about milestones");
    expect(code.children!.map((c) => c.label)).toEqual(["jobs"]);
  });

  it("never throws, and gives an empty tree with a hint for a bad manifest", () => {
    for (const bad of [null, undefined, 4, "x", [], [1]]) {
      expect(() => buildOutline(bad)).not.toThrow();
      expect(buildOutline(bad)).toEqual({ root: null, hint: EMPTY_HINT });
    }
    const odd = { name: 5, objects: [null, 3, { fields: "no" }, { name: "o", fields: [null, { key: 1 }] }], views: "x", skill: 3 };
    expect(() => buildOutline(odd)).not.toThrow();
  });

  it("gives every node a unique id, and lists the nodes that open", () => {
    const o = buildOutline(manifest);
    const ids: string[] = [];
    const walk = (n: NonNullable<typeof o.root>) => { ids.push(n.id); n.children?.forEach(walk); };
    walk(o.root!);
    expect(new Set(ids).size).toBe(ids.length);
    expect(branchIds(o)).toContain("branch:data");
    expect(branchIds(buildOutline(null))).toEqual([]);
  });
});
