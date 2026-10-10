/**
 * The presets (NS-7, `navigation_shell.md` §8.1 and §8.4). The owner approved
 * the table on 2026-10-10, and these cases hold it.
 *
 * ⚠️ **The subset rule is the one that matters.** A preset never grants. For
 * every preset and every grant set below, what the shell draws from the
 * preset is a subset of `visibleSections(features, isAdmin)`: the pins in
 * "My apps", the sidebar's whole shape, the My Day cards and the jobs of the
 * empty command bar.
 *
 * Mutation, seen to fail first: make `pinnedPanes` look a pin up in `PANES`
 * instead of in `sections`, and "draws no pin outside visibleSections" fails
 * for every preset with no grants.
 */
import { describe, expect, it } from "vitest";

import { PANES, visibleSections, type NavSection } from "@/lib/nav";

import { cardsFor } from "./myDay";
import {
  ANSWERS,
  BUILT_CARDS,
  CARD_KEYS,
  EMPTY_SHELL,
  PRESETS,
  PRESET_IDS,
  QUESTION,
  orderCards,
  pinnedPanes,
  presetById,
  presetForAnswer,
  presetForRole,
  roleDefault,
  shellLayout,
  togglePin,
} from "./presets";
import { JOBS, buildItems, heldPanes, rank } from "./registry";
import { shellSidebar } from "./shellNav";

const ALL = PANES.map((p) => p.feature).filter((f): f is string => !!f);

/** Grant sets from none to full, with and without the admin flag. */
const GRANTS: readonly { name: string; features: string[]; admin: boolean }[] = [
  { name: "no grants", features: [], admin: false },
  { name: "a guest with chat only", features: ["chat"], admin: false },
  { name: "tasks and email", features: ["tasks", "email"], admin: false },
  { name: "projects and people", features: ["projects", "people"], admin: false },
  { name: "approvals but not an admin", features: ["approvals", "email"], admin: false },
  { name: "everything, not an admin", features: ALL, admin: false },
  { name: "everything, an admin", features: ALL, admin: true },
  { name: "an admin with no features", features: [], admin: true },
];

const hrefs = (sections: readonly NavSection[]) => new Set(sections.flatMap((s) => s.items.map((p) => p.href)));

describe("the subset rule: a preset never draws what the member does not hold (§8.1)", () => {
  for (const preset of PRESETS) {
    for (const g of GRANTS) {
      for (const preview of [false, true]) {
        const sections = visibleSections(g.features, g.admin, preview);
        const held = hrefs(sections);
        const label = `${preset.label}, ${g.name}${preview ? ", preview apps restored" : ""}`;

        it(`${label}: draws no pin outside visibleSections`, () => {
          for (const p of pinnedPanes(preset.pins, sections)) expect(held.has(p.href)).toBe(true);
        });

        it(`${label}: the sidebar with its pins is still a subset`, () => {
          const drawn = shellSidebar(sections, preset.pins).flatMap((s) => s.items.map((p) => p.href));
          for (const h of drawn) expect(held.has(h)).toBe(true);
          // And it draws each held app once: a pin moves the app, never copies it.
          expect(new Set(drawn).size).toBe(drawn.length);
        });

        it(`${label}: the empty command bar offers only held jobs`, () => {
          const items = buildItems(heldPanes(sections), true);
          const ranked = rank({ items, query: "", context: null, recent: [], order: preset.jobs });
          for (const item of ranked) expect(held.has(item.app)).toBe(true);
        });
      }
    }

    it(`${preset.label}: orders only the cards the member gets, and hides none`, () => {
      for (const g of GRANTS) {
        const cards = cardsFor(g.features);
        const available = BUILT_CARDS.filter((k) => cards[k as keyof typeof cards]);
        const drawn = orderCards(available, preset.cards);
        expect([...drawn].sort()).toEqual([...available].sort());
      }
    });
  }

  it("draws a preview pin only when the app is promoted (CRM)", () => {
    const sales = presetById("sales-manager")!;
    expect(sales.pins).toContain("/crm");
    const live = pinnedPanes(sales.pins, visibleSections(ALL, false, false)).map((p) => p.href);
    expect(live).not.toContain("/crm");
    const promoted = pinnedPanes(sales.pins, visibleSections(ALL, false, true)).map((p) => p.href);
    expect(promoted).toContain("/crm");
  });

  it("never pins a page about the member, even when one is named", () => {
    const sections = visibleSections(ALL, true);
    expect(pinnedPanes(["/people/me", "/settings/appearance", "/tasks"], sections).map((p) => p.href)).toEqual(["/tasks"]);
  });
});

describe("the eight presets hold the approved table (owner, 2026-10-10)", () => {
  const labels = (pins: readonly string[]) =>
    pins.map((h) => PANES.find((p) => p.href === h)?.label ?? `missing ${h}`);

  it("has eight presets, in the table's order", () => {
    expect(PRESETS.map((p) => p.label)).toEqual([
      "Founder",
      "Sales manager",
      "Marketing lead",
      "Finance manager",
      "Operations manager",
      "Engineer",
      "Accounts assistant",
      "New hire",
    ]);
    expect(new Set(PRESET_IDS).size).toBe(8);
  });

  it("pins the table's apps, in its order", () => {
    const pins = Object.fromEntries(PRESETS.map((p) => [p.id, labels(p.pins)]));
    expect(pins).toEqual({
      founder: ["Projects", "Approvals", "My Email", "Chat"],
      "sales-manager": ["My Email", "My WhatsApp", "Projects", "CRM"],
      "marketing-lead": ["Projects", "My Email", "Chat"],
      // Invoices is in the table and not here: no Invoices pane exists yet.
      "finance-manager": ["Approvals", "My Email", "Projects"],
      "operations-manager": ["Projects", "People", "Calendar"],
      engineer: ["My Tasks", "Projects", "Calendar", "Chat"],
      "accounts-assistant": ["My Tasks", "My Email"],
      "new-hire": ["My Tasks", "People", "Chat"],
    });
  });

  it("orders the My Day cards and the jobs as the table does", () => {
    const rows = Object.fromEntries(PRESETS.map((p) => [p.id, [p.cards, p.jobs, p.needsFirst ?? null]]));
    expect(rows).toEqual({
      founder: [["needs", "today", "team-pulse"], ["new-project", "invite", "capture"], null],
      "sales-manager": [["needs", "today", "next"], ["compose", "whatsapp-reply", "capture"], "needs_reply"],
      "marketing-lead": [["needs", "next", "today"], ["capture", "new-chat", "compose"], null],
      "finance-manager": [["needs", "today"], ["review-approvals", "compose"], "approval"],
      "operations-manager": [["needs", "today", "next", "out-today"], ["new-project", "capture", "find-person"], null],
      engineer: [["next", "today", "needs"], ["capture", "plan-day", "new-chat"], null],
      "accounts-assistant": [["needs", "next"], ["capture", "compose"], null],
      "new-hire": [["today", "next"], ["edit-profile", "find-person", "new-chat"], null],
    });
  });

  it("names only real panes, real jobs and known cards", () => {
    const panes = new Set(PANES.map((p) => p.href));
    const jobs = new Set(JOBS.map((j) => j.id));
    for (const p of PRESETS) {
      for (const h of p.pins) expect(panes.has(h), `${p.id} pins ${h}`).toBe(true);
      for (const j of p.jobs) expect(jobs.has(j), `${p.id} names job ${j}`).toBe(true);
      for (const c of p.cards) expect(CARD_KEYS).toContain(c);
    }
  });

  it("marks the Accounts assistant for Desk mode, which waits on NS-8", () => {
    expect(PRESETS.filter((p) => p.desk).map((p) => p.id)).toEqual(["accounts-assistant"]);
  });
});

describe("the first sign-in question picks a preset (§8.4)", () => {
  it("asks the owner's question, with the six answers", () => {
    expect(QUESTION).toBe("What will you do most here?");
    expect(ANSWERS.map((a) => [a.label, a.preset])).toEqual([
      ["Run the company", "founder"],
      ["Sell and look after customers", "sales-manager"],
      ["Plan campaigns and content", "marketing-lead"],
      ["Handle money and approvals", "finance-manager"],
      ["Run projects and operations", "operations-manager"],
      ["Build and ship the work", "engineer"],
    ]);
  });

  it("maps each answer to its preset, and nothing else to one", () => {
    for (const a of ANSWERS) expect(presetForAnswer(a.label)).toBe(a.preset);
    expect(presetForAnswer("Something else")).toBeNull();
  });
});

describe("without an answer, the role picks (rule 2)", () => {
  it("names only presets that exist", () => {
    expect(presetForRole(["owner"])).toBe("founder");
    expect(presetForRole(["admin"])).toBe("founder");
    expect(presetForRole(["manager"])).toBe("operations-manager");
    expect(presetForRole(["member"])).toBe("new-hire");
    expect(presetForRole(["guest"])).toBe("new-hire");
  });

  it("lets the highest role win, and reads a custom role as a member", () => {
    expect(presetForRole(["member", "admin"])).toBe("founder");
    expect(presetForRole(["guest", "manager"])).toBe("operations-manager");
    expect(roleDefault(["sales-team"])).toEqual({ preset: "new-hire", pins: true });
    expect(roleDefault([])).toEqual({ preset: "new-hire", pins: true });
  });

  it("gives a guest New hire with no pins", () => {
    expect(roleDefault(["guest"])).toEqual({ preset: "new-hire", pins: false });
    expect(shellLayout(null, ["guest"]).pins).toEqual([]);
    expect(shellLayout({ ...EMPTY_SHELL, answered: "skipped" }, ["guest"]).pins).toEqual([]);
  });

  it("gives a guest the pins of a preset they choose", () => {
    const chose = shellLayout({ ...EMPTY_SHELL, preset: "engineer", answered: "answered" }, ["guest"]);
    expect(chose.pins).toEqual(presetById("engineer")!.pins);
  });

  it("uses the role's preset when the read failed or never came", () => {
    expect(shellLayout(undefined, ["owner"]).preset.id).toBe("founder");
    expect(shellLayout(undefined, ["member"]).pins).toEqual(presetById("new-hire")!.pins);
  });
});

describe("a stored layout wins, field by field", () => {
  it("keeps the member's own pins over the preset's", () => {
    const l = shellLayout({ ...EMPTY_SHELL, preset: "founder", answered: "answered", pins: ["/calendar"] }, ["member"]);
    expect(l.preset.id).toBe("founder");
    expect(l.pins).toEqual(["/calendar"]);
    expect(l.jobs).toEqual(presetById("founder")!.jobs);
  });

  it("keeps an empty pin list as none, not as the preset's", () => {
    expect(shellLayout({ ...EMPTY_SHELL, pins: [] }, ["owner"]).pins).toEqual([]);
  });

  it("carries the answer through, so a skip is never asked again", () => {
    expect(shellLayout({ ...EMPTY_SHELL, answered: "skipped" }, ["member"]).answered).toBe("skipped");
    expect(shellLayout(EMPTY_SHELL, ["member"]).answered).toBeNull();
  });
});

describe("pins and card order", () => {
  it("toggles a pin on at the end, and off", () => {
    expect(togglePin(["/tasks"], "/email")).toEqual(["/tasks", "/email"]);
    expect(togglePin(["/tasks", "/email"], "/tasks")).toEqual(["/email"]);
  });

  it("puts the named cards first and keeps every other card", () => {
    expect(orderCards(["needs", "today", "next"], ["next", "today", "needs"])).toEqual(["next", "today", "needs"]);
    expect(orderCards(["needs", "today", "next"], ["today", "next"])).toEqual(["today", "next", "needs"]);
    // A card that is not built (Team pulse, NS-5) draws nothing.
    expect(orderCards(["needs", "today"], ["needs", "today", "team-pulse"])).toEqual(["needs", "today"]);
  });
});
