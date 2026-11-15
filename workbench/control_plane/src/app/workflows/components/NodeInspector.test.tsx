/**
 * WFC-4a-1 fence: the inspector of a human step, and the note of a step
 * with no form.
 *
 * A human step shows a person picker over the directory of active members,
 * a title and an optional description. It shows no `{{ref}}` hint, because
 * the step takes fixed words only. The form writes through `humanStepEdit`
 * alone, so it writes the three keys and never a value with `{{`
 * (`lib/humanStep.test.ts` holds that rule).
 *
 * Mutation 1: call `onConfig({ [key]: value })` in `HumanStepForm` before the
 * check. "writes only through humanStepEdit" turns red.
 * Mutation 2: show the "Available data" hint for a human step. "shows no
 * {{ref}} hint" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { clearAll, put } from "@/lib/dataCache";
import type { DirectoryEntry } from "@/lib/rooms";

import NodeInspector from "./NodeInspector";
import { DIRECTORY_KEY } from "../lib/humanStep";
import type { NodeType, WorkflowGraphNode } from "../lib/types";

beforeEach(() => clearAll());

const node = (type: NodeType, config: Record<string, unknown> = {}): WorkflowGraphNode => ({
  id: `${type}_1`,
  type,
  position: { x: 0, y: 0 },
  data: { label: type, config },
});

const draw = (n: WorkflowGraphNode) =>
  renderToStaticMarkup(
    createElement(NodeInspector, {
      node: n,
      catalog: null,
      upstreamIds: ["start"],
      issues: [],
      triggerKinds: [],
      onConfig: () => {},
      onLabel: () => {},
      onDelete: () => {},
    }),
  );

const people: DirectoryEntry[] = [
  { subject: "ravi@acme.test", displayName: "Ravi", kind: "person" },
  { subject: "group:sales", displayName: "Sales", kind: "group" },
];

describe("the human step form", () => {
  it("shows the person, the title and the description, and no {{ref}} hint", () => {
    const html = draw(node("human_step", { assignee: "", title: "", description: "" }));
    expect(html).toContain("Person");
    expect(html).toContain("Title");
    expect(html).toContain("Description (optional)");
    expect(html).not.toContain("Available data");
    expect(html).not.toContain("{{trigger.*}}");
  });

  it("says that it loads the members before the first answer", () => {
    const html = draw(node("human_step"));
    expect(html).toContain("Loading the members of your organization");
    expect(html).toContain('role="status"');
  });

  it("offers the picker once the members are read", () => {
    put(DIRECTORY_KEY, people);
    const html = draw(node("human_step", { assignee: "", title: "Check it" }));
    expect(html).toContain("Pick a member");
    expect(html).not.toContain("Loading the members");
    expect(html).toContain('value="Check it"');
  });

  it("names the picked person on the picker", () => {
    put(DIRECTORY_KEY, people);
    const html = draw(node("human_step", { assignee: "ravi@acme.test", title: "Check it" }));
    expect(html).toContain("Ravi");
    expect(html).not.toContain("Pick a member");
  });
});

describe("a step with no form", () => {
  it.each(["pm_task", "pm_lifecycle"] as const)("%s says it cannot run", (type) => {
    const html = draw(node(type, { task_id: "t1" }));
    expect(html).toContain("This step cannot run in a workflow yet.");
    expect(html).not.toContain("Available data");
  });

  it("a step with a form keeps the {{ref}} hint", () => {
    const html = draw(node("approval", { message: "" }));
    expect(html).toContain("Available data");
  });
});

describe("the source of the form", () => {
  const src = readFileSync(new URL("./HumanStepForm.tsx", import.meta.url), "utf8");

  it("writes only through humanStepEdit", () => {
    expect(src.match(/onConfig\(/g) ?? []).toHaveLength(1);
    expect(src).toContain("onConfig(result.patch)");
  });

  it("reads the people through the one directory client", () => {
    expect(src).toContain("fetchDirectory");
    expect(src).not.toMatch(/fetch\(\s*["'`]/);
  });

  it("uses the shared controls", () => {
    expect(src).not.toMatch(/<(input|select|textarea|button)\b/);
  });
});
