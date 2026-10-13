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
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { createTaskRefResolver } from "@/lib/taskRefs";

import { EditableTaskTitle, TaskHeaderRow, titleKeyDown, titleToSave } from "./TaskPanelHeader";

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

describe("the breadcrumb — where the task lives", () => {
  const PATH = [
    { id: "s", name: "Fracktal" },
    { id: "p", name: "Metorite" },
    { id: "sp", name: "Assistant chat" },
  ];
  const render = (where?: Parameters<typeof TaskHeaderRow>[0]["where"]) =>
    renderToStaticMarkup(createElement(TaskHeaderRow, { taskRef: "#174", where }));

  it("runs from the space down to the project, then the ref", () => {
    // A card opened from the chat named nothing else (owner, 2026-10-12).
    const html = render({ path: PATH, onOpen: () => {} });
    expect(html).toContain('aria-label="Where this task is"');
    // The ref ends the trail, as a breadcrumb ends on the current page.
    expect(html).toMatch(/aria-current="page"[^>]*>[\s\S]*#174/);
    const order = ["Fracktal", "Metorite", "Assistant chat", "#174"].map((n) => html.indexOf(n));
    expect(order.every((i) => i > -1)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("each crumb is a Button that opens its node, and the project stands out", () => {
    const html = render({ path: PATH, onOpen: () => {} });
    const tags = [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);
    expect(tags).toHaveLength(3);
    for (const tag of tags) expect(tag).toContain("cc-control");
    expect(html).toContain('title="Open Metorite"');
    // Only the task's own project wears the foreground tone.
    expect(html.match(/font-medium text-foreground/g)).toHaveLength(1);
    expect(html).toMatch(/font-medium text-foreground">Assistant chat</);
  });

  it("is plain text with no opener, and absent with no path", () => {
    const text = render({ path: PATH });
    expect(text).toContain("Metorite");
    expect(text).not.toContain("<button");
    expect(render({ path: [] })).not.toContain("Where this task is");
    expect(render()).not.toContain("Where this task is");
  });
});

describe("the title", () => {
  it("is an h2 holding the button that starts the edit", () => {
    const html = renderToStaticMarkup(
      createElement(EditableTaskTitle, { value: "Ship it", onSave: () => {} }),
    );
    expect(html).toMatch(/^<h2[^>]*><button[^>]*title="Click to edit"[^>]*><span[^>]*>Ship it<\/span>/);
  });

  it("draws a #123 as the task pill, and keeps no link inside a button (WS-57 TR-5)", async () => {
    const resolver = createTaskRefResolver(async () => [
      { id: "aaaaaaaa-0000-4000-8000-000000000012", task_number: 12, title: "Twelve", status_name: "To do", category: "todo" },
    ]);
    resolver.request("", 12);
    resolver.request("", 9999);
    await resolver.flush();
    const html = renderToStaticMarkup(
      createElement(EditableTaskTitle, { value: "Ship #12, not #9999", onSave: () => {}, refResolver: resolver }),
    );
    expect(html).toMatch(/^<h2/);
    expect(html).toContain('href="/projects?task=aaaaaaaa-0000-4000-8000-000000000012"');
    expect(html).toContain("Twelve");
    expect(html).toContain("not #9999");
    // A link may not sit inside a button: the pencil is its own Button.
    expect(html).not.toMatch(/<button\b[^>]*>(?:(?!<\/button>)[\s\S])*<a\b/);
    expect(html).toMatch(/<button\b[^>]*aria-label="Edit title"[^>]*class="[^"]*cc-control/);
  });

  it("saves the trimmed text, and nothing for an empty or unchanged one", () => {
    expect(titleToSave("  New name  ", "Old")).toBe("New name");
    expect(titleToSave("   ", "Old")).toBeNull();
    expect(titleToSave("Old ", "Old")).toBeNull();
  });
});

describe("the title editor's keys", () => {
  const press = (key: string, shiftKey = false) => {
    const calls: string[] = [];
    titleKeyDown(
      {
        key,
        shiftKey,
        preventDefault: () => calls.push("preventDefault"),
        stopPropagation: () => calls.push("stopPropagation"),
      },
      { save: () => calls.push("save"), cancel: () => calls.push("cancel") },
    );
    return calls;
  };

  it("Escape cancels AND stops, so the panel does not close with it", () => {
    expect(press("Escape")).toEqual(["stopPropagation", "cancel"]);
  });

  it("Enter saves, Shift+Enter is a new line, other keys do nothing", () => {
    expect(press("Enter")).toEqual(["preventDefault", "save"]);
    expect(press("Enter", true)).toEqual([]);
    expect(press("a")).toEqual([]);
  });

  it("the textarea routes its keys through titleKeyDown", () => {
    const src = code(read("components/TaskPanelHeader.tsx"));
    expect(src).toMatch(/onKeyDown=\{\(e\) =>\s*titleKeyDown\(e, \{/);
  });
});

describe("both apps render it", () => {
  it("Projects' title is editable, through the board's own rename write", () => {
    const src = code(read("app/projects/components/TaskPanel.tsx"));
    expect(src).toMatch(/<TaskHeaderRow\b/);
    // WS-57 TR-5: the title's `#123` pills read the task's root store.
    expect(src).toMatch(
      /<EditableTaskTitle value=\{task\.title\} onSave=\{\(t\) => void rename\(t\)\}\s+refResolver=\{taskRefResolverFor\(task\.root_project_id\)\} \/>/,
    );
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
