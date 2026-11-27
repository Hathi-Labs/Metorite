/**
 * STU-AF5: the model of the record page. Default tabs by field, an explicit `record` wins, the URL
 * round trip survives a reload, a hostile URL is dropped, the related filter, and the linked door.
 */
import { describe, expect, it, vi } from "vitest";

import type { StudioDataDoor } from "./dataDoor";
import { boundPageState, decodePageState, encodePageState, EMPTY_PAGE_STATE, type PageState } from "./pageState";
import { previewControls } from "./packagePreview";
import { verbMap } from "./realDoor";
import { mergePageQuery } from "./usePageState";
import {
  RELATED_LIMIT, closeRecord, fileFields, linkedDoor, openRecord, openedRecord, parseRecordLink, pickRecordTab,
  recordLinkBase, linesFields, recordTabs, fileLabel, newButtonState, relatedArgs, relatedLists, withFullPage, withRecordTab, type RecordObject,
} from "./recordPageModel";

const visit: RecordObject = {
  name: "visit", label: "Visit",
  fields: [{ key: "title", type: "text" }, { key: "photos", type: "file", many: true }],
};
const part: RecordObject = {
  name: "part", label: "Part",
  fields: [{ key: "name", type: "text" }, { key: "visit", type: "relation", to: "visit", label: "Visit", hot: true }],
};
const note: RecordObject = { name: "note", fields: [{ key: "text", type: "text" }] };

describe("the tabs of a record", () => {
  it("gives Details alone to an object with no link and no file", () => {
    expect(recordTabs(note, [note, part, visit]).map((t) => t.id)).toEqual(["details"]);
  });

  it("adds Related when another object has a relation to it, and Files for a file field", () => {
    expect(recordTabs(visit, [visit, part]).map((t) => t.id)).toEqual(["details", "related", "files"]);
    expect(recordTabs({ ...visit, fields: [{ key: "title", type: "text" }] }, [visit, part]).map((t) => t.id))
      .toEqual(["details", "related"]);
    expect(recordTabs(visit, [visit]).map((t) => t.id)).toEqual(["details", "files"]);
  });

  it("makes one related table for each relation field that points here", () => {
    const two: RecordObject = {
      name: "task", fields: [
        { key: "a", type: "relation", to: "visit" }, { key: "b", type: "relation", to: "visit", label: "Backup" },
        { key: "c", type: "relation", to: "other" },
      ],
    };
    const lists = relatedLists("visit", [visit, part, two]);
    expect(lists.map((l) => `${l.object}.${l.field}`)).toEqual(["part.visit", "task.a", "task.b"]);
    expect(lists[0].title).toBe("Part by Visit");
  });

  it("skips a target of another app, a many link, a lines relation and its own object", () => {
    const odd: RecordObject = {
      name: "odd", fields: [
        { key: "x", type: "relation", to: "crm.visit" }, { key: "y", type: "relation", to: "visit", many: true },
        { key: "z", type: "relation", to: "visit", lines: "visit" },
      ],
    };
    const self: RecordObject = { name: "visit", fields: [{ key: "parent", type: "relation", to: "visit" }] };
    expect(relatedLists("visit", [odd, self])).toEqual([]);
  });

  it("lists the file fields", () => {
    expect(fileFields(visit).map((f) => f.key)).toEqual(["photos"]);
  });

  it("lets an explicit `record` win, keeps its order and drops an unknown tab", () => {
    const explicit: RecordObject = { ...visit, record: { tabs: ["files", "details", "files", "nope", 3] } };
    expect(recordTabs(explicit, [explicit, part]).map((t) => t.id)).toEqual(["files", "details"]);
    expect(recordTabs({ ...visit, record: { tabs: ["bad"] } }, [visit]).map((t) => t.id)).toEqual(["details", "files"]);
  });

  it("shows the first tab for an unknown wanted tab", () => {
    const tabs = recordTabs(visit, [visit, part]);
    expect(pickRecordTab(tabs, "files")).toBe("files");
    expect(pickRecordTab(tabs, "zzz")).toBe("details");
    expect(pickRecordTab(tabs, null)).toBe("details");
  });
});

describe("the related read", () => {
  it("filters the child list on the link, with the eq op that a relation takes", () => {
    expect(relatedArgs({ field: "visit" }, "r1")).toEqual({
      limit: RELATED_LIMIT, filter: [{ field: "visit", op: "eq", value: "r1" }],
    });
  });
});

describe("the URL of a record", () => {
  const base: PageState = { ...EMPTY_PAGE_STATE, page: "visits" };

  it("round-trips the record, its tab and the full page, so a reload shows the same record and tab", () => {
    const s = withFullPage(withRecordTab(openRecord(base, "visit", "abc-123"), "related"), true);
    const q = encodePageState(s);
    expect(q).toBe("full=1&page=visits&rec=abc-123&ro=visit&rtab=related");
    const back = decodePageState(q);
    expect(openedRecord(back)).toEqual({ object: "visit", id: "abc-123", tab: "related", full: true });
  });

  it("opens in the panel first and clears the tab and the full page on a new record", () => {
    const s = openRecord(withFullPage(withRecordTab(openRecord(base, "visit", "a"), "files"), true), "part", "b");
    expect(openedRecord(s)).toEqual({ object: "part", id: "b", tab: null, full: false });
    expect(closeRecord(s)).toMatchObject({ page: "visits" });
    expect(openedRecord(closeRecord(s))).toBeNull();
    expect(encodePageState(closeRecord(s))).toBe("page=visits");
  });

  it("drops a record with no object, an object with no record, and a hostile id or object", () => {
    expect(decodePageState("rec=a").rec).toBeUndefined();
    expect(decodePageState("ro=visit").ro).toBeUndefined();
    for (const bad of ["a b", "a/b", "<x>", "x".repeat(65), "a\u0000b", "../x"]) {
      const s = decodePageState(new URLSearchParams({ rec: bad, ro: "visit" }));
      expect(s.rec).toBeUndefined();
      expect(s.ro).toBeUndefined();
    }
    for (const bad of ["Visit", "a-b", "1a", "x".repeat(49)]) {
      expect(decodePageState(new URLSearchParams({ rec: "a", ro: bad })).rec).toBeUndefined();
    }
  });

  it("keeps rtab and full only with a record, and a bad rtab is dropped", () => {
    expect(decodePageState("rtab=files&full=1").rtab).toBeUndefined();
    expect(decodePageState("rtab=files&full=1").full).toBeFalsy();
    expect(decodePageState("rec=a&ro=visit&rtab=%3Cb%3E").rtab).toBeUndefined();
    expect(decodePageState("rec=a&ro=visit&full=yes").full).toBeFalsy();
    expect(boundPageState({ ...EMPTY_PAGE_STATE, rec: 5 as unknown as string, ro: "visit" }).rec).toBeUndefined();
  });

  it("does not collide with the tab of the page", () => {
    const s = decodePageState("tab=plan&rec=a&ro=visit&rtab=files");
    expect(s.tab).toBe("plan");
    expect(s.rtab).toBe("files");
  });

  it("merges into a foreign query and keeps the foreign keys", () => {
    const q = mergePageQuery("pkg=abc&rec=old&ro=old&rtab=x&full=1", openRecord(EMPTY_PAGE_STATE, "visit", "new"));
    const p = new URLSearchParams(q);
    expect(p.get("pkg")).toBe("abc");
    expect(p.get("rec")).toBe("new");
    expect(p.get("ro")).toBe("visit");
    expect(p.get("rtab")).toBeNull();
    expect(p.get("full")).toBeNull();
  });
});

describe("the record link of a row", () => {
  it("builds the base and the parse reads it back, with the page query kept", () => {
    const base = recordLinkBase("pkg=abc&page=visits&rec=x&ro=y&rtab=files&full=1&q=bolt", "visit");
    expect(base.startsWith("?")).toBe(true);
    expect(base.endsWith("&rec=")).toBe(true);
    const href = `${base}${encodeURIComponent("11111111-2222-3333-4444-555555555555")}`;
    expect(parseRecordLink(href)).toEqual({ object: "visit", id: "11111111-2222-3333-4444-555555555555" });
    const q = new URLSearchParams(href.slice(1));
    expect(q.get("pkg")).toBe("abc");
    expect(q.get("q")).toBe("bolt");
    expect(q.get("rtab")).toBeNull();
    expect(q.get("full")).toBeNull();
  });

  it("reads no other link", () => {
    for (const h of [null, undefined, "", "/projects?task=1", "https://x.test/?rec=1&ro=a", "?page=a", "?rec=1"]) {
      expect(parseRecordLink(h as string)).toBeNull();
    }
  });
});

describe("linkedDoor", () => {
  it("fills the link in the data of a create, and reads through the same door", async () => {
    const run = vi.fn(async () => ({ ok: true }));
    const read = vi.fn(async () => []);
    const door: StudioDataDoor = { scope: "p:live", read, run };
    const linked = linkedDoor(door, "visit", "rec-1");
    expect(linked.scope).toBe("p:live");
    await linked.run("object:part.create", { data: { name: "Bolt" } });
    expect(run).toHaveBeenCalledWith("object:part.create", { data: { name: "Bolt", visit: { id: "rec-1" } } });
    await linked.read({ action: "object:part.list" });
    expect(read).toHaveBeenCalledOnce();
  });

  it("overrides a value that a caller put in the link field, and copes with no data", async () => {
    const run = vi.fn(async () => ({ ok: true }));
    const linked = linkedDoor({ scope: "s", read: async () => [], run }, "visit", "rec-1");
    await linked.run("object:part.create", { data: { visit: { id: "evil" } } });
    expect(run).toHaveBeenLastCalledWith("object:part.create", { data: { visit: { id: "rec-1" } } });
    await linked.run("object:part.create");
    expect(run).toHaveBeenLastCalledWith("object:part.create", { data: { visit: { id: "rec-1" } } });
  });
});

describe("fileLabel", () => {
  it("says a count and never an id", () => {
    expect(fileLabel({ id: "bbbbbbbb-0000-4000-8000-000000000001" })).toBe("File attached");
    expect(fileLabel([{ id: "a" }])).toBe("File attached");
    expect(fileLabel([{ id: "a" }, { id: "b" }])).toBe("2 files");
    for (const empty of [null, undefined, [], {}, "x", [{}]]) expect(fileLabel(empty)).toBe("");
    expect(fileLabel([{ id: "secret-id" }, { id: "b" }])).not.toContain("secret");
  });
});

describe("the New button of a related table", () => {
  const actions = [
    { id: "pl", uses: "object:part.list" }, { id: "pg", uses: "object:part.get" }, { id: "pc", uses: "object:part.create" },
  ];
  it("is hidden for a viewer: the owner value that AppFrame passes comes from previewControls", () => {
    const viewer = previewControls("viewer", actions);
    expect(viewer.owner).toBe(false);
    expect(verbMap(viewer.actions)["object:part.create"]).toBeUndefined();
    expect(newButtonState(viewer.owner, verbMap(viewer.actions)["object:part.create"] !== undefined)).toBe("hidden");
  });
  it("is on for an owner with a create, and off with a reason for an owner with none", () => {
    const owner = previewControls("owner", actions);
    expect(newButtonState(owner.owner, verbMap(owner.actions)["object:part.create"] !== undefined)).toBe("on");
    const noCreate = previewControls("owner", actions.slice(0, 2));
    expect(newButtonState(noCreate.owner, verbMap(noCreate.actions)["object:part.create"] !== undefined)).toBe("off");
  });
});

describe("the Lines tab (STU-D17a)", () => {
  const formulation: RecordObject = {
    name: "formulation", label: "Formulation",
    fields: [{ key: "name", type: "text" }, { key: "total", type: "number" },
      { key: "ingredients", type: "relation", to: "ingredient", lines: "formulation", label: "Ingredients" }],
  };
  const ingredient: RecordObject = {
    name: "ingredient", label: "Ingredient",
    fields: [{ key: "name", type: "text" }, { key: "pct", type: "number" },
      { key: "formulation", type: "relation", to: "formulation", label: "Formulation" }],
  };
  const batch: RecordObject = {
    name: "batch", label: "Batch",
    fields: [{ key: "code", type: "text" }, { key: "formulation", type: "relation", to: "formulation" }],
  };

  it("gives a parent with a lines field a Lines tab after Details", () => {
    expect(recordTabs(formulation, [formulation, ingredient]).map((t) => t.id)).toEqual(["details", "lines"]);
    expect(recordTabs(formulation, [formulation, ingredient])[1].title).toBe("Lines");
  });

  it("names one sheet for each lines field", () => {
    expect(linesFields("formulation", [formulation, ingredient])).toEqual([
      { key: "ingredients", label: "Ingredients", child: "ingredient", back: "formulation" }]);
    expect(linesFields("ingredient", [formulation, ingredient])).toEqual([]);
    expect(linesFields("formulation", [formulation])).toEqual([]);
  });

  it("drops the back-link of a lines child from Related, and keeps another link", () => {
    const lists = relatedLists("formulation", [formulation, ingredient, batch]);
    expect(lists.map((l) => `${l.object}.${l.field}`)).toEqual(["batch.formulation"]);
    expect(recordTabs(formulation, [formulation, ingredient]).map((t) => t.id)).not.toContain("related");
    expect(recordTabs(formulation, [formulation, ingredient, batch]).map((t) => t.id)).toEqual(["details", "lines", "related"]);
  });

  it("drops a declared Lines tab when the object has no lines field", () => {
    const note2: RecordObject = { ...visit, record: { tabs: ["lines", "details"] } };
    expect(recordTabs(note2, [note2]).map((t) => t.id)).toEqual(["details"]);
    const f2: RecordObject = { ...formulation, record: { tabs: ["lines"] } };
    expect(recordTabs(f2, [f2, ingredient]).map((t) => t.id)).toEqual(["lines"]);
  });
});

