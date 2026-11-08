import { describe, expect, it } from "vitest";

import { cleanName, diffLines, diffManifests } from "./manifestDiff";

const base = () => ({
  studio: 1, id: "visits", name: "Visits", purpose: "Track visits.",
  objects: [
    { name: "units", label: "Units", fields: [{ key: "site", type: "text" }, { key: "phone", type: "phone", label: "Phone" }] },
    { name: "visits", fields: [{ key: "when", type: "date" }] },
  ],
  views: [{ id: "board", block: "board", title: "Board", object: "visits" }],
  actions: [{ id: "peek", uses: "object:visits.get", title: "Peek" }],
});
const lines = (b: unknown, a: unknown) => diffLines(diffManifests(b, a), 50);
const edit = (fn: (m: ReturnType<typeof base>) => void) => { const m = base(); fn(m); return m; };

describe("diffManifests", () => {
  it("finds nothing when nothing changed", () => {
    expect(diffManifests(base(), base())).toEqual([]);
  });
  it("says an order change, because the app draws these lists in order", () => {
    expect(lines(base(), edit((m) => { m.objects.reverse(); }))).toEqual(["changes the order of the record types"]);
    expect(lines(base(), edit((m) => { m.objects[0].fields.reverse(); }))).toEqual(["changes the order of the fields of «Units»"]);
    const two = edit((m) => { m.views.push({ id: "t", block: "table", title: "All", object: "visits" }); });
    const flipped = JSON.parse(JSON.stringify(two)); flipped.views.reverse();
    expect(lines(two, flipped)).toEqual(["changes the order of the views"]);
    const pg = { ...base(), pages: [{ id: "a" }, { id: "b" }] };
    expect(lines(pg, { ...base(), pages: [{ id: "b" }, { id: "a" }] })).toEqual(["changes the order of the pages"]);
    const ac = edit((m) => { m.actions.push({ id: "z", uses: "x", title: "Z" }); m.actions.push({ id: "y", uses: "x", title: "Y" }); });
    const acr = JSON.parse(JSON.stringify(ac)); acr.actions.reverse();
    expect(lines(ac, acr)).toEqual(["changes the order of the actions"]);
  });
  it("says no order change for an add, and no change for the same order", () => {
    expect(lines(base(), edit((m) => { m.objects.unshift({ name: "new", fields: [] } as never); }))).toEqual(["adds the record type «new»"]);
    expect(diffManifests(base(), base())).toEqual([]);
  });
  it("backstops a difference that no rule reads", () => {
    expect(lines(base(), { ...base(), id: "other" })).toEqual(["changes other parts of the app"]);
    expect(lines(base(), { ...base(), studio: 2 })).toEqual(["changes other parts of the app"]);
  });
  it("names the app and its purpose", () => {
    expect(lines(base(), edit((m) => { m.name = "Trips"; }))).toEqual(["renames the app from «Visits» to «Trips»"]);
    expect(lines(base(), edit((m) => { m.purpose = "Other."; }))).toEqual(["changes the purpose of the app"]);
  });
  it("adds, removes and relabels a record type", () => {
    expect(lines(base(), edit((m) => { m.objects.push({ name: "sites", fields: [] } as never); }))).toEqual(["adds the record type «sites»"]);
    expect(lines(base(), edit((m) => { m.objects.pop(); }))).toEqual(["removes the record type «visits»"]);
    expect(lines(base(), edit((m) => { (m.objects[0] as { label: string }).label = "Homes"; }))).toEqual(["renames the record type «Units» to «Homes»"]);
  });
  it("adds and removes a field", () => {
    expect(lines(base(), edit((m) => { m.objects[0].fields.push({ key: "site2", type: "text" } as never); }))).toEqual(["adds the field «site2» to «Units»"]);
    expect(lines(base(), edit((m) => { m.objects[0].fields.pop(); }))).toEqual(["removes the field «Phone» from «Units»"]);
  });
  it("relabels, retypes and flips required on a field", () => {
    expect(lines(base(), edit((m) => { (m.objects[0].fields[1] as { label: string }).label = "Mobile"; })))
      .toEqual(["renames the field «Phone» to «Mobile» in «Units»"]);
    expect(lines(base(), edit((m) => { m.objects[0].fields[0].type = "email"; })))
      .toEqual(["changes the type of «site» in «Units» from «text» to «email»"]);
    const req = edit((m) => { (m.objects[0].fields[0] as { required?: boolean }).required = true; });
    expect(lines(base(), req)).toEqual(["makes the field «site» in «Units» required"]);
    expect(lines(req, base())).toEqual(["makes the field «site» in «Units» optional"]);
  });
  it("sees a change of options and of another property", () => {
    const sel = edit((m) => { m.objects[0].fields.push({ key: "s", type: "select", options: ["a"] } as never); });
    const sel2 = JSON.parse(JSON.stringify(sel));
    sel2.objects[0].fields[2].options = ["a", "b"];
    expect(lines(sel, sel2)).toEqual(["changes the options of «s» in «Units»"]);
    const hot = JSON.parse(JSON.stringify(sel));
    hot.objects[0].fields[2].hot = true;
    expect(lines(sel, hot)).toEqual(["changes the field «s» in «Units»"]);
  });
  it("adds, removes and changes a view", () => {
    expect(lines(base(), edit((m) => { m.views.push({ id: "t", block: "table", title: "All", object: "visits" }); }))).toEqual(["adds the view «All»"]);
    expect(lines(base(), edit((m) => { m.views.pop(); }))).toEqual(["removes the view «Board»"]);
    expect(lines(base(), edit((m) => { m.views[0].title = "Flow"; }))).toEqual(["renames the view «Board» to «Flow»"]);
    expect(lines(base(), edit((m) => { m.views[0].object = "units"; }))).toEqual(["moves the view «Board» to the record type «units»"]);
    expect(lines(base(), edit((m) => { m.views[0].block = "table"; }))).toEqual(["changes the view «Board» from «board» to «table»"]);
  });
  const rec = (verbs: string[], obj = "units") => verbs.map((v) => ({ id: `${obj}_${v}`, uses: `object:${obj}.${v}` }));
  const ALL = ["list", "get", "create", "update", "delete"];
  it("folds the five record actions of one record type into one line", () => {
    const withAll = edit((m) => { m.actions.push(...(rec(ALL) as never[])); });
    expect(lines(base(), withAll)).toEqual(["adds the record actions of «Units» (list, open, create, change, delete)"]);
  });
  it("lists only the record actions that are there", () => {
    const some = edit((m) => { m.actions.push(...(rec(["get", "list"]) as never[])); });
    expect(lines(base(), some)).toEqual(["adds the record actions of «Units» (list, open)"]);
  });
  it("keeps a custom action on its own line beside a full set", () => {
    const mix = edit((m) => { m.actions.push(...(rec(ALL) as never[]), { id: "ship", uses: "x.y", title: "Ship" } as never); });
    expect(lines(base(), mix)).toEqual(["adds the record actions of «Units» (list, open, create, change, delete)", "adds the action «Ship»"]);
  });
  it("folds a removal, and each record type on its own", () => {
    const withAll = edit((m) => { m.actions.push(...(rec(ALL) as never[]), ...(rec(["list"], "visits") as never[])); });
    expect(lines(withAll, base())).toEqual(["removes the record actions of «Units» (list, open, create, change, delete)", "removes the record actions of «visits» (list)"]);
  });
  it("reads the rule from uses and not from the id", () => {
    const odd = edit((m) => { m.actions.push({ id: "units_list", uses: "custom.thing" } as never); });
    expect(lines(base(), odd)).toEqual(["adds the action «units_list»"]);
    const renamed = edit((m) => { m.actions.push({ id: "see_all", uses: "object:units.list" } as never); });
    expect(lines(base(), renamed)).toEqual(["adds the record actions of «Units» (list)"]);
  });
  it("says makes for a first version, and counts the cap on folded lines", () => {
    const withAll = edit((m) => { m.actions.push(...(rec(ALL) as never[])); });
    expect(diffLines(diffManifests(null, withAll), 12, true)).toContain("makes the record actions of «Units» (list, open, create, change, delete)");
    const many = edit((m) => { for (let i = 0; i < 3; i++) m.objects.push({ name: `o${i}`, fields: [] } as never); m.actions.push(...(rec(ALL) as never[])); });
    const out = diffLines(diffManifests(base(), many), 3);
    expect(out).toHaveLength(4);
    expect(out[3]).toBe("and 1 more change");
  });
  it("adds and removes an action", () => {
    expect(lines(base(), edit((m) => { m.actions.push({ id: "n", uses: "x", title: "New" }); }))).toEqual(["adds the action «New»"]);
    const custom = edit((m) => { m.actions.push({ id: "n", uses: "x", title: "New" }); });
    expect(lines(custom, base())).toEqual(["removes the action «New»"]);
    expect(lines(base(), edit((m) => { m.actions.pop(); }))).toEqual(["removes the record actions of «visits» (open)"]);
  });
  it("says a generic line for any other part", () => {
    const withSkill = { ...base(), skill: { description: "x" } };
    expect(lines(base(), withSkill)).toEqual(["changes the skill of the app"]);
    expect(lines(base(), { ...base(), custom: 1 })).toEqual(["changes the custom"]);
  });
  it("lists what a first version makes", () => {
    const out = diffLines(diffManifests(null, base()), 12, true);
    expect(out).toEqual(["makes the record type «Units»", "makes the record type «visits»", "makes the view «Board»", "makes the record actions of «visits» (open)"]);
  });
  it("caps the list and counts the rest", () => {
    const big = edit((m) => { for (let i = 0; i < 15; i++) m.objects.push({ name: `o${i}`, fields: [] } as never); });
    const out = diffLines(diffManifests(base(), big), 12);
    expect(out).toHaveLength(13);
    expect(out[12]).toBe("and 3 more changes");
    expect(diffLines(diffManifests(base(), edit((m) => { m.objects.push({ name: "a", fields: [] } as never); m.objects.push({ name: "b", fields: [] } as never); })), 1)[1]).toBe("and 1 more change");
  });
  it("cuts a hostile name and drops its hidden characters", () => {
    expect(cleanName("A‮b​c\u0000d")).toBe("Abcd");
    expect(cleanName("x".repeat(80))).toHaveLength(36);
    expect(cleanName("«evil» and »more«")).not.toMatch(/[«»]/);
    const bad = edit((m) => { m.views.push({ id: "z", block: "table", title: `«x»‮${"y".repeat(60)}`, object: "visits" }); });
    const [line] = lines(base(), bad);
    expect(line).not.toContain("‮");
    expect(line.match(/«/g)).toHaveLength(1);
    expect(line.length).toBeLessThan(70);
  });
});
