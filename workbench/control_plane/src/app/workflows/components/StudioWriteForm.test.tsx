/**
 * WFC-4a-2a and WFC-4a-2b-1 fence: the inspector of a Studio write step.
 *
 * The owner of the workflow sees the form: a package picker, an action picker,
 * and then the data form of a create, or the record picker and the added
 * fields of a change. Any other member, and the owner while access loads, sees
 * a note and the stored values, with the record by its id only, and starts no
 * read. A viewer package, a delete, a `set` and an update with no list show no
 * form. The step shows no `{{ref}}` hint, and it shows the three notes and the
 * shape check.
 *
 * Mutations (each was run, and each turned this file red):
 * M1. Draw the form for every role (drop the `isOwner` test): "shows no form to another member".
 * M2. Call `onConfig({ [key]: value })` from a form: "writes only through the rules".
 * M3. Build the door from `owned.actions` and not `readActions(...)`: "reads through a door of reads".
 * M4. Put the title in the record patch: "writes only through the rules".
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { clearAll, put } from "@/lib/dataCache";
import { sourceKey, withMeta } from "@/lib/studio/dataDoor";
import { createRealDoor, readActions, type PackageDetail } from "@/lib/studio/realDoor";

import NodeInspector, { type StudioWriteContext } from "./NodeInspector";
import { recordGetSource, recordListSource, type WriterRole } from "../lib/studioWrite";
import type { WorkflowGraphNode } from "../lib/types";

beforeEach(() => clearAll());

const PID = "11111111-1111-4111-8111-111111111111";
const RID = "22222222-2222-4222-8222-222222222222";
const FAR = "33333333-3333-4333-8333-333333333333";

const write = (config: Record<string, unknown>): WorkflowGraphNode => ({
  id: "w",
  type: "studio_write",
  position: { x: 0, y: 0 },
  data: { label: "Add a record", config },
});

const ctx = (role: WriterRole, withApproval = true): StudioWriteContext => ({
  role,
  ownerEmail: "ravi@acme.test",
  nodes: [
    { id: "a", type: "approval" },
    { id: "w", type: "studio_write" },
    ...(withApproval ? [] : [{ id: "x", type: "output" }]),
  ],
  edges: withApproval ? [{ source: "a", target: "w" }] : [{ source: "x", target: "w" }],
});

const draw = (n: WorkflowGraphNode, studioWrite?: StudioWriteContext) =>
  renderToStaticMarkup(
    createElement(NodeInspector, {
      node: n,
      catalog: null,
      upstreamIds: [],
      issues: [],
      triggerKinds: [],
      onConfig: () => {},
      onLabel: () => {},
      onDelete: () => {},
      studioWrite,
    }),
  );

const ACTIONS = [
  { id: "add_ticket", uses: "object:ticket.create" },
  { id: "edit_ticket", uses: "object:ticket.update" },
  { id: "list_tickets", uses: "object:ticket.list" },
  { id: "get_ticket", uses: "object:ticket.get" },
  { id: "drop_ticket", uses: "object:ticket.delete" },
  { id: "close_ticket", uses: "object:ticket.update", set: { status: "closed" } },
  { id: "edit_visit", uses: "object:visit.update" },
];

const detail = (over: Partial<PackageDetail> = {}): PackageDetail => ({
  id: PID,
  name: "Tickets",
  access: "owner",
  actions: ACTIONS,
  objects: [
    {
      id: "o1",
      name: "ticket",
      label: "Ticket",
      fields: [
        { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
        { key: "done", type: "checkbox", label: "Done", required: false, many: false, options: null, to: null, hot: false },
      ],
      rules: [],
    },
    {
      id: "o2",
      name: "visit",
      label: "Visit",
      fields: [
        { key: "place", type: "text", label: "Place", required: false, many: false, options: null, to: null, hot: false },
      ],
      rules: [],
    },
  ],
  views: [],
  pages: [],
  nav: [],
  ...over,
});

const seed = (over: Partial<PackageDetail> = {}) => {
  put("/api/studio/packages", [
    { id: PID, name: "Tickets", status: "draft", live_version: null, updated_at: null },
  ]);
  put(`/api/studio/packages/${PID}`, detail(over));
};

/** The reads of the picker, as the read door keys them. */
const door = createRealDoor({ packageId: PID, actions: readActions(ACTIONS) });
const seedRecords = () => {
  put(
    sourceKey(door, recordListSource("ticket", 0)),
    withMeta([{ id: RID, title: "Fix the door" }, { id: "r2", title: "Order paper" }], { total: 2 }),
  );
  put(sourceKey(door, recordGetSource("ticket", FAR)), [{ id: FAR, title: "Replace the lock" }]);
};

const stored = { package_id: PID, action: "add_ticket", data: { title: "Fix the door" } };
const change = { package_id: PID, action: "edit_ticket", record_id: RID, data: { done: true } };

describe("the Studio write inspector: create", () => {
  it("shows the owner the pickers and the data form", () => {
    seed();
    const html = draw(write(stored), ctx("owner"));
    expect(html).toContain("Package that gets the record");
    expect(html).toContain("Action that writes the record");
    expect(html).toContain("Values for the new Ticket");
    expect(html).toContain("Use these values");
    expect(html).toContain("Fix the door");
    expect(html).not.toContain("Record that this step changes");
  });

  it("shows a blank step the package picker and nothing below it", () => {
    seed();
    const html = draw(write({ package_id: "", action: "", data: {} }), ctx("owner"));
    expect(html).toContain("Package that gets the record");
    expect(html).not.toContain("Use these values");
  });

  it("shows no form to another member, and names the owner", () => {
    seed();
    const html = draw(write(stored), ctx("other"));
    expect(html).not.toContain("Package that gets the record");
    expect(html).not.toContain("Use these values");
    expect(html).toContain("Only the owner of this workflow can set this step");
    expect(html).toContain("ravi@acme.test");
    expect(html).toContain("Fix the door");
  });

  it("shows no form while access loads", () => {
    seed();
    const html = draw(write(stored), ctx("loading"));
    expect(html).not.toContain("Use these values");
    expect(html).toContain("Checking who owns this workflow");
  });

  it("shows no form when the editor gives no context", () => {
    seed();
    expect(draw(write(stored))).not.toContain("Use these values");
  });

  it("shows no form for a viewer package", () => {
    seed({ access: "viewer" });
    const html = draw(write(stored), ctx("owner"));
    expect(html).not.toContain("Use these values");
    expect(html).toContain("you do not own it");
  });

  it("shows a read-only summary for a package that the owner does not own", () => {
    put("/api/studio/packages", []);
    const html = draw(write(stored), ctx("owner"));
    expect(html).not.toContain("Use these values");
    expect(html).toContain("not one that you own");
  });

  it("shows the three notes, and no {{ref}} hint", () => {
    seed();
    const html = draw(write(stored), ctx("owner"));
    expect(html).toContain("only when a schedule starts the workflow");
    expect(html).toContain("A Test run stops at this step.");
    expect(html).toContain("Each member who can open this workflow can read these values.");
    expect(html).not.toContain("Available data");
  });

  it("names the shape issue of a write with no approval above it", () => {
    seed();
    const html = draw(write(stored), ctx("owner", false));
    expect(html).toContain("must follow a Human approval step directly");
  });

  it("says that the owner approves each record when the shape is right", () => {
    seed();
    expect(draw(write(stored), ctx("owner"))).toContain("the owner approves each record");
  });

  it("names the update that has no list in one plain line", () => {
    seed();
    expect(draw(write(stored), ctx("owner"))).toContain("The editor cannot change a record of Visit");
  });
});

describe("the Studio write inspector: change (WFC-4a-2b-1)", () => {
  it("shows the owner the record picker with the title, and the added fields", () => {
    seed();
    seedRecords();
    const html = draw(write(change), ctx("owner"));
    expect(html).toContain("Record that this step changes");
    expect(html).toContain("Fix the door");
    expect(html).toContain("Fields to change");
    expect(html).toContain("New values for the Ticket");
    expect(html).toContain("Remove Done from this step");
    expect(html).not.toContain("Remove Title from this step");
    expect(html).not.toContain("Values for the new Ticket");
  });

  it("labels the action of a change by its verb", () => {
    seed();
    seedRecords();
    const html = draw(write(change), ctx("owner"));
    expect(html).toContain("Change a Ticket");
    expect(html).not.toContain("drop_ticket");
    expect(html).not.toContain("close_ticket");
  });

  it("starts an update with no field, and says that it changes none yet", () => {
    seed();
    seedRecords();
    const html = draw(write({ ...change, data: {} }), ctx("owner"));
    expect(html).toContain("This step changes no field yet.");
    expect(html).toContain("Add a field to change");
    expect(html).not.toContain("Remove Done from this step");
  });

  it("names a saved record that the page lacks by its title from the get read", () => {
    seed();
    seedRecords();
    const html = draw(write({ ...change, record_id: FAR }), ctx("owner"));
    expect(html).toContain("Replace the lock");
  });

  it("asks for a record when the update has none", () => {
    seed();
    seedRecords();
    const { record_id: _drop, ...noRecord } = change;
    void _drop;
    const html = draw(write(noRecord), ctx("owner"));
    expect(html).toContain("Pick the record that this step changes.");
    expect(html).toContain("Pick a record");
  });

  it("shows another member the record id only, and no title", () => {
    seed();
    seedRecords();
    const html = draw(write({ ...change, record_id: FAR }), ctx("other"));
    expect(html).toContain(FAR);
    expect(html).not.toContain("Replace the lock");
    expect(html).not.toContain("Record that this step changes");
    const near = draw(write(change), ctx("other"));
    expect(near).toContain(RID);
    expect(near).not.toContain("Fix the door");
  });

  it("shows the owner the record id only while access loads", () => {
    seed();
    seedRecords();
    const html = draw(write(change), ctx("loading"));
    expect(html).toContain(RID);
    expect(html).not.toContain("Fix the door");
  });

  it("shows a read-only summary for an update of an object with no list", () => {
    seed();
    const html = draw(write({ package_id: PID, action: "edit_visit", record_id: RID, data: {} }), ctx("owner"));
    expect(html).not.toContain("Use these values");
    expect(html).toContain("no list action");
  });

  it("shows a read-only summary for a stored delete and a stored set", () => {
    seed();
    for (const action of ["drop_ticket", "close_ticket"]) {
      const html = draw(write({ package_id: PID, action, record_id: RID, data: {} }), ctx("owner"));
      expect(html, action).not.toContain("Use these values");
      expect(html, action).toContain("does not offer");
    }
  });
});

describe("the source of the form", () => {
  const src = readFileSync(new URL("./StudioWriteForm.tsx", import.meta.url), "utf8");

  it("writes only through the rules", () => {
    expect(src.match(/onConfig\(/g) ?? []).toHaveLength(4);
    expect(src).toContain("onConfig(packagePatch(value))");
    expect(src).toContain("onConfig(actionPatch(value))");
    expect(src).toContain("onConfig(recordPatch(value))");
    expect(src).toContain("const apply = (patch: Record<string, unknown>) => onConfig(patch);");
    expect(src.match(/onApply\(result\.patch\)/g) ?? []).toHaveLength(2);
  });

  it("reads the package through the studio client, and asks the owner only", () => {
    expect(src).toContain("listPackages");
    expect(src).toContain("getPackage");
    expect(src).toContain("isOwner ? PACKAGES_KEY : null");
    expect(src).not.toMatch(/fetch\(\s*["'`]/);
    expect(src).not.toContain("/studio/actions");
  });

  it("reads the records through a door of reads, and never runs an action", () => {
    expect(src).toContain("keeper(packageId, readActions(owned.actions))");
    expect(src).not.toMatch(/\.run\(/);
    expect(src.match(/recordReadKey\(role, door, /g) ?? []).toHaveLength(2);
  });

  it("takes no free text and no JSON", () => {
    expect(src).not.toMatch(/<(input|select|textarea|button)\b/);
    expect(src).not.toContain("JSON.parse");
  });
});
