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

  it("aligns the top of a taller target that starts below the port", () => {
    expect(nearestScrollTop(0, port, { top: 900, bottom: 2000 })).toBe(800);
  });

  it("leaves a taller target that covers both edges of the port", () => {
    expect(nearestScrollTop(400, port, { top: 50, bottom: 800 })).toBe(400);
  });

  it("aligns the bottom of a taller target whose top is above the port", () => {
    // 1100px tall in a 600px port. Its bottom shows at 600, so the port moves
    // up by 100 to align the bottom edges, as CSSOM block "nearest" does.
    expect(nearestScrollTop(1000, port, { top: -500, bottom: 600 })).toBe(900);
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
    const blocks = [...css.matchAll(/(?:^|\n)html,\s*body\s*\{([^}]*)\}/g)].map((m) => m[1]);
    expect(blocks.some((b) => /(^|[\s;])overflow:\s*clip\s*(;|$)/.test(b))).toBe(true);
  });

  it("positions and clips every shell root, one window tall", () => {
    const shell = read("components/AppShell.tsx");
    const roots = [...shell.matchAll(/data-app-shell=""\s+className=(\{[^}]*\}|"[^"]*")/g)].map((m) => m[1]);
    // The desktop root and the phone root.
    expect(roots).toHaveLength(2);
    for (const root of roots) {
      // The class strings, not the `"full"` of the frame test.
      const classes = quoted(root).filter((c) => tokens(c).has("flex"));
      expect(classes.length).toBeGreaterThan(0);
      for (const cls of classes) {
        expect([...tokens(cls)]).toEqual(expect.arrayContaining(["relative", "overflow-clip"]));
        expect(tokens(cls).has("overflow-hidden")).toBe(false);
      }
    }
    // The desktop root and the chromeless main follow the iOS toolbar.
    const desktop = quoted(roots[0]).filter((c) => tokens(c).has("flex"));
    for (const cls of desktop) expect(tokens(cls).has("h-dvh")).toBe(true);
    const chromeless = shell.match(/<main className="([^"]*)">\{children\}<\/main>/);
    expect(tokens(chromeless?.[1] ?? "").has("h-dvh")).toBe(true);
  });

  it("clips the email layout and the reading pane", () => {
    const page = classNames(read("app/email/page.tsx")).filter((c) =>
      ["h-full", "w-full", "select-none"].every((t) => tokens(c).has(t)));
    expect(page).toHaveLength(1);
    expect(tokens(page[0]).has("overflow-clip")).toBe(true);
    const detail = read("app/email/components/EmailDetail.tsx");
    const root = detail.match(/<div className="([^"]*)">\s*\{\/\* ── Main toolbar/);
    expect(tokens(root?.[1] ?? "").has("overflow-clip")).toBe(true);
  });

  it("holds a wheel in the thread, and lets the draft pass it on", () => {
    const detail = read("app/email/components/EmailDetail.tsx");
    const thread = detail.match(/data-email-thread=""\s+className="([^"]*)"/);
    expect([...tokens(thread?.[1] ?? "")]).toEqual(expect.arrayContaining(["overflow-y-auto", "overscroll-contain"]));
    // Reviewer P2: on a short window the member scrolls past the end of the
    // draft to the AI bar and Send, so the draft must not hold the wheel.
    const draft = detail.match(/placeholder=\{`Write your[\s\S]*?className="([^"]*)"/);
    expect(draft).not.toBeNull();
    expect(tokens(draft?.[1] ?? "").has("overscroll-contain")).toBe(false);
  });

  it("holds the sr-only summary of DraftAssistant inside the composer", () => {
    const assistant = read("app/email/components/DraftAssistant.tsx");
    expect(classNames(assistant)).toContain("sr-only");
    // The panel root is the one with the top border and the primary tint.
    const roots = [...assistant.matchAll(/return \(\s*<div className="([^"]*)"/g)]
      .map((m) => m[1])
      .filter((c) => tokens(c).has("border-t"));
    expect(roots).toHaveLength(1);
    expect(tokens(roots[0]).has("relative")).toBe(true);
  });
});

/** The class tokens of one class string. Order and spacing do not count. */
function tokens(cls: string): Set<string> {
  return new Set(cls.split(/\s+/).filter(Boolean));
}

/** Each double-quoted string in a JSX `className` expression. */
function quoted(expr: string): string[] {
  return [...expr.matchAll(/"([^"]*)"/g)].map((m) => m[1]);
}

/** Each literal `className="…"` in a source file. */
function classNames(src: string): string[] {
  return [...src.matchAll(/className="([^"]*)"/g)].map((m) => m[1]);
}
