/**
 * EntityPill (WS-27bm S9, spec §15): its role and name, its href, and a plain
 * click that navigates in this tab.
 *
 * vitest here is node-env with no DOM, so a click cannot be dispatched.
 * `ControlLink` is replaced by a stub that keeps the `onActivate` it was
 * handed, and the test calls it: that is the plain left click. The real
 * `ControlLink` decides which clicks those are, and `controlLink.test.ts`
 * holds that decision. The tokens-only rule is `theme/conformance.test.ts`,
 * which scans this file with every other one.
 */
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const activations: Array<() => void> = [];

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/ControlLink", () => ({
  ControlLink: ({
    href,
    onActivate,
    children,
    ...rest
  }: {
    href: string;
    onActivate: () => void;
    children?: ReactNode;
  } & Record<string, unknown>) => {
    activations.push(onActivate);
    return createElement("a", { href, ...rest }, children);
  },
}));

import EntityPill, { ENTITY_ICONS, isInAppPath, pillLinkName } from "./EntityPill";

const TASK = "/projects?task=0f8fad5b-d9cb-469f-a165-70867728950e";
const PROJECT = "/projects?project=5b0c7a52-3f4e-4d2a-9c1e-0a1b2c3d4e5f";

const render = (props: Parameters<typeof EntityPill>[0]) =>
  renderToStaticMarkup(createElement(EntityPill, props));

beforeEach(() => {
  push.mockClear();
  activations.length = 0;
});

describe("a pill that links", () => {
  it("is an anchor with the task's href and an accessible name", () => {
    const html = render({ kind: "task", label: "Notification engine", number: "#5", href: TASK });
    expect(html).toMatch(/^<a /);
    expect(html).toContain(`href="${TASK}"`);
    expect(html).toContain('aria-label="Open task #5 Notification engine in Projects"');
    expect(html).not.toContain("target=");
  });

  it("carries the project's href", () => {
    const html = render({ kind: "project", label: "Projects/Tasks App", href: PROJECT });
    expect(html).toContain(`href="${PROJECT}"`);
    expect(html).toContain("lucide-folder-kanban");
  });

  it("navigates in this tab on a plain click", () => {
    render({ kind: "task", label: "x", number: "#5", href: TASK });
    expect(activations).toHaveLength(1);
    activations[0]();
    expect(push).toHaveBeenCalledWith(TASK);
  });

  it("names the status for a screen reader", () => {
    expect(pillLinkName("task", "Fix it", "#2", "In progress")).toBe(
      "Open task #2 Fix it in Projects, status In progress",
    );
  });
});

describe("a pill that does not link", () => {
  it("is a span, with no router, when it has no href", () => {
    const html = render({ kind: "unknown", label: "Launch" });
    expect(html).toMatch(/^<span /);
    expect(activations).toHaveLength(0);
    // No hover layer: that says a chip clicks when it does not.
    expect(html).not.toContain("cc-control");
  });

  it("refuses to link anything but an in-app path", () => {
    for (const href of ["https://x.io", "//x.io", "/api/x", "javascript:alert(1)", "mailto:a@b.io"]) {
      expect(isInAppPath(href)).toBe(false);
      expect(render({ kind: "task", label: "x", href })).toMatch(/^<span /);
    }
    expect(isInAppPath(TASK)).toBe(true);
  });

  it("truncates a long name, and the tooltip holds all of it", () => {
    const long = "A".repeat(120);
    const html = render({ kind: "project", label: long });
    expect(html).toContain("truncate");
    expect(html).toContain("max-w-64");
    expect(html).toContain(`title="${long}"`);
  });
});

describe("the kind is never only a hue", () => {
  it("every kind but a person, a status and a plain chip wears an icon", () => {
    for (const kind of ["task", "space", "folder", "project", "subproject", "agent", "tag"] as const) {
      expect(ENTITY_ICONS[kind]).toBeTruthy();
    }
  });

  it("a person wears their initials", () => {
    const html = render({ kind: "person", label: "Vijay Varada", email: "v@x.io" });
    expect(html).toContain(">VV<");
    expect(html).toContain('title="Vijay Varada · v@x.io"');
  });

  it("a space, a folder and a project each have their own icon", () => {
    expect(new Set([ENTITY_ICONS.space, ENTITY_ICONS.folder, ENTITY_ICONS.project]).size).toBe(3);
  });
});
