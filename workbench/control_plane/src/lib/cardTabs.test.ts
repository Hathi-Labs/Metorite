import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { cardKind, cutLabel, pickLevel, type TabLevel, KIND_ICON, personName, tabLabel, TAB_LABEL_MAX, restoredFirst, tabKeyTarget, tabsCount, tabsLabel } from "./cardTabs";

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
    // §18.10: a new-mail form is its own kind, told by the form's kind and
    // never by the title. Mutation: return "reply" for every form → red.
    expect(kind("Email to sharat@x.test", { kind: "new" })).toBe("new");
    expect(kind("Reply to 1 recipient?", { kind: "new" })).toBe("new");
    expect(KIND_ICON).toEqual({ send: "Mail", reply: "Reply", new: "Mail", task: "CheckSquare", reminder: "Bell", other: "ShieldCheck" });
  });

  it("a tab is a kind word and a short object, at most 24 characters", () => {
    const long = "«Send Neuropeak a Snowflake 3D printer quotation»";
    const rows = [
      { id: "cc-0", label: "Cc: ann@x.test", checked: true, kind: "cc" as const },
      { id: "to-0", label: "To: Sharat Satyanarayana <sharat@x.test>", checked: true, kind: "to" as const },
    ];
    expect(tabLabel({ title: "Send this new email?", rows })).toBe("Email to Sharat Satyana…");
    expect(tabLabel({ title: "Send this draft?", rows: [{ id: "to-0", label: "To: kim.lee@x.test", checked: true, kind: "to" as const }] })).toBe("Draft to Kim");
    expect(tabLabel({ title: "Send this new email?", context: "To: Kim <kim@x.test>, ann@x.test" })).toBe("Email to Kim");
    expect(tabLabel({ title: "Send this draft?" })).toBe("Draft");
    expect(tabLabel({ title: "Send this new email?" })).toBe("Email");
    const one = [{ id: "to-0", label: "To: kim@x.test", checked: true, kind: "to" as const }];
    expect(tabLabel({ title: "Send this draft at 10:00?", rows: one })).toBe("Scheduled to Kim");
    expect(tabLabel({ title: "Forward this email?", rows: one })).toBe("Forward to Kim");
    expect(tabLabel({ title: "Send this email?", rows: one })).toBe("Email to Kim");
    const reply = { messageId: "m", replyAll: false, recipients: { reply: { to: ["Pat Jones <pat@x.test>"], cc: [] }, reply_all: { to: ["z@x.test"], cc: [] } } };
    expect(tabLabel({ title: "Reply to 1 recipient?", reply: reply as never })).toBe("Reply to Pat Jones");
    // §18.10: the tab of a new-mail card reads "Email to", whatever the title.
    const fresh = { kind: "new", messageId: "", replyAll: true, recipients: { reply: { to: ["sharat.satyanarayana@x.test"], cc: [] }, reply_all: { to: ["sharat.satyanarayana@x.test"], cc: [] } } };
    expect(tabLabel({ title: "Reply to sharat@x.test?", reply: fresh as never })).toBe("Email to Sharat");
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

  it("the level follows the container width, with no oscillation (the P0 of round 3)", () => {
    // What each level needs, measured on the hidden copies: the count plus tabs.
    const NEEDS = [700, 420, 250];
    const settle = (room: number, from: TabLevel) => pickLevel(NEEDS, room, from);
    expect(settle(1060, 0)).toBe(0);
    expect(settle(600, 0)).toBe(1);
    expect(settle(300, 0)).toBe(2);
    expect(settle(260, 0)).toBe(2);
    expect(settle(240, 2)).toBe(2); // the fade is the last resort
    // A sweep 1060 -> 240 -> 1060: the level changes only at the thresholds.
    let level: TabLevel = 0;
    const seen: Array<[number, TabLevel]> = [];
    const down = Array.from({ length: 83 }, (_, i) => 1060 - i * 10);
    const up = Array.from({ length: 83 }, (_, i) => 240 + i * 10);
    for (const room of [...down, ...up]) {
      const was: TabLevel = level;
      level = settle(room, level);
      if (level !== was) seen.push([room, level]);
    }
    // Down: level 1 below 700, level 2 below 420. Up: level 1 from 428, 0 from 708.
    expect(seen).toEqual([[690, 1], [410, 2], [430, 1], [710, 0]]);
    // Held at one width, the level never changes again, from any start.
    for (let room = 240; room <= 1100; room += 7) {
      for (const from of [0, 1, 2] as const) {
        const once = settle(room, from);
        expect(settle(room, once)).toBe(once);
        expect(settle(room, settle(room, once))).toBe(once);
      }
    }
  });

  it("the level is chosen from the probe and the container, never from the row as drawn", () => {
    const tabs = read("components/ui/CardTabs.tsx");
    expect(tabs).toContain("data-card-tabs-probe");
    expect(tabs).toContain("pickLevel(needs, room, level)");
    expect(tabs).toContain("ro.observe(outer)");
    // The level is set from `pickLevel` alone, and from nothing the row drew.
    expect(tabs.match(/setLevel\(/g)).toHaveLength(1);
  });

  it("the row observes its width and lets go on unmount", () => {
    const tabs = read("components/ui/CardTabs.tsx");
    expect(tabs).toContain("new ResizeObserver");
    expect(tabs).toContain("ro.disconnect()");
    expect(tabs).toContain("aria-label={slim");
    expect(tabs).toContain("aria-label={tabsCount(waiting)}");
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
