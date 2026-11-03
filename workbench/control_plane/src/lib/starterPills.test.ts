/**
 * WS-62 CF-4b (`chat_first_surface.md` §4.6, D-CF-8): the starter pills of a
 * fresh chat, as one pure rule. Each branch of `starterPills` has a case
 * here. The source block in `lib/shell/assistant.test.ts` pins that
 * `AgentChat` reads the rule, and only with the flag on.
 * `e2e/chat-starter-pills.spec.ts` sees it in a browser.
 */
import { describe, expect, it } from "vitest";

import {
  ACTION_GROUPS,
  APP_TEMPLATES,
  KIND_TEMPLATES,
  STARTER_PILL_COUNT,
  pillOfCard,
  shortName,
  starterPills,
  type StarterCard,
} from "./starterPills";

const FIXED = ["What can you help me with?", "Summarize recent activity", "Create a new task"];

const card = (kind: string, title: string, over: Partial<StarterCard> = {}): StarterCard => ({
  app: "tasks",
  kind,
  group: "needs_you",
  title,
  ...over,
});

const FEED: StarterCard[] = [
  card("needs_reply", "Q3 budget", { app: "email", group: "now" }),
  card("deal_quiet", "Acme", { app: "crm" }),
  card("due_today", "Send the signed contract", { group: "ready" }),
  card("overdue", "Fourth card"),
];

const WORDS = (s: string) => s.trim().split(/\s+/).length;
const STARTS = /^[A-Z][a-z]+ /;

const LONG = "Prepare the quarterly revenue review for the whole leadership team before Friday";

describe("starterPills", () => {
  it("shows the first three cards that need action, in the feed's order", () => {
    expect(STARTER_PILL_COUNT).toBe(3);
    const pills = starterPills({ chatFirst: true, cards: FEED, fallback: FIXED });
    expect(pills.map((p) => p.label)).toEqual([
      "Draft a reply to “Q3 budget”",
      "Revive the “Acme” deal",
      "Finish “Send the signed contract” today",
    ]);
    expect(pills.map((p) => p.app)).toEqual(["email", "crm", "tasks"]);
  });

  it("never lets an fyi, later or handled card fill a slot", () => {
    const cards = [
      card("needs_reply", "Hello", { group: "fyi" }),
      card("overdue", "Late thing", { group: "later" }),
      card("overdue", "Closed thing", { group: "handled" }),
      card("due_today", "Real one", { group: "ready" }),
    ];
    const pills = starterPills({ chatFirst: true, cards, fallback: FIXED });
    expect(pills).toHaveLength(1);
    expect(pills[0].message).toContain("Real one");
    expect(ACTION_GROUPS).toEqual(["now", "needs_you", "ready"]);
  });

  it("gives fewer pills for fewer qualifying cards, never a filler", () => {
    expect(starterPills({ chatFirst: true, cards: FEED.slice(0, 1), fallback: FIXED })).toHaveLength(1);
    expect(starterPills({ chatFirst: true, cards: FEED.slice(0, 2), fallback: FIXED })).toHaveLength(2);
  });

  it("shows the fixed list when no card qualifies, with the flag off, while loading, and when empty", () => {
    const fixed = FIXED.map((s) => ({ label: s, message: s, app: null }));
    const fyi = [card("needs_reply", "Hello", { group: "fyi" })];
    expect(starterPills({ chatFirst: true, cards: fyi, fallback: FIXED })).toEqual(fixed);
    expect(starterPills({ chatFirst: false, cards: FEED, fallback: FIXED })).toEqual(fixed);
    expect(starterPills({ chatFirst: true, cards: undefined, fallback: FIXED })).toEqual(fixed);
    expect(starterPills({ chatFirst: true, cards: null, fallback: FIXED })).toEqual(fixed);
    expect(starterPills({ chatFirst: true, cards: [], fallback: FIXED })).toEqual(fixed);
  });

  it("drops a repeated label and takes the next card", () => {
    const cards = [
      card("overdue", "Same name"),
      card("overdue", "same name"),
      card("due_today", "Third item"),
    ];
    const pills = starterPills({ chatFirst: true, cards, fallback: FIXED });
    expect(pills.map((p) => p.label)).toEqual([
      "Get “Same name” moving again",
      "Finish “Third item” today",
    ]);
  });

  it("skips a card with no title when its kind needs a name, and keeps a nameless kind", () => {
    const cards = [card("overdue", "  "), card("no_plan_yet", "Nothing is planned for today", { app: "calendar" })];
    const pills = starterPills({ chatFirst: true, cards, fallback: FIXED });
    expect(pills.map((p) => p.label)).toEqual(["Plan my day"]);
    const broken = { app: "tasks", kind: "overdue", group: "now", title: undefined } as unknown as StarterCard;
    expect(pillOfCard(broken)).toBeNull();
  });
});

describe.each(Object.keys(KIND_TEMPLATES))("the template of kind %s", (kind) => {
  it("is a short request, with no ellipsis and no whole raw title", () => {
    for (const title of [LONG, "Budget"]) {
      const pill = pillOfCard(card(kind, title))!;
      expect(pill).not.toBeNull();
      expect(pill.label).toMatch(STARTS);
      expect(WORDS(pill.label)).toBeLessThanOrEqual(8);
      expect(pill.label).not.toMatch(/…|\.\.\./);
      expect(pill.label).not.toBe(title);
      expect(pill.label).not.toContain(LONG);
      expect(pill.label.toLowerCase()).not.toMatch(/\bnothing\b/);
    }
  });

  it("sends a message that names the item", () => {
    const pill = pillOfCard(card(kind, LONG))!;
    if (KIND_TEMPLATES[kind].nameless) expect(pill.message.length).toBeGreaterThan(20);
    else expect(pill.message).toContain(`"${LONG}"`);
    expect(pill.message).not.toMatch(/…|\.\.\./);
  });
});

describe("the labels and the messages", () => {
  it("puts a short name in the label and a long name only in the message", () => {
    expect(pillOfCard(card("needs_reply", "Q3 budget", { app: "email" }))!.label).toBe("Draft a reply to “Q3 budget”");
    const long = "Re: the very long subject of a mail about the annual offsite plan";
    const p = pillOfCard(card("needs_reply", long, { app: "email" }))!;
    expect(p.label).toBe("Draft a reply to “the very long subject”");
    // The message keeps the full name, with straight quotes.
    expect(p.message).toBe(`Draft a reply to the email "${long}" that waits for my answer.`);
  });

  it("shortens a very long name in the message at a word, with no ellipsis", () => {
    const long = "word ".repeat(80).trim();
    const p = pillOfCard(card("overdue", long))!;
    expect(p.message).not.toContain("…");
    expect(p.message.length).toBeLessThan(long.length);
  });

  it("makes a double quote in a name single, so the message stays whole", () => {
    expect(pillOfCard(card("overdue", 'Fix "login" bug'))!.message).toContain(`"Fix 'login' bug"`);
  });

  it("uses the app template for a kind it does not know, and skips an unknown app", () => {
    const p = pillOfCard(card("brand_new_kind", "Thing", { app: "crm" }))!;
    expect(p.label).toBe(APP_TEMPLATES.crm.plain);
    expect(p.message).toContain('"Thing"');
    expect(pillOfCard(card("brand_new_kind", "Thing", { app: "nowhere" }))).toBeNull();
    expect(pillOfCard(card("constructor", "Thing", { app: "nowhere" }))).toBeNull();
  });

  it("covers every feed app with a fallback template", () => {
    for (const app of ["tasks", "approvals", "projects", "email", "calendar", "crm", "people", "chat", "knowledge"]) {
      const p = pillOfCard(card("brand_new_kind", "Thing", { app }))!;
      expect(p.label).toMatch(/^(What|[A-Z][a-z]+) /);
      expect(WORDS(p.label)).toBeLessThanOrEqual(8);
      expect(p.app).toBe(app);
    }
  });
});

describe("long real titles keep a short name in the label (CF-4b repair)", () => {
  const CASES: Array<[string, string, string]> = [
    ["Call with Dr Sagar K G (Jain University) — 3D printer…", "needs_reply", "Draft a reply to “Call with Dr Sagar”"],
    ["FRACKTAL WORKS - PO 5100014449", "needs_reply", "Draft a reply to “FRACKTAL WORKS”"],
    ["Re: Fwd: Quote for 40 units of the V5 printer", "needs_reply", "Draft a reply to “Quote for 40 units”"],
  ];

  it.each(CASES)("%s", (title, kind, label) => {
    const p = pillOfCard(card(kind, title, { app: "email" }))!;
    expect(p.label).toBe(label);
    expect(p.label).not.toMatch(/…|\.\.\./);
    expect(p.message).toContain(`"${title.replace(/"/g, "'")}"`);
  });

  it("gives three different pills for three mails, with no duplicate drop", () => {
    const cards = CASES.map(([t]) => card("needs_reply", t, { app: "email" }));
    const labels = starterPills({ chatFirst: true, cards, fallback: FIXED }).map((p) => p.label);
    expect(labels).toEqual(CASES.map((c) => c[2]));
  });

  it("strips Re:, Fwd: and FW: prefixes, and cuts at a word with no ellipsis", () => {
    expect(shortName("RE: FW: Fwd: Budget review")).toBe("Budget review");
    expect(shortName("one two three four five six")).toBe("one two three four");
    expect(shortName("Averyveryverylongsingleworditemnamehere")).toBeNull();
    expect(shortName("Re: ")).toBeNull();
    expect(shortName("Solo | rest of it")).toBe("Solo rest of it");
  });

  it("keeps a named label within eight words, else the plain label", () => {
    const p = pillOfCard(card("absent_on_due", "Prepare the quarterly revenue review"))!;
    expect(p.label.split(/\s+/).length).toBeLessThanOrEqual(8);
  });
});
