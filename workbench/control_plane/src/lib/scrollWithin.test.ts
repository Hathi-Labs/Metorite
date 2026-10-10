/**
 * No ancestor of the app shell scrolls (owner report, 2026-10-10).
 *
 * The owner scrolled in an AI draft of a reply, and the whole app moved up
 * and left a blank band at the bottom. Two causes, and this file fences both:
 *
 *   1. A box escaped the shell. `DraftAssistant` drew an `sr-only` summary,
 *      which is `position: absolute`, and no ancestor was positioned. So it
 *      sat in the long thread, below the window, and made the document taller
 *      than the window. A wheel at the end of the draft then scrolled `html`.
 *   2. `scrollIntoView` scrolls every scrollable ancestor, and an
 *      `overflow: hidden` box is one for a script. So a reveal of the
 *      composer could shift the shell too.
 *
 * The behavioural fence is `e2e/email-composer-scroll.spec.ts`, which drives
 * the real page in Chromium. This file holds the helper and the source half,
 * because vitest here runs in node and has no layout.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { nearestScrollTop, revealWithin, scrollToEnd, type ScrollBox } from "./scrollWithin";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

function filesUnder(rel: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(join(SRC, rel));
  return out;
}

/** A scroller in viewport pixels, with a record of each `scrollTo`. */
function fakeScroller(top: number, height: number, scrollTop: number, scrollHeight = 5000) {
  const calls: Array<{ top: number; behavior?: ScrollBehavior }> = [];
  const box: ScrollBox & { calls: typeof calls } = {
    scrollTop,
    scrollHeight,
    clientTop: 0,
    clientHeight: height,
    getBoundingClientRect: () => ({ top }),
    scrollTo: (o) => {
      calls.push(o);
    },
    calls,
  };
  return box;
}

const at = (top: number, bottom: number) => ({ getBoundingClientRect: () => ({ top, bottom }) });

describe("nearestScrollTop", () => {
  const port = { top: 100, bottom: 700 };

  it("leaves a target that shows where it is", () => {
    expect(nearestScrollTop(50, port, { top: 200, bottom: 400 })).toBe(50);
  });

  it("scrolls down by the part below the port", () => {
    expect(nearestScrollTop(50, port, { top: 600, bottom: 800 })).toBe(150);
  });

  it("scrolls up by the part above the port", () => {
    expect(nearestScrollTop(300, port, { top: 40, bottom: 200 })).toBe(240);
  });

  it("aligns the top of a target that is taller than the port", () => {
    expect(nearestScrollTop(0, port, { top: 900, bottom: 2000 })).toBe(800);
  });
});

describe("revealWithin and scrollToEnd", () => {
  it("scrolls only the scroller it gets, by the least distance", () => {
    const thread = fakeScroller(100, 600, 50);
    revealWithin(thread, at(600, 800), "smooth");
    expect(thread.calls).toEqual([{ top: 150, behavior: "smooth" }]);
  });

  it("does not scroll when the target already shows", () => {
    const thread = fakeScroller(100, 600, 50);
    revealWithin(thread, at(200, 300));
    expect(thread.calls).toEqual([]);
  });

  it("does nothing when the scroller or the target is missing", () => {
    expect(() => revealWithin(null, at(0, 1))).not.toThrow();
    const thread = fakeScroller(0, 100, 0);
    revealWithin(thread, null);
    scrollToEnd(null);
    expect(thread.calls).toEqual([]);
  });

  it("scrolls to the end of the scroller", () => {
    const thread = fakeScroller(0, 600, 0, 4200);
    scrollToEnd(thread, "smooth");
    expect(thread.calls).toEqual([{ top: 4200, behavior: "smooth" }]);
  });
});

describe("no ancestor of the app shell scrolls", () => {
  // The email app, the shell, and the chat that the email app shows. A
  // `scrollIntoView` in them can move a box above the scroller it means.
  const SCOPES = [
    ...filesUnder("app/email"),
    ...filesUnder("lib/shell"),
    "components/AppShell.tsx",
    "components/AgentChat.tsx",
    "components/AskPin.tsx",
  ];

  it("has no scrollIntoView in the email app, the shell or the chat", () => {
    const offenders = SCOPES.filter((rel) => /\.scrollIntoView\s*\(/.test(read(rel)));
    expect(offenders).toEqual([]);
  });

  it("clips the document, so a wheel at the end of a pane cannot scroll it", () => {
    const css = read("app/globals.css");
    expect(css).toMatch(/html,\s*body\s*\{\s*overflow:\s*clip;\s*\}/);
  });

  it("positions and clips every shell root", () => {
    const shell = read("components/AppShell.tsx");
    const roots = [...shell.matchAll(/<div data-app-shell="" className=(\{[^}]*\}|"[^"]*")/g)].map((m) => m[1]);
    // The desktop root and the phone root.
    expect(roots).toHaveLength(2);
    for (const root of roots) {
      expect(root).not.toMatch(/overflow-hidden/);
      // The class strings, not the `"full"` of the frame test.
      const classes = (root.match(/"[^"]*"/g) ?? []).filter((s) => /\bflex\b/.test(s));
      expect(classes.length).toBeGreaterThan(0);
      for (const cls of classes) {
        expect(cls).toMatch(/\brelative\b/);
        expect(cls).toMatch(/\boverflow-clip\b/);
      }
    }
  });

  it("clips the email layout and the reading pane", () => {
    expect(read("app/email/page.tsx")).toContain('<div className="flex h-full w-full bg-background overflow-clip select-none">');
    expect(read("app/email/components/EmailDetail.tsx")).toContain('<div className="flex flex-col h-full overflow-clip">');
  });

  it("keeps a wheel in the thread and in the draft", () => {
    const detail = read("app/email/components/EmailDetail.tsx");
    const thread = detail.match(/data-email-thread=""\s+className="([^"]*)"/);
    expect(thread?.[1]).toMatch(/\boverflow-y-auto\b/);
    expect(thread?.[1]).toMatch(/\boverscroll-contain\b/);
    const draft = detail.match(/placeholder=\{`Write your[\s\S]*?className="([^"]*)"/);
    expect(draft?.[1]).toMatch(/\boverscroll-contain\b/);
  });

  it("holds the sr-only summary of DraftAssistant inside the composer", () => {
    const assistant = read("app/email/components/DraftAssistant.tsx");
    expect(assistant).toContain('<span className="sr-only">');
    expect(assistant).toMatch(/return \(\s*<div className="relative border-t border-border/);
  });
});
