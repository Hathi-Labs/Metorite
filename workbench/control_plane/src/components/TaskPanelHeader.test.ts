/**
 * The task panel's header: one order, one set of names, in both apps.
 *
 *   #ref [copy link] · app chips … · app actions · [expand] [close]
 *   Title (click to edit)
 *
 * Projects' `TaskPanel` and My Tasks' `ItemDetail` both render
 * `components/TaskPanelHeader.tsx`. Before 2026-09-24 they carried the same
 * controls in two orders, the close was "Close task" in one app and "Close
 * detail" in the other, and only My Tasks could rename a task in place.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EditableTaskTitle, TaskHeaderRow, titleToSave } from "./TaskPanelHeader";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
const code = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/])\/\/[^\n]*/g, "");

describe("the header row", () => {
  const html = renderToStaticMarkup(
    createElement(TaskHeaderRow, {
      taskRef: "#42",
      linkFor: () => "/projects?task=t1",
      chips: createElement("span", null, "CHIP"),
      actions: createElement("span", null, "ACTION"),
      expand: { expanded: false, onToggle: () => {} },
      onClose: () => {},
    }),
  );

  it("keeps one order: ref and copy, chips, actions, expand, close", () => {
    const at = (needle: string) => {
      const i = html.indexOf(needle);
      expect(i, `${needle} is missing`).toBeGreaterThan(-1);
      return i;
    };
    const order = [
      at("#42"),
      at('aria-label="Copy a link to this task"'),
      at("CHIP"),
      at("ACTION"),
      at('aria-label="Open as a full card"'),
      at('aria-label="Close task"'),
    ];
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("every icon button is the Button primitive", () => {
    const tags = [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);
    expect(tags).toHaveLength(3);
    for (const tag of tags) expect(tag).toContain("cc-control");
  });

  it("the close is `icon-sm` and says 'Close task'; the full card says so too", () => {
    const close = html.match(/<button\b[^>]*aria-label="Close task"[^>]*>/)?.[0] ?? "";
    // `icon-sm` is `p-1.5` in the primitive.
    expect(close).toContain("p-1.5");
    const full = renderToStaticMarkup(
      createElement(TaskHeaderRow, {
        taskRef: "Task",
        expand: { expanded: true, onToggle: () => {} },
      }),
    );
    expect(full).toContain('aria-label="Back to the side panel"');
    // No copy button without a link, and no close without a handler.
    expect(full).not.toContain("Copy a link");
    expect(full).not.toContain("Close task");
  });
});

describe("the title", () => {
  it("is an h2 holding the button that starts the edit", () => {
    const html = renderToStaticMarkup(
      createElement(EditableTaskTitle, { value: "Ship it", onSave: () => {} }),
    );
    expect(html).toMatch(/^<h2[^>]*><button[^>]*title="Click to edit"[^>]*><span[^>]*>Ship it<\/span>/);
  });

  it("saves the trimmed text, and nothing for an empty or unchanged one", () => {
    expect(titleToSave("  New name  ", "Old")).toBe("New name");
    expect(titleToSave("   ", "Old")).toBeNull();
    expect(titleToSave("Old ", "Old")).toBeNull();
  });
});

describe("both apps render it", () => {
  it("Projects' title is editable, through the board's own rename write", () => {
    const src = code(read("app/projects/components/TaskPanel.tsx"));
    expect(src).toMatch(/<TaskHeaderRow\b/);
    expect(src).toMatch(/<EditableTaskTitle value=\{task\.title\} onSave=\{\(t\) => void rename\(t\)\} \/>/);
    // The existing task-update path, and no new endpoint.
    expect(src).toMatch(/onChanged\(await projectsApi\.patchTask\(task\.id, \{ title \}\)\);/);
    // The old read-only title.
    expect(src).not.toMatch(/<h2[^>]*>\s*\{task\.title\}\s*<\/h2>/);
  });

  it("My Tasks renders the same header, with no raw button in it", () => {
    const src = code(read("app/tasks/components/ItemDetail.tsx"));
    const start = src.indexOf("<TaskHeaderRow");
    const end = src.indexOf("<EditableTaskTitle", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    expect(src.slice(start, end)).not.toMatch(/<button\b/);
    // The retired names.
    expect(src).not.toMatch(/Close detail|Open full page/);
    expect(src).not.toMatch(/function EditableTitle\b/);
  });
});
