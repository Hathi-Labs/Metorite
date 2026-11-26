/**
 * The pin card and its hook (PKG-19a-2). The runner has no DOM, so the card
 * view is read as markup and as a tree, and the answers run without React.
 */
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { answerCard } from "./pinSuggestCard";
import { PinSuggestCardView } from "./PinSuggestCard";
import { acceptPin } from "./pinSuggestAccept";
import { pinSuggestOn } from "./pinSuggestFlag";
import { hiddenKey, hideSuggestion, readHidden, readOpens, recordOpen } from "./pinSuggestStore";
import { NAV_SECTIONS } from "@/lib/nav";
import { DAY_MS } from "./pinSuggest";

const sug = { href: "/tasks", opens: 6, why: "You opened Tasks 6 times this week. Pin it?" };

function mem() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
}

function findButtons(node: ReactNode, out: ReactElement<{ onClick?: () => void; children?: ReactNode }>[] = []) {
  if (Array.isArray(node)) node.forEach((n) => findButtons(n, out));
  else if (isValidElement(node)) {
    const el = node as ReactElement<{ onClick?: () => void; children?: ReactNode }>;
    if (el.props.onClick) out.push(el);
    findButtons(el.props.children, out);
  }
  return out;
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("PinSuggestCardView", () => {
  it("draws nothing without a suggestion", () => {
    const html = renderToStaticMarkup(createElement(PinSuggestCardView, { suggestion: null, onYes: () => {}, onNo: () => {} }));
    expect(html).toBe("");
  });
  it("draws the reason, a status region and the two buttons", () => {
    const html = renderToStaticMarkup(createElement(PinSuggestCardView, { suggestion: sug, onYes: () => {}, onNo: () => {} }));
    expect(html).toContain('role="status"');
    expect(html).toContain('data-testid="pin-suggest-card"');
    expect(html).toContain("You opened Tasks 6 times this week. Pin it?");
    expect(html).toContain(">Yes<");
    expect(html).toContain(">No<");
  });
  it("wires Yes and No to its two handlers", () => {
    const onYes = vi.fn();
    const onNo = vi.fn();
    const tree = PinSuggestCardView({ suggestion: sug, onYes, onNo });
    const buttons = findButtons(tree);
    expect(buttons).toHaveLength(2);
    buttons[0].props.onClick!();
    buttons[1].props.onClick!();
    expect(onYes).toHaveBeenCalledTimes(1);
    expect(onNo).toHaveBeenCalledTimes(1);
  });
});

describe("answerCard", () => {
  it("Yes calls accept once with the pane, and the card closes", async () => {
    const accept = vi.fn().mockResolvedValue(true);
    const hide = vi.fn();
    const close = vi.fn();
    await answerCard("yes", "/tasks", { accept, hide, close });
    expect(accept).toHaveBeenCalledTimes(1);
    expect(accept).toHaveBeenCalledWith("/tasks");
    expect(hide).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledTimes(1);
  });
  it("No stores the hide, writes no pin, and the card closes", async () => {
    const accept = vi.fn();
    const hide = vi.fn();
    const close = vi.fn();
    await answerCard("no", "/tasks", { accept, hide, close });
    expect(hide).toHaveBeenCalledWith("/tasks");
    expect(accept).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledTimes(1);
  });
  it("No hides for 30 days through the store", () => {
    const s = mem();
    const now = 1_000_000_000_000;
    hideSuggestion("a@b.co", "/tasks", now, s);
    expect(readHidden("a@b.co", now + 29 * DAY_MS, s)["/tasks"]).toBe(now + 30 * DAY_MS);
    expect(readHidden("a@b.co", now + 31 * DAY_MS, s)).toEqual({});
    expect(s.getItem(hiddenKey("a@b.co"))).toBeTruthy();
  });
});

describe("acceptPin", () => {
  const sections = NAV_SECTIONS;
  const held = sections.flatMap((s) => s.items.map((p) => p.href));
  it("saves once with the pane starred, through the given save", async () => {
    const save = vi.fn().mockResolvedValue({});
    const layout = { pins: [] as string[], known: [...held] };
    const href = held.find((h) => h === "/tasks") ?? held[1];
    const done = await acceptPin(href, { stored: { ...layout } as never, layout, sections }, save as never);
    expect(done).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0].pins).toContain(href);
  });
});

describe("the flag and the counter", () => {
  it("is off by default", () => {
    vi.stubEnv("NEXT_PUBLIC_PIN_SUGGEST", "");
    expect(pinSuggestOn()).toBe(false);
  });
  it("is on with the build value", () => {
    vi.stubEnv("NEXT_PUBLIC_PIN_SUGGEST", "1");
    expect(pinSuggestOn()).toBe(true);
  });
  it("the hook source counts once per route and guards on the flag", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("./usePinOpenCount.ts", import.meta.url), "utf8");
    expect(src).toMatch(/pinSuggestOn\(\)/);
    expect(src).toMatch(/last\.current === key/);
    expect(src.match(/recordOpen\(/g)).toHaveLength(1);
  });
  it("recordOpen counts a pane open and ignores a route outside the panes", () => {
    const s = mem();
    recordOpen("a@b.co", "/tasks/12", 5_000, s);
    recordOpen("a@b.co", "/nowhere/x", 5_001, s);
    expect(readOpens("a@b.co", s)).toEqual({ "/tasks": [5_000] });
  });
});
