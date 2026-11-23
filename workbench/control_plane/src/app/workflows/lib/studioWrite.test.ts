/**
 * WFC-4a-2a fence: the rules of the Studio write form.
 *
 * The form writes `package_id`, `action` and `data`, and no other key. The
 * `data` is exactly what `checkForm` returns, so a value that the Form block
 * refuses writes nothing, and a value with `{{` writes nothing. Only the owner
 * of the workflow may set the step, and only a create action of a package that
 * the owner owns is offered. `tests/unit/test_workflows_node_offer.py` holds
 * the pair that the palette drops against the engine (`validate_graph`).
 *
 * Mutation 1: make `writerRole` return "owner" for every member. "only the owner
 * is the owner" turns red.
 * Mutation 2: drop the `{{` check of `dataEdit`. "refuses {{" turns red.
 * Mutation 3: let `createOffers` also find `.update`. "never offers an update"
 * turns red.
 */
import { describe, expect, it } from "vitest";

import type { PackageDetail } from "@/lib/studio/realDoor";

import {
  CREATE_VERB,
  LITERAL_ONLY_MESSAGE,
  STUDIO_WRITE_KEYS,
  actionPatch,
  chainOf,
  createOffers,
  dataDrafts,
  dataEdit,
  dataFields,
  newStudioWriteConfig,
  packagePatch,
  packageView,
  storedSummary,
  studioWritePair,
  studioWriteShape,
  uneditableReason,
  writerRole,
} from "./studioWrite";

const field = (key: string, type: string, extra: Record<string, unknown> = {}) => ({
  key,
  type,
  label: null,
  required: false,
  many: false,
  options: null,
  to: null,
  hot: false,
  ...extra,
});

const ticket = {
  id: "o1",
  name: "ticket",
  label: "Ticket",
  fields: [
    field("title", "text", { required: true }),
    field("points", "number"),
    field("total", "number"),
  ],
  rules: [{ kind: "formula", target: "total", expr: "points * 2" }],
};

const actions = [
  { id: "add_ticket", uses: "object:ticket.create" },
  { id: "edit_ticket", uses: "object:ticket.update" },
  { id: "drop_ticket", uses: "object:ticket.delete" },
  { id: "list_tickets", uses: "object:ticket.list" },
  { id: "add_ghost", uses: "object:ghost.create" },
];

const owned = (over: Partial<PackageDetail> = {}): PackageDetail => ({
  id: "p",
  name: "Tickets",
  access: "owner",
  actions,
  objects: [ticket],
  views: [],
  pages: [],
  nav: [],
  ...over,
});

describe("the keys of the step", () => {
  it("holds exactly package_id, action and data", () => {
    expect([...STUDIO_WRITE_KEYS]).toEqual(["package_id", "action", "data"]);
    expect(Object.keys(newStudioWriteConfig()).sort()).toEqual([...STUDIO_WRITE_KEYS].sort());
    expect(Object.keys(packagePatch("x")).sort()).toEqual([...STUDIO_WRITE_KEYS].sort());
    expect(Object.keys(actionPatch("x")).sort()).toEqual(["action", "data"]);
  });

  it("writes no record_id: a create has none", () => {
    expect(STUDIO_WRITE_KEYS as readonly string[]).not.toContain("record_id");
  });
});

describe("who may edit", () => {
  it("only the owner is the owner", () => {
    expect(writerRole("a@acme.test", "a@acme.test", false)).toBe("owner");
    expect(writerRole("a@acme.test", "b@acme.test", false)).toBe("other");
  });

  it("compares trimmed and lower-cased, as the gateway does", () => {
    expect(writerRole("  Ravi@Acme.test ", "ravi@acme.TEST", false)).toBe("owner");
  });

  it("says loading while access loads, even for the owner", () => {
    expect(writerRole("a@acme.test", "a@acme.test", true)).toBe("loading");
  });

  it("never makes an empty email the owner", () => {
    expect(writerRole("", "", false)).toBe("other");
    expect(writerRole(undefined, undefined, false)).toBe("other");
    expect(writerRole("a@acme.test", undefined, false)).toBe("other");
  });
});

describe("the create offer", () => {
  it("offers the declared create action of an object of the package", () => {
    const offers = createOffers(owned());
    expect(offers.map((o) => o.actionId)).toEqual(["add_ticket"]);
    expect(offers[0].object.name).toBe("ticket");
  });

  it("never offers an update, a delete, a read, or an action of an unknown object", () => {
    const ids = createOffers(owned()).map((o) => o.actionId);
    for (const id of ["edit_ticket", "drop_ticket", "list_tickets", "add_ghost"]) {
      expect(ids).not.toContain(id);
    }
    expect(createOffers(owned({ actions: actions.filter((a) => a.id !== "add_ticket") }))).toEqual([]);
  });

  it("offers nothing for a viewer answer", () => {
    expect(createOffers(owned({ access: "viewer" }))).toEqual([]);
    expect(packageView(owned({ access: "viewer" })).kind).toBe("viewer");
  });

  it("skips an action that sets values by itself", () => {
    const set = [{ id: "add_ticket", uses: "object:ticket.create", set: { title: "x" } }];
    expect(createOffers(owned({ actions: set }))).toEqual([]);
  });

  it("skips an action id that the engine would refuse", () => {
    const bad = [{ id: "Add-Ticket", uses: "object:ticket.create" }];
    expect(createOffers(owned({ actions: bad }))).toEqual([]);
  });

  it("says why a package has no create action", () => {
    const view = packageView(owned({ actions: [] }));
    expect(view.kind).toBe("none");
  });

  it("uses the create verb only", () => {
    expect(CREATE_VERB).toBe("create");
  });
});

describe("the data form", () => {
  const fields = dataFields(ticket);

  it("leaves out a field that a rule fills", () => {
    expect(fields.map((f) => f.key)).toEqual(["title", "points"]);
  });

  it("writes exactly what the Form block checks", () => {
    const drafts = dataDrafts(fields, { title: "Fix the door", points: 3 });
    const edit = dataEdit(fields, drafts);
    expect(edit).toEqual({ ok: true, patch: { data: { title: "Fix the door", points: 3 } } });
  });

  it("starts from the stored values", () => {
    const edit = dataEdit(fields, dataDrafts(fields, { title: "Old" }));
    expect(edit.ok && edit.patch.data).toEqual({ title: "Old" });
  });

  it("refuses {{ and writes nothing", () => {
    const edit = dataEdit(fields, dataDrafts(fields, { title: "Fix {{trigger.door}}" }));
    expect(edit.ok).toBe(false);
    expect(!edit.ok && edit.errors.title).toBe(LITERAL_ONLY_MESSAGE);
  });

  it("refuses a value that the Form block refuses, and writes nothing", () => {
    const blank = dataEdit(fields, dataDrafts(fields, {}));
    expect(blank.ok).toBe(false);
    expect(!blank.ok && Object.keys(blank.errors)).toContain("title");
    const word = dataEdit(fields, dataDrafts(fields, { title: "Ok", points: "many" }));
    expect(word.ok).toBe(false);
  });
});

describe("a stored step that the form cannot edit", () => {
  const ready = packageView(owned());
  const fields = dataFields(ticket);
  const base = { package_id: "11111111-1111-4111-8111-111111111111", action: "add_ticket", data: { title: "A" } };

  it("is editable when it is blank or when it is a create of a known package", () => {
    expect(uneditableReason(newStudioWriteConfig() as Record<string, unknown>, undefined, undefined, null)).toBeNull();
    expect(uneditableReason(base, true, ready, fields)).toBeNull();
  });

  it("is read-only for an update (a record id), whatever the package says", () => {
    expect(uneditableReason({ ...base, record_id: "r" }, true, ready, fields)).not.toBeNull();
  });

  it("is read-only for an action that is not a create action", () => {
    expect(uneditableReason({ ...base, action: "edit_ticket" }, true, ready, fields)).not.toBeNull();
    expect(uneditableReason({ ...base, action: "gone" }, true, ready, fields)).not.toBeNull();
  });

  it("is read-only for an unknown package and for a package that cannot be read", () => {
    expect(uneditableReason(base, false, undefined, null)).not.toBeNull();
    expect(uneditableReason(base, true, null, null)).not.toBeNull();
  });

  it("is read-only for a viewer package", () => {
    expect(uneditableReason(base, true, packageView(owned({ access: "viewer" })), null)).not.toBeNull();
  });

  it("is read-only for a value that the form has no field for", () => {
    expect(uneditableReason({ ...base, data: { title: "A", stray: 1 } }, true, ready, fields)).not.toBeNull();
  });

  it("summarises the stored values in words", () => {
    const lines = storedSummary({ ...base, record_id: "r1", data: { title: "A", n: 2 } }, "Tickets");
    expect(lines).toEqual([
      { label: "Package", value: "Tickets" },
      { label: "Action", value: "add_ticket" },
      { label: "Record", value: "r1" },
      { label: "title", value: "A" },
      { label: "n", value: "2" },
    ]);
  });
});

describe("the pair that the palette drops", () => {
  it("is a Human approval, then the Studio write", () => {
    const chain = chainOf(studioWritePair());
    expect(chain.map((d) => d.nodeType)).toEqual(["approval", "studio_write"]);
    expect(chain[1].config).toEqual({ package_id: "", action: "", data: {} });
  });

  it("holds no {{ in any value", () => {
    expect(JSON.stringify(studioWritePair())).not.toContain("{{");
  });
});

describe("the shape check", () => {
  const n = (id: string, type: string) => ({ id, type });
  const e = (source: string, target: string, sourceHandle?: string) => ({ source, target, sourceHandle });
  const names = (id: string, nodes: ReturnType<typeof n>[], edges: ReturnType<typeof e>[]) =>
    studioWriteShape(id, nodes, edges).map((i) => i.name);

  it("passes a write directly under one approval", () => {
    const nodes = [n("t", "trigger"), n("a", "approval"), n("w", "studio_write")];
    expect(names("w", nodes, [e("t", "a"), e("a", "w")])).toEqual([]);
  });

  it("names a write with no approval above it", () => {
    const nodes = [n("t", "trigger"), n("w", "studio_write")];
    expect(names("w", nodes, [e("t", "w")])).toEqual(["studio_write_without_approval"]);
    expect(names("w", nodes, [])).toEqual(["studio_write_without_approval"]);
  });

  it("names a write two steps below the approval", () => {
    const nodes = [n("a", "approval"), n("s", "set"), n("w", "studio_write")];
    expect(names("w", nodes, [e("a", "s"), e("s", "w")])).toEqual(["studio_write_without_approval"]);
  });

  it("names a write with two parents", () => {
    const nodes = [n("a", "approval"), n("b", "approval"), n("w", "studio_write")];
    expect(names("w", nodes, [e("a", "w"), e("b", "w")])).toEqual(["studio_write_without_approval"]);
  });

  it("names each write after the first under one approval, in the engine order", () => {
    const nodes = [n("a", "approval"), n("w1", "studio_write"), n("w2", "studio_write")];
    const edges = [e("a", "w2"), e("a", "w1")];
    expect(names("w1", nodes, edges)).toEqual([]);
    expect(names("w2", nodes, edges)).toEqual(["approval_clears_one_write"]);
  });

  it("says nothing for a node that is not a write", () => {
    expect(names("a", [n("a", "approval")], [])).toEqual([]);
  });
});
