import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { cardKind, cutLabel, nextLevel, START_FIT, type TabFit, KIND_ICON, personName, tabLabel, TAB_LABEL_MAX, restoredFirst, tabKeyTarget, tabsCount, tabsLabel } from "./cardTabs";

const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf-8");

describe("the tabs of one approval card (owner, 2026-11-03, D173)", () => {
  it("the count leads the row", () => {
    expect(tabsCount(3)).toBe("3 to approve");
    expect(tabsLabel(2, "changes")).toBe("2 changes to approve. Use the arrow keys to see each one.");
  });

  it("the arrow keys, Home and End move, and the ends do not wrap", () => {
    expect(tabKeyTarget("ArrowRight", 0, 3)).toBe(1);
    expect(tabKeyTarget("ArrowRight", 2, 3)).toBe(2);
    expect(tabKeyTarget("ArrowLeft", 0, 3)).toBe(0);
    expect(tabKeyTarget("ArrowLeft", 2, 3)).toBe(1);
    expect(tabKeyTarget("Home", 2, 3)).toBe(0);
    expect(tabKeyTarget("End", 0, 3)).toBe(2);
    expect(tabKeyTarget("a", 0, 3)).toBeNull();
    expect(tabKeyTarget("End", 0, 0)).toBeNull();
  });

  it("a card that comes back first opens, and a card that was there does not", () => {
    expect(restoredFirst(["a", "b"], ["x", "a", "b"])).toBe("x");
    expect(restoredFirst(["a", "b"], ["a", "b", "c"])).toBeNull();
    expect(restoredFirst(["a", "b"], ["b"])).toBeNull();
    expect(restoredFirst([], ["a"])).toBeNull();
  });

  it("each kind of card has its own icon, from ONE helper", () => {
    const kind = (title: string, reply?: object | null) => cardKind({ title, ...(reply !== undefined ? { reply: reply as never } : {}) });
    expect(kind("Send this new email?")).toBe("send");
    expect(kind("Send this draft?")).toBe("send");
    expect(kind("Send this draft at 10:00?")).toBe("send");
    expect(kind("Reply to 1 recipient?")).toBe("reply");
    expect(kind("Anything", null)).toBe("reply");
    expect(kind("Create this task?")).toBe("task");
    expect(kind("Watch this task?")).toBe("task");
    expect(kind("Set this reminder?")).toBe("reminder");
    expect(kind("Create this CRM lead?")).toBe("other");
    expect(kind("Send from which mailbox?")).toBe("other");
    expect(KIND_ICON).toEqual({ send: "Mail", reply: "Reply", task: "CheckSquare", reminder: "Bell", other: "ShieldCheck" });
  });

  it("a tab is a kind word and a short object, at most 24 characters", () => {
    const long = "«Send Neuropeak a Snowflake 3D printer quotation»";
    const rows = [
      { id: "cc-0", label: "Cc: ann@x.test", checked: true, kind: "cc" as const },
      { id: "to-0", label: "To: Sharat Satyanarayana <sharat@x.test>", checked: true, kind: "to" as const },
    ];
    expect(tabLabel({ title: "Send this new email?", rows })).toBe("Email to Sharat Satyana…");
    expect(tabLabel({ title: "Send this draft?", rows: [{ id: "to-0", label: "To: kim.lee@x.test", checked: true, kind: "to" as const }] })).toBe("Email to Kim");
    expect(tabLabel({ title: "Send this new email?", context: "To: Kim <kim@x.test>, ann@x.test" })).toBe("Email to Kim");
    expect(tabLabel({ title: "Send this draft?" })).toBe("Email");
    const reply = { messageId: "m", replyAll: false, recipients: { reply: { to: ["Pat Jones <pat@x.test>"], cc: [] }, reply_all: { to: ["z@x.test"], cc: [] } } };
    expect(tabLabel({ title: "Reply to 1 recipient?", reply: reply as never })).toBe("Reply to Pat Jones");
    expect(tabLabel({ title: "Create this task?", detail: `${long} · status Open` })).toBe("Task: Send Neuropeak a…");
    expect(tabLabel({ title: "Set this reminder?", detail: "Call Rajendra · Wed 10:00" })).toBe("Reminder: Call Rajendra");
    expect(tabLabel({ title: "Create this CRM lead?" })).toBe("Create this CRM lead");
    for (const l of [tabLabel({ title: "Create this task?", detail: long }), tabLabel({ title: "A very long card title that goes on and on?" })]) {
      expect(l.length).toBeLessThanOrEqual(TAB_LABEL_MAX);
    }
    expect(cutLabel("short", 24)).toBe("short");
    expect(personName("Kim <kim@x.test>")).toBe("Kim");
    expect(personName("kim@x.test")).toBe("Kim");
    expect(personName("sharat.satyanarayana@x.test")).toBe("Sharat");
    expect(personName("a_b-c@x.test")).toBe("A");
    expect(tabLabel({ title: "Reply to 1 recipient?", reply: { messageId: "m", replyAll: false, recipients: { reply: { to: ["pat.jones@x.test"], cc: [] }, reply_all: { to: [], cc: [] } } } as never })).toBe("Reply to Pat");
  });

  it("the row tightens by level as its room shrinks, and loosens with a margin", () => {
    // What the row needs at each level: the count plus the tabs.
    const NEED = [700, 420, 250] as const;
    const settle = (room: number, from: TabFit = START_FIT) => {
      let fit = from;
      for (let i = 0; i < 6; i++) fit = nextLevel(fit, NEED[fit.level], room);
      return fit;
    };
    expect(settle(1060).level).toBe(0);
    expect(settle(600).level).toBe(1);
    expect(settle(300).level).toBe(2);
    expect(settle(260).level).toBe(2);
    // Below level 2's need the fade is the last resort: the level stays 2.
    expect(settle(200).level).toBe(2);
    // Hysteresis: from level 2 the row loosens only with the margin.
    const tight = settle(300);
    expect(settle(427, tight).level).toBe(2);
    expect(settle(428, tight).level).toBe(1);
    expect(settle(707, settle(600)).level).toBe(1);
    expect(settle(708, settle(600)).level).toBe(0);
  });

  it("the row observes its width and lets go on unmount", () => {
    const tabs = read("components/ui/CardTabs.tsx");
    expect(tabs).toContain("new ResizeObserver");
    expect(tabs).toContain("ro.disconnect()");
    expect(tabs).toContain("aria-label={slim");
    expect(tabs).toContain("aria-label={tabsCount(total)}");
  });

  it("the row has a fade at an edge with more tabs, and a narrow tab width", () => {
    const tabs = read("components/ui/CardTabs.tsx");
    expect(tabs).toContain("data-[fade=right]:[mask-image");
    expect(tabs).toContain("max-w-[12rem]");
  });

  it("the queue draws ONE card with tabs, and no stack", () => {
    const q = read("components/ConfirmationQueue.tsx");
    expect(q).toContain("<CardTabs");
    expect(q).not.toContain("<CardStack");
    expect(q).not.toMatch(/swapStackCard|stackDepth|swipeStep|edgePose/);
    const tabs = read("components/ui/CardTabs.tsx");
    for (const word of ['role="tablist"', 'role="tab"', "aria-selected", "aria-controls", "tabIndex", "tabKeyTarget"]) {
      expect(tabs).toContain(word);
    }
    expect(tabs).not.toMatch(/onPointer|swapStackCard|data-card-stack-edge/);
  });

  it("the card carries the tab panel role and the tab row inside its frame", () => {
    const card = read("components/ConfirmationCard.tsx");
    expect(card).toContain("tabPanel");
    expect(card).toContain("{tabs}");
  });

  it("the stack's motion is gone from the CSS, and the spring still stops for reduced motion", () => {
    const css = read("app/globals.css");
    expect(css).not.toMatch(/card-stack-nudge|card-stack-lift|card-swap/);
    const reduce = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reduce).toContain("--spring-duration: 0.01ms");
  });
});
