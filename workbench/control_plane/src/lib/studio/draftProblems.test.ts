/**
 * STU-G3a fence (Problems): the panel runs structural checks in the browser and counts them.
 *
 * Mutation 1: make `checkDraft` return `[]` for a non-object. "gives one error for a draft that is no manifest" turns red.
 * Mutation 2: drop the unknown-object check of a view. "errors on a view of an unknown object" turns red.
 * Mutation 3: count a warning as an error in `countProblems`. "counts errors and warnings apart" turns red.
 * Mutation 4: change the header words in `problemsLine`. "writes the header" turns red.
 */
import { describe, expect, it } from "vitest";

import { CHAT_CAP, checkDraft, countProblems, problemsLine } from "./draftProblems";

const ok = {
  studio: 1, id: "train", name: "Train", purpose: "p",
  objects: [{ name: "m", fields: [{ key: "t", type: "text" }, { key: "s", type: "select", options: ["A"] }] }],
  actions: [{ id: "l", uses: "object:m.list" }],
  views: [{ id: "v", block: "table", object: "m" }],
};
const only = (m: unknown, sev: "error" | "warning") => checkDraft(m).filter((p) => p.severity === sev).map((p) => p.path);

describe("checkDraft", () => {
  it("finds nothing in a sound draft", () => {
    expect(checkDraft(ok)).toEqual([]);
  });

  it("gives one error for a draft that is no manifest, and never throws", () => {
    for (const bad of [null, undefined, 3, "x", [], [1]]) {
      const p = checkDraft(bad);
      expect(p).toHaveLength(1);
      expect(p[0]).toMatchObject({ severity: "error", path: "$" });
    }
    expect(() => checkDraft({ objects: [null, 4, { fields: [null] }], views: "x", actions: [3] })).not.toThrow();
  });

  it("errors on a missing id, name and purpose, and a wrong version", () => {
    expect(only({}, "error")).toEqual(expect.arrayContaining(["$.studio", "$.id", "$.name", "$.purpose"]));
    expect(only({ ...ok, id: "Bad Id" }, "error")).toEqual(["$.id"]);
  });

  it("errors on a duplicate object, a duplicate field and an unknown field type", () => {
    const m = { ...ok, objects: [
      { name: "m", fields: [{ key: "t", type: "text" }, { key: "t", type: "wat" }] },
      { name: "m", fields: [{ key: "a", type: "text" }] },
    ] };
    expect(only(m, "error")).toEqual(
      expect.arrayContaining(["$.objects[1].name", "$.objects[0].fields[1].key", "$.objects[0].fields[1].type"]),
    );
  });

  it("errors on a relation with no target or an unknown target", () => {
    const m = { ...ok, objects: [{ name: "m", fields: [{ key: "a", type: "relation" }, { key: "b", type: "relation", to: "zz" }] }] };
    expect(only(m, "error")).toEqual(["$.objects[0].fields[0].to", "$.objects[0].fields[1].to"]);
  });

  it("errors on a view of an unknown object, an unknown block and an action of an unknown object", () => {
    expect(only({ ...ok, views: [{ id: "v", block: "table", object: "zz" }] }, "error")).toEqual(["$.views[0].object"]);
    expect(only({ ...ok, actions: [{ id: "a", uses: "object:zz.list" }] }, "error")).toEqual(["$.actions[0].uses"]);
    expect(only({ ...ok, views: [{ id: "v", block: "nope", object: "m" }] }, "error")).toEqual(["$.views[0].block"]);
  });

  it("warns, and does not error, on an empty app, a select with no options and a missing list action", () => {
    expect(only({ ...ok, objects: [], actions: [], views: [] }, "warning")).toEqual(["$.objects"]);
    const m = { ...ok, objects: [{ name: "m", fields: [{ key: "s", type: "select" }] }], actions: [] };
    expect(only(m, "warning")).toEqual(["$.objects[0].fields[0].options", "$.actions"]);
    expect(only(m, "error")).toEqual([]);
  });

  it("warns on a draft over what a chat holds", () => {
    const big = { ...ok, purpose: "x".repeat(CHAT_CAP) };
    const w = checkDraft(big).find((p) => p.severity === "warning" && p.path === "$");
    expect(w).toBeTruthy();
    expect(w!.message).toMatch(/new, smaller app/);
  });
});

/**
 * C8a fence (row rules): the browser check refuses a `where` on a field that is not a hot person
 * field, as `studio_access.check_access_fit` does on the server.
 * Mutation 5: drop the hot test of `checkAccess`. "refuses a where on a cold person field" turns red.
 * Mutation 6: drop the type test. "refuses a where on a field that is not a person" turns red.
 */
describe("checkDraft, the row rules of C8a", () => {
  const slip = (access: unknown[], fields?: unknown[]) => ({
    ...ok,
    roles: ["hr", "employee"],
    objects: [{
      name: "m",
      fields: fields ?? [{ key: "employee", type: "person", hot: true }, { key: "pay", type: "number" }],
      access,
    }],
  });
  const readsMe = { where: "employee = $me" };

  it("finds nothing in a sound access list", () => {
    expect(only(slip([{ role: "hr", read: "all", write: "all" }, { role: "employee", read: readsMe, hide: ["pay"] }]),
      "error")).toEqual([]);
  });

  it("refuses a where on a cold person field", () => {
    const cold = [{ key: "employee", type: "person" }, { key: "pay", type: "number" }];
    const p = checkDraft(slip([{ role: "employee", read: readsMe }], cold)).filter((x) => x.severity === "error");
    expect(p.map((x) => x.path)).toEqual(["$.objects[0].access[0].read"]);
    expect(p[0].message).toMatch(/must be hot/);
  });

  it("refuses a where on a field that is not a person", () => {
    const p = checkDraft(slip([{ role: "employee", read: { where: "pay = $me" } }])).filter((x) => x.severity === "error");
    expect(p.map((x) => x.path)).toEqual(["$.objects[0].access[0].read"]);
    expect(p[0].message).toMatch(/not a person field/);
  });

  it("refuses a read of another shape and a role that the app does not declare", () => {
    expect(only(slip([{ role: "boss", read: "some" }]), "error")).toEqual([
      "$.objects[0].access[0].role", "$.objects[0].access[0].read",
    ]);
  });

  it("asks for access on the lines of a table with access (fix round 1)", () => {
    const withLines = (lineAccess: unknown[] | undefined): unknown => ({
      ...ok,
      roles: ["hr"],
      objects: [
        { name: "m", fields: [{ key: "rows", type: "relation", to: "row", lines: "m" }],
          access: [{ role: "hr", read: "all" }] },
        { name: "row", fields: [{ key: "m", type: "relation", to: "m" }], access: lineAccess },
      ],
    });
    expect(only(withLines(undefined), "error")).toEqual(["$.objects[1]"]);
    expect(only(withLines([{ role: "hr", read: "all" }]), "error")).toEqual([]);
  });
});

describe("the count", () => {
  it("counts errors and warnings apart", () => {
    const p = [
      { severity: "error" as const, path: "a", message: "" }, { severity: "warning" as const, path: "b", message: "" },
      { severity: "warning" as const, path: "c", message: "" },
    ];
    expect(countProblems(p)).toEqual({ errors: 1, warnings: 2 });
  });
  it("writes the header", () => {
    expect(problemsLine([])).toBe("0 errors · 0 warnings");
    expect(problemsLine([{ severity: "warning", path: "x", message: "" }])).toBe("0 errors · 1 warning");
    expect(problemsLine([{ severity: "error", path: "x", message: "" }])).toBe("1 error · 0 warnings");
  });
});
