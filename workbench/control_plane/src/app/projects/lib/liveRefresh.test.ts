/**
 * WS-64 #128 — the live refresh stopgap. A chat write reads at once, a wake
 * (focus, visible) reads at most once per window, and the two surfaces and
 * the chat cards are wired to it.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { PROJECTS_CHANGED_EVENT } from "@/components/projects/ProjectToolCards";

import { LIVE_REFRESH_MS, wireLiveRefresh } from "./liveRefresh";

function rig(visible = true) {
  const handlers: Record<string, Array<() => void>> = {};
  const add = (t: string, f: () => void) => (handlers[t] ??= []).push(f);
  const remove = (t: string, f: () => void) => {
    handlers[t] = (handlers[t] ?? []).filter((x) => x !== f);
  };
  const doc = { visibilityState: visible ? "visible" : "hidden", addEventListener: add, removeEventListener: remove };
  const win = { addEventListener: add, removeEventListener: remove };
  let now = 1_000_000;
  const fire = (t: string) => (handlers[t] ?? []).slice().forEach((f) => f());
  return { doc, win, clock: () => now, tick: (ms: number) => (now += ms), fire, handlers };
}

describe("wireLiveRefresh", () => {
  it("reads at once on a chat write, however soon the last read was", () => {
    const r = rig();
    let n = 0;
    wireLiveRefresh(() => n++, r);
    r.fire(PROJECTS_CHANGED_EVENT);
    r.fire(PROJECTS_CHANGED_EVENT);
    expect(n).toBe(2);
  });

  it("reads on focus and on visible, at most once per window", () => {
    const r = rig();
    let n = 0;
    wireLiveRefresh(() => n++, r);
    r.tick(LIVE_REFRESH_MS + 1);
    r.fire("focus");
    r.fire("visibilitychange");
    expect(n).toBe(1);
    r.tick(LIVE_REFRESH_MS - 1000);
    r.fire("focus");
    expect(n).toBe(1);
    r.tick(2000);
    r.fire("visibilitychange");
    expect(n).toBe(2);
  });

  it("does not read when the tab is hidden", () => {
    const r = rig(false);
    let n = 0;
    wireLiveRefresh(() => n++, r);
    r.tick(LIVE_REFRESH_MS * 2);
    r.fire("visibilitychange");
    expect(n).toBe(0);
  });

  it("removes every listener on cleanup", () => {
    const r = rig();
    const off = wireLiveRefresh(() => {}, r);
    off();
    expect(Object.values(r.handlers).every((h) => h.length === 0)).toBe(true);
  });
});

describe("the wiring", () => {
  const src = (p: string) => fs.readFileSync(path.join(__dirname, "..", "..", "..", p), "utf-8");

  it("the Projects page and My work both use the one hook", () => {
    expect(src("app/projects/page.tsx")).toContain("useLiveRefresh(");
    const host = src("app/projects/components/MyWorkHost.tsx");
    expect(host).toContain("useLiveRefresh(");
    expect(host).toContain("reloadItems");
  });

  it("a task card that changes a task announces it", () => {
    const cards = src("components/tasks/TaskToolCards.tsx");
    expect(cards).toContain("announceChange(e.id)");
    expect(cards).toContain("announceChange(`task-row:");
    expect(cards).toContain("announceChange(`plan:");
  });
});
