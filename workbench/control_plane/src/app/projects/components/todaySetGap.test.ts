/**
 * WS-MDC CC-2 fix round 2: the Today chip says when the read of the set
 * failed, and holds the place of each task that the read still brings.
 *
 * The rule is `todaySetGap` in `lib/myTasks.ts`, and the markup is
 * `TodaySetGap.tsx`. The vitest environment has no DOM, so the markup renders
 * with `renderToStaticMarkup`, and Retry is called through the element tree.
 *
 * Mutations, run by hand on 2026-11-07 (each turned this file red, then was
 * restored):
 * - `todaySetGap` answers `failed: false` for an error: the failed cases fail.
 * - `todaySetGap` answers `pending: 0` while the read is on its way: the
 *   pending cases fail.
 * - `TodaySetGap` draws no Retry: the Retry case fails.
 */

import fs from "node:fs";
import path from "node:path";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import Button from "@/components/ui/Button";

import { TODAY_SET_FAILED, todaySetGap } from "../lib/myTasks";
import { TodaySetGap } from "./TodaySetGap";

const html = (el: ReactElement) => renderToStaticMarkup(el);
const escaped = (s: string) => s.replace(/'/g, "&#x27;");
const rows = (...ids: string[]) => ids.map((id) => ({ id }));
const ids = (...list: string[]) => new Set(list);

/** Each element of the tree that a plain function component returns. */
function elements(node: ReactNode): ReactElement[] {
  if (!isValidElement(node)) {
    return Array.isArray(node) ? node.flatMap(elements) : [];
  }
  const props = node.props as { children?: ReactNode };
  return [node, ...elements(props.children)];
}

describe("the gap rule of the Today chip", () => {
  it("is no gap with the flag off, on another chip, or with the set read", () => {
    const none = { failed: false, pending: 0 };
    expect(todaySetGap(rows("a"), "today", null, { error: "boom" })).toEqual(none);
    expect(todaySetGap(rows("a"), "next", ids("a", "b"), { error: "boom" })).toEqual(none);
    expect(todaySetGap(rows("a"), "today", ids("a", "b"), { data: { rows: [] }, error: "boom" })).toEqual(none);
    // The list holds every id of the set: nothing to wait for or to lose.
    expect(todaySetGap(rows("a", "b"), "today", ids("a", "b"), { error: "boom" })).toEqual(none);
  });

  it("holds one place for each id that the list lacks while the read is on its way", () => {
    expect(todaySetGap(rows("a"), "today", ids("a", "b", "c"), {})).toEqual({ failed: false, pending: 2 });
    expect(todaySetGap([], "today", ids("x"), { error: null })).toEqual({ failed: false, pending: 1 });
  });

  it("says the read failed when the chip lacks rows that it counts", () => {
    expect(todaySetGap(rows("a"), "today", ids("a", "b"), { error: "Gateway error 502" })).toEqual({
      failed: true,
      pending: 0,
    });
  });
});

describe("the markup of the gap", () => {
  it("the failed read draws one line that says what happened and what to do, and a Retry", () => {
    const out = html(createElement(TodaySetGap, { gap: { failed: true, pending: 0 }, onRetry: () => undefined }));
    expect(TODAY_SET_FAILED).toBe(
      "Metorite could not load all of today's tasks. The tasks assigned to you show below.",
    );
    expect(out).toContain(escaped(TODAY_SET_FAILED));
    expect(out).toContain("data-today-set-failed");
    expect(out).toContain(">Retry<");
    expect(out).not.toContain("data-today-set-placeholder");
  });

  it("Retry reads the set again", () => {
    const onRetry = vi.fn();
    const tree = TodaySetGap({ gap: { failed: true, pending: 0 }, onRetry });
    const retry = elements(tree).filter((el) => el.type === Button);
    expect(retry).toHaveLength(1);
    (retry[0].props as { onClick: () => void }).onClick();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("the space hands Retry the read of the set", () => {
    const space = fs.readFileSync(path.join(__dirname, "MyTasksSpace.tsx"), "utf8");
    expect(space).toContain("<TodaySetGap gap={gap} onRetry={todaySet.refresh} />");
    expect(space).toContain("todaySetGap(list.rows, chip, todayIds, { data: todaySet.data, error: todaySet.error })");
  });

  it("the pending read draws one row of the list's height for each missing task", () => {
    const out = html(createElement(TodaySetGap, { gap: { failed: false, pending: 3 }, onRetry: () => undefined }));
    expect(out.match(/data-today-set-placeholder/g)).toHaveLength(3);
    // A row of `TaskList`: `py-2` around a 20 px title line and a 16 px path line.
    expect(out).toContain('class="border-b border-border px-3 py-2"');
    expect(out).toContain("flex h-5 items-center");
    expect(out).toContain("flex h-4 items-center");
    expect(out).toContain('aria-busy="true"');
    expect(out).not.toContain(escaped(TODAY_SET_FAILED));
  });

  it("no gap draws nothing", () => {
    expect(html(createElement(TodaySetGap, { gap: { failed: false, pending: 0 }, onRetry: () => undefined }))).toBe("");
  });
});
