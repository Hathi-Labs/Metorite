/**
 * The filter row's arrow rule — H-94 direction 2, pinned.
 *
 * ⚠️ Owner direction, 2026-08-26: *"Every dropdown in the row becomes a
 * button. At the default value, draw a two-headed arrow in place of the single
 * down arrow."*
 *
 * A row of four controls all showing a down chevron says nothing about which
 * of them are doing anything. The arrow is the only cue besides the active
 * tint, and it is the one that survives when colour does not — so it is worth
 * a test rather than a screenshot.
 *
 * ⚠️ **Why a pure function and not a rendered assertion.**
 * `vitest.config.ts` is `environment: "node"`, so there is no DOM to render
 * into. The alternative was a source-text fence, and this repo has now watched
 * two of those pass on a value they existed to reject — `"7to14d".isalnum()`
 * and a regex that could not see an f-string hole. A function that takes the
 * two inputs and returns the answer cannot lie about itself.
 */
import { describe, expect, it } from "vitest";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  SelectList,
  arrowFor,
  filterOptions,
  groupOptions,
  selectPanelReducer,
  type SelectOption,
} from "./SelectButton";

describe("arrowFor", () => {
  it("draws TWO heads at the default", () => {
    expect(arrowFor("", "")).toBe("ChevronsUpDown");
    expect(arrowFor("status", "status")).toBe("ChevronsUpDown");
  });

  it("draws ONE head once the control is doing something", () => {
    expect(arrowFor("assignee", "status")).toBe("ChevronDown");
    expect(arrowFor("todo", "")).toBe("ChevronDown");
  });

  it("⚠️ treats an empty default as a real default, not as absent", () => {
    // Status and Assignee both default to `""` — "Any status", "Anyone". A
    // falsy check instead of an equality check would give those two a single
    // chevron forever, which is exactly the state the row is meant to escape.
    expect(arrowFor("", "")).toBe("ChevronsUpDown");
  });

  it("is not fooled by a value that merely looks empty", () => {
    expect(arrowFor(" ", "")).toBe("ChevronDown");
    expect(arrowFor("0", "")).toBe("ChevronDown");
  });

  it("returns names the icon pack actually has", () => {
    // Both are Lucide names. A typo here renders nothing at all, and an
    // absent glyph in a 28px button is very easy to miss.
    for (const name of [arrowFor("a", "a"), arrowFor("a", "b")]) {
      expect(name).toMatch(/^Chevron/);
    }
  });
});

describe("the task body's Status list paints above the focused task", () => {
  it("passes layer=top, over My Tasks' TaskFocusModal at z-[80]", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const body = readFileSync(
      fileURLToPath(new URL("../../app/projects/components/TaskBody.tsx", import.meta.url)),
      "utf-8",
    );
    const status = body.match(/<SelectButton\s+label="Status"[\s\S]*?\/>/)?.[0] ?? "";
    expect(status).toContain('layer="top"');
  });
});

describe("filterOptions (WS-27bn R5b)", () => {
  const options = [
    { value: "", label: "Everyone", group: "Everyone" },
    { value: "person:a@x.test", label: "Ana Shah", hint: "a@x.test", group: "People" },
    { value: "team:hw", label: "Hardware team", group: "Teams" },
  ];

  it("keeps every option for an empty query", () => {
    expect(filterOptions(options, "  ")).toEqual(options);
  });

  it("matches the label or the hint, in any case", () => {
    expect(filterOptions(options, "SHAH").map((o) => o.value)).toEqual(["person:a@x.test"]);
    expect(filterOptions(options, "a@x").map((o) => o.value)).toEqual(["person:a@x.test"]);
    expect(filterOptions(options, "hard").map((o) => o.group)).toEqual(["Teams"]);
    expect(filterOptions(options, "zzz")).toEqual([]);
  });
});

describe("(4) every close clears the filter query (R5b-1 repair)", () => {
  const typed = selectPanelReducer(
    selectPanelReducer({ open: false, query: "" }, { type: "toggle" }),
    { type: "query", query: "ana" }
  );

  it("the query holds while the list is open", () => {
    expect(typed).toEqual({ open: true, query: "ana" });
  });

  it("an outside click, Escape or a pick clears it", () => {
    expect(selectPanelReducer(typed, { type: "close" })).toEqual({ open: false, query: "" });
  });

  it("a second click on the trigger clears it", () => {
    const shut = selectPanelReducer(typed, { type: "toggle" });
    expect(shut).toEqual({ open: false, query: "" });
    // And it opens again with no filter.
    expect(selectPanelReducer(shut, { type: "toggle" })).toEqual({ open: true, query: "" });
  });

  it("a closed list takes no query", () => {
    expect(selectPanelReducer({ open: false, query: "" }, { type: "query", query: "x" }).query).toBe("");
  });
});

describe("(5) the filter box sits above the listbox, and each group is a group", () => {
  const grouped: SelectOption[] = [
    { value: "", label: "Everyone", group: "Everyone" },
    { value: "person:a@x.test", label: "Ana", hint: "a@x.test", group: "People" },
    { value: "person:b@x.test", label: "Ben", group: "People" },
    { value: "team:hw", label: "Hardware team", group: "Teams" },
  ];
  const render = (options: SelectOption[], filtering: boolean, structured: boolean) =>
    renderToStaticMarkup(
      createElement(SelectList, {
        label: "Subject",
        listId: "L",
        value: "",
        options,
        filtering,
        structured,
        query: "",
        onQuery: () => {},
        onPick: () => {},
      })
    );

  it("groupOptions cuts runs in order", () => {
    expect(groupOptions(grouped).map((g) => [g.group, g.options.length])).toEqual([
      ["Everyone", 1],
      ["People", 2],
      ["Teams", 1],
    ]);
  });

  it("the input is outside role=listbox, and the groups are inside it", () => {
    const html = render(grouped, true, true);
    const input = html.indexOf("<input");
    const listbox = html.indexOf('role="listbox"');
    expect(input).toBeGreaterThanOrEqual(0);
    expect(listbox).toBeGreaterThan(input);
    // Nothing but options and groups inside the listbox.
    const inside = html.slice(listbox);
    expect(inside).not.toContain("<input");
    for (const g of ["Everyone", "People", "Teams"]) {
      expect(inside).toContain(`role="group" aria-label="${g}"`);
    }
    expect(inside.match(/role="option"/g)?.length).toBe(4);
  });

  it("a plain list renders option rows only, as before R5b", () => {
    const plain: SelectOption[] = [
      { value: "a", label: "A" },
      { value: "b", label: "B", hint: "2" },
    ];
    const html = render(plain, false, false);
    expect(html.startsWith('<button type="button" role="option"')).toBe(true);
    expect(html).not.toContain('role="listbox"');
    expect(html).not.toContain('role="group"');
    expect(html).not.toContain("<input");
  });
});
