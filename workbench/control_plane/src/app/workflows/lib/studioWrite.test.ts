/**
 * WFC-4a-2a and WFC-4a-2b-1 fence: the rules of the Studio write form.
 *
 * A create step holds `package_id`, `action` and `data`, and never a
 * `record_id`. An update step holds exactly `package_id`, `action`,
 * `record_id` and `data`, and its `data` holds only the fields that the owner
 * added. Only the owner of the workflow may set the step, only a create or a
 * listed update of a package that the owner owns is offered, and only the
 * owner reads the records. `tests/unit/test_workflows_node_offer.py` holds the
 * pair against the engine (`validate_graph`).
 *
 * Mutations (each was run, and each turned this file red):
 * M1. Make `writerRole` return "owner" for every member: "only the owner is the owner".
 * M2. Drop the `{{` check of `dataEdit`: "refuses {{ and writes nothing".
 * M3. Let `writeOffers` take any update, with no list: "offers an update only with a list".
 * M4. Let `verbMap` keep an action with a `set`, or offer `.delete`: "never offers a delete or a set".
 * M5. Run `updateEdit` over every field and not the added ones: "writes no untouched checkbox".
 * M6. Drop `record_id: undefined` from `actionPatch`: "a switch to a create drops the record id".
 * M7. Drop the role test of `recordReadKey`: "another member starts no read".
 * M8. Put the title in `recordPatch`: "a pick stores the id only".
 */
import { describe, expect, it } from "vitest";

import { createRealDoor, GONE_SENTENCE, NOT_DECLARED_SENTENCE, readActions, type PackageDetail } from "@/lib/studio/realDoor";

import {
  BLANK_FIELD_MESSAGE,
  CREATE_KEYS,
  CREATE_VERB,
  EMPTY_UPDATE_MESSAGE,
  LITERAL_ONLY_MESSAGE,
  MAX_RECORD_OFFSET,
  PICK_RECORD_NOTE,
  RECORDS_FAILED_MESSAGE,
  RECORD_PAGE,
  STRAY_RECORD_NOTE,
  STUDIO_WRITE_KEYS,
  UPDATE_VERB,
  actionPatch,
  addField,
  addableFields,
  canGetRecord,
  chainOf,
  createOffers,
  dataDrafts,
  dataEdit,
  dataFields,
  fieldsFor,
  initialAdded,
  newStudioWriteConfig,
  nextOffset,
  offerLabel,
  packagePatch,
  packageView,
  presentKeys,
  recordGetSource,
  recordListSource,
  recordOptions,
  recordPatch,
  recordReadKey,
  recordsFailedText,
  removeField,
  savedRecordState,
  stepNote,
  storedSummary,
  studioWriteChangePair,
  studioWritePair,
  studioWriteShape,
  studioWriteSummary,
  uneditableReason,
  unlistedLine,
  unlistedUpdates,
  updateEdit,
  updateFields,
  updateOffers,
  writeOffers,
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
    field("done", "checkbox"),
  ],
  rules: [{ kind: "formula", target: "total", expr: "points * 2" }],
};

const visit = { id: "o2", name: "visit", label: "Visit", fields: [field("place", "text")], rules: [] };

const actions = [
  { id: "add_ticket", uses: "object:ticket.create" },
  { id: "edit_ticket", uses: "object:ticket.update" },
  { id: "drop_ticket", uses: "object:ticket.delete" },
  { id: "list_tickets", uses: "object:ticket.list" },
  { id: "get_ticket", uses: "object:ticket.get" },
  { id: "add_ghost", uses: "object:ghost.create" },
  { id: "edit_visit", uses: "object:visit.update" },
];

const owned = (over: Partial<PackageDetail> = {}): PackageDetail => ({
  id: "p",
  name: "Tickets",
  access: "owner",
  actions,
  objects: [ticket, visit],
  views: [],
  pages: [],
  nav: [],
  ...over,
});

const PID = "11111111-1111-4111-8111-111111111111";
const RID = "22222222-2222-4222-8222-222222222222";

/** The config as the save sends it: `JSON.stringify` drops an `undefined` key. */
const saved = (config: Record<string, unknown>) => JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
const merge = (...patches: Record<string, unknown>[]) =>
  patches.reduce<Record<string, unknown>>((acc, p) => ({ ...acc, ...p }), newStudioWriteConfig());

describe("the keys of the step", () => {
  it("lists every engine key, and a create has no record_id", () => {
    expect([...STUDIO_WRITE_KEYS]).toEqual(["package_id", "action", "record_id", "data"]);
    expect([...CREATE_KEYS]).toEqual(["package_id", "action", "data"]);
    expect(Object.keys(newStudioWriteConfig()).sort()).toEqual([...CREATE_KEYS].sort());
  });

  it("a create as the form writes it holds exactly package_id, action and data", () => {
    const fields = dataFields(ticket);
    const edit = dataEdit(fields, dataDrafts(fields, { title: "Fix the door" }));
    expect(edit.ok).toBe(true);
    const config = merge(packagePatch(PID), actionPatch("add_ticket"), edit.ok ? edit.patch : {});
    expect(Object.keys(saved(config)).sort()).toEqual([...CREATE_KEYS].sort());
  });

  it("an update as the form writes it holds exactly the four keys", () => {
    const fields = updateFields(ticket);
    const edit = updateEdit(fields, ["title"], dataDrafts(fields, { title: "Fixed" }));
    expect(edit.ok).toBe(true);
    const config = merge(packagePatch(PID), actionPatch("edit_ticket"), recordPatch(RID), edit.ok ? edit.patch : {});
    expect(Object.keys(saved(config)).sort()).toEqual([...STUDIO_WRITE_KEYS].sort());
    expect(saved(config)).toEqual({ package_id: PID, action: "edit_ticket", record_id: RID, data: { title: "Fixed" } });
  });

  it("a switch to a create drops the record id", () => {
    const config = merge(packagePatch(PID), actionPatch("edit_ticket"), recordPatch(RID), actionPatch("add_ticket"));
    expect(saved(config)).not.toHaveProperty("record_id");
    expect(presentKeys(config)).not.toContain("record_id");
  });

  it("a new package drops the action, the record and the values", () => {
    const config = merge(actionPatch("edit_ticket"), recordPatch(RID), { data: { title: "x" } }, packagePatch(PID));
    expect(saved(config)).toEqual({ package_id: PID, action: "", data: {} });
  });

  it("a create values patch drops a stray record id", () => {
    const fields = dataFields(ticket);
    const edit = dataEdit(fields, dataDrafts(fields, { title: "A" }));
    const config = merge({ record_id: RID }, edit.ok ? edit.patch : {});
    expect(saved(config)).not.toHaveProperty("record_id");
  });

  it("never writes null or an empty record id, because publish refuses both", () => {
    for (const patch of [packagePatch(PID), actionPatch("x")]) {
      expect(patch.record_id).toBeUndefined();
      expect(JSON.stringify(patch)).not.toContain("record_id");
    }
  });

  it("a pick stores the id only", () => {
    expect(recordPatch(RID)).toEqual({ record_id: RID });
    expect(Object.keys(recordPatch(RID))).toEqual(["record_id"]);
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

describe("the offers", () => {
  it("offers the create of an object, and the update of a listed object", () => {
    const offers = writeOffers(owned());
    expect(offers.map((o) => [o.verb, o.actionId])).toEqual([
      ["create", "add_ticket"],
      ["update", "edit_ticket"],
    ]);
    expect(createOffers(owned()).map((o) => o.actionId)).toEqual(["add_ticket"]);
    expect(updateOffers(owned()).map((o) => o.actionId)).toEqual(["edit_ticket"]);
  });

  it("offers an update only with a list", () => {
    const ids = writeOffers(owned()).map((o) => o.actionId);
    expect(ids).not.toContain("edit_visit");
    const noList = owned({ actions: actions.filter((a) => a.id !== "list_tickets") });
    expect(updateOffers(noList)).toEqual([]);
    expect(unlistedUpdates(noList).map((o) => o.actionId)).toEqual(["edit_ticket", "edit_visit"]);
  });

  it("never offers a delete or a set, a read, or an action of an unknown object", () => {
    const ids = writeOffers(owned()).map((o) => o.actionId);
    for (const id of ["drop_ticket", "list_tickets", "get_ticket", "add_ghost"]) {
      expect(ids).not.toContain(id);
    }
    const set = [
      { id: "close_ticket", uses: "object:ticket.update", set: { title: "x" } },
      { id: "list_tickets", uses: "object:ticket.list" },
    ];
    expect(writeOffers(owned({ actions: set }))).toEqual([]);
    const deletes = [{ id: "drop_ticket", uses: "object:ticket.delete" }, { id: "list_tickets", uses: "object:ticket.list" }];
    expect(writeOffers(owned({ actions: deletes }))).toEqual([]);
  });

  it("offers nothing for a viewer answer", () => {
    expect(writeOffers(owned({ access: "viewer" }))).toEqual([]);
    expect(unlistedUpdates(owned({ access: "viewer" }))).toEqual([]);
    expect(packageView(owned({ access: "viewer" })).kind).toBe("viewer");
  });

  it("skips an action id that the engine would refuse", () => {
    const bad = [{ id: "Add-Ticket", uses: "object:ticket.create" }];
    expect(writeOffers(owned({ actions: bad }))).toEqual([]);
  });

  it("says why a package has no write action", () => {
    expect(packageView(owned({ actions: [] })).kind).toBe("none");
  });

  it("names an update that has no list in one plain line", () => {
    const view = packageView(owned());
    expect(view.kind === "ready" && unlistedLine(view.unlisted)).toBe(
      "The editor cannot change a record of Visit, because the package declares no action that lists its records.",
    );
    expect(unlistedLine([])).toBeNull();
  });

  it("labels each offer by its verb", () => {
    const [create, update] = writeOffers(owned());
    expect(offerLabel(create)).toBe("Add a Ticket");
    expect(offerLabel(update)).toBe("Change a Ticket");
    expect(CREATE_VERB).toBe("create");
    expect(UPDATE_VERB).toBe("update");
  });
});

describe("the data form of a create", () => {
  const fields = dataFields(ticket);

  it("leaves out a field that a rule fills", () => {
    expect(fields.map((f) => f.key)).toEqual(["title", "points", "done"]);
  });

  it("writes exactly what the Form block checks", () => {
    const edit = dataEdit(fields, dataDrafts(fields, { title: "Fix the door", points: 3 }));
    expect(edit.ok && edit.patch.data).toEqual({ title: "Fix the door", points: 3, done: false });
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

describe("the data of an update", () => {
  const fields = updateFields({
    ...ticket,
    fields: [...ticket.fields, field("lines", "relation", { lines: "ticket" }), field("spots", "geo", { many: true })],
  });

  it("offers only the fields that an update may change", () => {
    expect(fields.map((f) => f.key)).toEqual(["title", "points", "done"]);
    expect(fieldsFor(writeOffers(owned())[1]).map((f) => f.key)).toEqual(["title", "points", "done"]);
  });

  it("starts with no field, and adds and removes in field order", () => {
    expect(initialAdded(fields, {})).toEqual([]);
    expect(initialAdded(fields, { done: true, title: "A" })).toEqual(["title", "done"]);
    expect(addField(fields, ["done"], "title")).toEqual(["title", "done"]);
    expect(addField(fields, ["done"], "total")).toEqual(["done"]);
    expect(addField(fields, ["done"], "done")).toEqual(["done"]);
    expect(removeField(["title", "done"], "title")).toEqual(["done"]);
    expect(addableFields(fields, ["title"]).map((f) => f.key)).toEqual(["points", "done"]);
  });

  it("writes no untouched checkbox, and no field that was not added", () => {
    const drafts = dataDrafts(fields, {});
    drafts.title = "Fixed";
    const edit = updateEdit(fields, ["title"], drafts);
    expect(edit).toEqual({ ok: true, patch: { data: { title: "Fixed" } } });
    expect(edit.ok && edit.patch.data).not.toHaveProperty("done");
  });

  it("writes a checkbox that the owner added, as it is", () => {
    const edit = updateEdit(fields, ["done"], dataDrafts(fields, {}));
    expect(edit).toEqual({ ok: true, patch: { data: { done: false } } });
  });

  it("does not refuse a blank required field that the owner did not add", () => {
    const drafts = dataDrafts(fields, {});
    drafts.points = "4";
    expect(updateEdit(fields, ["points"], drafts)).toEqual({ ok: true, patch: { data: { points: 4 } } });
  });

  it("refuses an added required field that is blank", () => {
    const edit = updateEdit(fields, ["title"], dataDrafts(fields, {}));
    expect(edit.ok).toBe(false);
    expect(!edit.ok && edit.errors.title).toBe("This field is required.");
  });

  it("refuses an added field with no value, because a step cannot clear a field", () => {
    const edit = updateEdit(fields, ["points"], dataDrafts(fields, {}));
    expect(!edit.ok && edit.errors.points).toBe(BLANK_FIELD_MESSAGE);
  });

  it("writes nothing for an update with no added field, and says so", () => {
    expect(updateEdit(fields, [], dataDrafts(fields, { title: "A" }))).toEqual({
      ok: false, errors: {}, message: EMPTY_UPDATE_MESSAGE,
    });
  });

  it("refuses {{ in an added field", () => {
    const edit = updateEdit(fields, ["title"], dataDrafts(fields, { title: "Fix {{trigger.door}}" }));
    expect(!edit.ok && edit.errors.title).toBe(LITERAL_ONLY_MESSAGE);
  });

  it("refuses a value that the field check refuses", () => {
    const edit = updateEdit(fields, ["points"], dataDrafts(fields, { points: "many" }));
    expect(edit.ok).toBe(false);
  });
});

describe("the record picker", () => {
  const door = createRealDoor({ packageId: PID, actions: readActions(actions) });
  const list = recordListSource("ticket", 0);

  it("reads a page of 100 by offset, up to the store's last offset", () => {
    expect(list).toEqual({ action: "object:ticket.list", args: { limit: RECORD_PAGE, offset: 0 } });
    expect(recordGetSource("ticket", RID)).toEqual({ action: "object:ticket.get", args: { id: RID } });
    expect(nextOffset(0, 100)).toBe(100);
    expect(nextOffset(0, 40)).toBeNull();
    expect(nextOffset(MAX_RECORD_OFFSET - RECORD_PAGE, 100)).toBe(MAX_RECORD_OFFSET);
    expect(nextOffset(MAX_RECORD_OFFSET, 100)).toBeNull();
  });

  it("another member starts no read", () => {
    expect(recordReadKey("other", door, list)).toBeNull();
    expect(recordReadKey("loading", door, list)).toBeNull();
    expect(recordReadKey("owner", null, list)).toBeNull();
    expect(recordReadKey("owner", door, null)).toBeNull();
    expect(recordReadKey("owner", door, list)).toMatch(/^studio:11111111-1111-4111-8111-111111111111:live:object:ticket\.list:/);
  });

  it("reads one record only when the package declares a get", () => {
    expect(canGetRecord(actions, "ticket")).toBe(true);
    expect(canGetRecord(actions, "visit")).toBe(false);
    expect(canGetRecord([{ id: "get_ticket", uses: "object:ticket.get", set: { x: 1 } }], "ticket")).toBe(false);
  });

  it("names each option by its title, and puts a saved record that the page lacks first", () => {
    const fields = dataFields(ticket);
    const rows = [{ id: "r1", title: "Fix the door" }, { id: "r2", title: "" }];
    expect(recordOptions(fields, rows, "r1", null)).toEqual([
      { value: "r1", label: "Fix the door", hint: "r1" },
      { value: "r2", label: "r2", hint: "r2" },
    ]);
    expect(recordOptions(fields, rows, RID, "Replace the lock")[0]).toEqual({ value: RID, label: "Replace the lock" });
    expect(recordOptions(fields, rows, RID, null)[0]).toEqual({ value: RID, label: RID });
    expect(recordOptions(fields, rows, "", null)).toHaveLength(2);
  });

  it("knows a gone record from its get read", () => {
    expect(savedRecordState({ key: null, data: undefined, error: null })).toBe("none");
    expect(savedRecordState({ key: "k", data: undefined, error: null })).toBe("loading");
    expect(savedRecordState({ key: "k", data: [{ id: RID }], error: null })).toBe("found");
    expect(savedRecordState({ key: "k", data: [{ id: RID }], error: GONE_SENTENCE })).toBe("gone");
    expect(savedRecordState({ key: "k", data: undefined, error: "Failed to fetch" })).toBe("failed");
  });

  it("words a failed read with no raw error", () => {
    expect(recordsFailedText("Failed to fetch")).toBe(RECORDS_FAILED_MESSAGE);
    expect(recordsFailedText(NOT_DECLARED_SENTENCE)).toBe(`Could not load the records. ${NOT_DECLARED_SENTENCE}`);
  });

  it("asks for a record on an update, and flags a record id on a create", () => {
    expect(stepNote("update", { record_id: undefined })).toBe(PICK_RECORD_NOTE);
    expect(stepNote("update", { record_id: "" })).toBe(PICK_RECORD_NOTE);
    expect(stepNote("update", { record_id: RID })).toBeNull();
    expect(stepNote("create", { record_id: RID })).toBe(STRAY_RECORD_NOTE);
    expect(stepNote("create", { record_id: undefined })).toBeNull();
    expect(stepNote(undefined, { record_id: RID })).toBeNull();
  });
});

describe("a stored step that the form cannot edit", () => {
  const ready = packageView(owned());
  const createFields = dataFields(ticket);
  const changeFields = updateFields(ticket);
  const base = { package_id: PID, action: "add_ticket", data: { title: "A" } };
  const update = { package_id: PID, action: "edit_ticket", record_id: RID, data: { done: true } };

  it("is editable when it is blank, a create of a known package, or a listed update", () => {
    expect(uneditableReason(newStudioWriteConfig() as Record<string, unknown>, undefined, undefined, null)).toBeNull();
    expect(uneditableReason(base, true, ready, createFields)).toBeNull();
    expect(uneditableReason(update, true, ready, changeFields)).toBeNull();
    expect(uneditableReason({ ...update, record_id: undefined }, true, ready, changeFields)).toBeNull();
  });

  it("is read-only for an update of an object with no list", () => {
    expect(uneditableReason({ ...update, action: "edit_visit" }, true, ready, null)).toContain("no list action");
  });

  it("is read-only for a delete, a set, and an unknown action", () => {
    expect(uneditableReason({ ...base, action: "drop_ticket" }, true, ready, null)).not.toBeNull();
    expect(uneditableReason({ ...base, action: "gone" }, true, ready, null)).not.toBeNull();
  });

  it("is read-only for a key that the engine does not know", () => {
    expect(uneditableReason({ ...update, title: "Fix the door" }, true, ready, changeFields)).not.toBeNull();
  });

  it("is read-only for an unknown package and for a package that cannot be read", () => {
    expect(uneditableReason(base, false, undefined, null)).not.toBeNull();
    expect(uneditableReason(base, true, null, null)).not.toBeNull();
  });

  it("is read-only for a viewer package", () => {
    expect(uneditableReason(base, true, packageView(owned({ access: "viewer" })), null)).not.toBeNull();
  });

  it("is read-only for a value that the form has no field for, such as a computed field", () => {
    expect(uneditableReason({ ...base, data: { title: "A", stray: 1 } }, true, ready, createFields)).not.toBeNull();
    expect(uneditableReason({ ...update, data: { total: 4 } }, true, ready, changeFields)).not.toBeNull();
  });

  it("summarises the stored values in words, with the record by its id only", () => {
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

describe("the card of the step on the canvas", () => {
  it("says adds, changes, or no action, from the config alone", () => {
    expect(studioWriteSummary({ action: "" }).summary).toBe("No action picked yet.");
    expect(studioWriteSummary({ action: "add_ticket", data: {} }).summary).toBe("Adds a record after the owner approves it.");
    expect(studioWriteSummary({ action: "edit_ticket", record_id: RID, data: {} }).summary).toBe(
      "Changes a record after the owner approves it.",
    );
  });

  it("never names the record or a value", () => {
    const words = studioWriteSummary({ action: "edit_ticket", record_id: RID, data: { title: "Secret plan" } });
    expect(JSON.stringify(words)).not.toContain(RID);
    expect(JSON.stringify(words)).not.toContain("Secret plan");
    expect(words.detail).toBe("edit_ticket");
  });
});

describe("the pairs that the palette drops", () => {
  it("is a Human approval, then the Studio write", () => {
    for (const pair of [studioWritePair(), studioWriteChangePair()]) {
      const chain = chainOf(pair);
      expect(chain.map((d) => d.nodeType)).toEqual(["approval", "studio_write"]);
      expect(chain[1].config).toEqual({ package_id: "", action: "", data: {} });
    }
  });

  it("names the change in fixed words, with no {{", () => {
    const chain = chainOf(studioWriteChangePair());
    expect(chain[0].config).toEqual({ message: "Change this record in the app?" });
    expect(chain[1].label).toBe("Change a record");
    expect(JSON.stringify(studioWritePair())).not.toContain("{{");
    expect(JSON.stringify(studioWriteChangePair())).not.toContain("{{");
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
