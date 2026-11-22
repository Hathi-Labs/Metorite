/**
 * WFC-4a-2a fence: the inspector of a Studio write step.
 *
 * The owner of the workflow sees the form: a package picker, an action picker
 * and the data form of the object. Any other member, and the owner while access
 * loads, sees a note and the stored values, and no form. A viewer package, an
 * update and a stored step that the form cannot edit show no form either. The
 * step shows no `{{ref}}` hint, and it shows the three notes and the shape check.
 *
 * Mutation 1: draw the form for every role (drop the `isOwner` test of
 * `StudioWriteForm`). "shows no form to another member" turns red.
 * Mutation 2: call `onConfig({ [key]: value })` from the data form. "writes only
 * through the rules" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { clearAll, put } from "@/lib/dataCache";
import type { PackageDetail } from "@/lib/studio/realDoor";

import NodeInspector, { type StudioWriteContext } from "./NodeInspector";
import type { WriterRole } from "../lib/studioWrite";
import type { WorkflowGraphNode } from "../lib/types";

beforeEach(() => clearAll());

const PID = "11111111-1111-4111-8111-111111111111";

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

const detail = (over: Partial<PackageDetail> = {}): PackageDetail => ({
  id: PID,
  name: "Tickets",
  access: "owner",
  actions: [
    { id: "add_ticket", uses: "object:ticket.create" },
    { id: "edit_ticket", uses: "object:ticket.update" },
  ],
  objects: [
    {
      id: "o1",
      name: "ticket",
      label: "Ticket",
      fields: [
        { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
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

const stored = { package_id: PID, action: "add_ticket", data: { title: "Fix the door" } };

describe("the Studio write inspector", () => {
  it("shows the owner the pickers and the data form", () => {
    seed();
    const html = draw(write(stored), ctx("owner"));
    expect(html).toContain("Package that gets the record");
    expect(html).toContain("Action that adds the record");
    expect(html).toContain("Values for the new Ticket");
    expect(html).toContain("Use these values");
    expect(html).toContain("Fix the door");
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

  it("shows a read-only summary for a stored update", () => {
    seed();
    const html = draw(write({ ...stored, action: "edit_ticket", record_id: "r1" }), ctx("owner"));
    expect(html).not.toContain("Use these values");
    expect(html).toContain("The editor cannot change it yet");
    expect(html).toContain("edit_ticket");
    expect(html).toContain("r1");
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
});

describe("the source of the form", () => {
  const src = readFileSync(new URL("./StudioWriteForm.tsx", import.meta.url), "utf8");

  it("writes only through the rules", () => {
    expect(src.match(/onConfig\(/g) ?? []).toHaveLength(3);
    expect(src).toContain("onConfig(packagePatch(value))");
    expect(src).toContain("onConfig(actionPatch(value))");
    expect(src).toContain("onApply={(patch) => onConfig(patch)}");
    expect(src).toContain("onApply(result.patch)");
  });

  it("reads the package through the studio client, and asks the owner only", () => {
    expect(src).toContain("listPackages");
    expect(src).toContain("getPackage");
    expect(src).toContain("isOwner ? PACKAGES_KEY : null");
    expect(src).not.toMatch(/fetch\(\s*["'`]/);
    expect(src).not.toContain("/studio/actions");
  });

  it("takes no free text and no JSON", () => {
    expect(src).not.toMatch(/<(input|select|textarea|button)\b/);
    expect(src).not.toContain("JSON.parse");
  });
});
