/**
 * The presets (NS-7, `navigation_shell.md` §8.1 and §8.4). The owner approved
 * the table on 2026-10-10, and these cases hold it.
 *
 * ⚠️ **The subset rule is the one that matters.** A preset never grants. For
 * every preset and every grant set below, what the shell draws from the
 * preset is a subset of `visibleSections(features, isAdmin)`: the starred
 * apps, the sidebar's whole shape, the My Day cards and the jobs of the
 * empty command bar.
 *
 * Mutation, seen to fail first: make `starrableHeld` read `PANES` instead of
 * `sections`, and "draws no star outside visibleSections" fails for every
 * preset with no grants.
 *
 * The star model (owner, 2026-10-11) has its own block below: null stars
 * show every app, the `known` rule, an answer, and a skip.
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
  STARRABLE,
  answerStars,
  isStarred,
  orderCards,
  presetById,
  presetExtras,
  presetForAnswer,
  presetForRole,
  roleDefault,
  shellLayout,
  starredHrefs,
  starsToSave,
  toggleStar,
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

        const stars = { pins: answerStars(preset, sections), known: [...held] };
        const raw = { pins: [...preset.pins, "/tasks", "/approvals"], known: [] };

        it(`${label}: draws no star outside visibleSections`, () => {
          for (const h of starredHrefs(stars, sections) ?? []) expect(held.has(h)).toBe(true);
          // A stored list that names apps the member lacks, and a `known`
          // that makes every app new, still draws only what they hold.
          for (const h of starredHrefs(raw, sections) ?? []) expect(held.has(h)).toBe(true);
        });

        it(`${label}: the sidebar with its stars is still a subset`, () => {
          for (const s of [stars, raw]) {
            const drawn = shellSidebar(sections, s).flatMap((g) => g.items.map((p) => p.href));
            for (const h of drawn) expect(held.has(h)).toBe(true);
            // And it draws each held app once: nothing moves, nothing copies.
            expect(new Set(drawn).size).toBe(drawn.length);
          }
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

  it("draws the CRM pin only for a member who holds the CRM (live since CRM-L2)", () => {
    const sales = presetById("sales-manager")!;
    expect(sales.pins).toContain("/crm");
    const stars = { pins: sales.pins, known: [] };
    const without = ALL.filter((f) => f !== "crm");
    expect([...starredHrefs(stars, visibleSections(without, false, false))!]).not.toContain("/crm");
    expect([...starredHrefs(stars, visibleSections(ALL, false, false))!]).toContain("/crm");
  });

  it("never pins a page about the member, even when one is named", () => {
    const sections = visibleSections(ALL, true);
    const stars = { pins: ["/people/me", "/settings/appearance", "/tasks"], known: PANES.map((p) => p.href) };
    expect([...starredHrefs(stars, sections)!]).toEqual(["/tasks"]);
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
    expect(roleDefault(["sales-team"])).toEqual({ preset: "new-hire" });
    expect(roleDefault([])).toEqual({ preset: "new-hire" });
  });

  it("gives a guest New hire, and every app they hold shows", () => {
    expect(roleDefault(["guest"])).toEqual({ preset: "new-hire" });
    expect(shellLayout(null, ["guest"]).pins).toBeNull();
    expect(shellLayout({ ...EMPTY_SHELL, answered: "skipped" }, ["guest"]).pins).toBeNull();
  });

  it("uses the role's preset when the read failed or never came, and hides no app", () => {
    expect(shellLayout(undefined, ["owner"]).preset.id).toBe("founder");
    expect(shellLayout(undefined, ["owner"]).cards).toEqual(presetById("founder")!.cards);
    expect(shellLayout(undefined, ["member"]).pins).toBeNull();
  });
});

describe("a stored layout wins, field by field", () => {
  it("keeps the member's own pins over the preset's", () => {
    const l = shellLayout(
      { ...EMPTY_SHELL, preset: "founder", answered: "answered", pins: ["/calendar"], known: ["/calendar"] },
      ["member"],
    );
    expect(l.preset.id).toBe("founder");
    expect(l.pins).toEqual(["/calendar"]);
    expect(l.known).toEqual(["/calendar"]);
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

describe("a star only where the save can land (round 2)", () => {
  // The gateway accepts the panes nav.ts declares by a literal href
  // (`SHELL_PANES`, held to nav.ts by `test_auth_me_shell.py`). The six
  // Center panes are derived, D49 keeps them unlinked, and the PUT refuses
  // them. So with the preview apps on, a Center tile gets no star. A setting
  // lives in the account menu, so it is never starred either.
  it("holds no Center pane and no setting, and every other pane", () => {
    expect([...STARRABLE].some((h) => h.startsWith("/centers/"))).toBe(false);
    const literal = PANES.filter((p) => !p.href.startsWith("/centers/") && !p.setting).map((p) => p.href);
    expect([...STARRABLE].sort()).toEqual([...literal].sort());
    expect(PANES.some((p) => p.href.startsWith("/centers/"))).toBe(true);
    expect(STARRABLE.has("/people/me")).toBe(false);
    expect(STARRABLE.has("/settings/appearance")).toBe(false);
    // Home is not a nav.ts pane, so it can never be starred away.
    expect(STARRABLE.has("/")).toBe(false);
  });

  it("holds every app of every preset", () => {
    for (const p of PRESETS) for (const h of p.pins) expect(STARRABLE.has(h), `${p.id} stars ${h}`).toBe(true);
  });
});

// ── The star model (owner, 2026-10-11) ─────────────────────────────────────

describe("the star model: only starred apps show, under their own category", () => {
  const sections = visibleSections(ALL, true);
  const heldApps = () => [...STARRABLE].filter((h) => hrefs(sections).has(h));

  it("with no stored stars (never asked, or skipped), every held app shows", () => {
    expect(starredHrefs(null, sections)).toBeNull();
    expect(starredHrefs({ pins: null, known: ["/tasks"] }, sections)).toBeNull();
    for (const h of heldApps()) expect(isStarred({ pins: null, known: null }, sections, h)).toBe(true);
    // The sidebar is the one it was before stars.
    expect(shellSidebar(sections, { pins: null, known: null })).toEqual(shellSidebar(sections));
  });

  it("a skip stores no stars, so every app shows", () => {
    const skipped = shellLayout({ ...EMPTY_SHELL, answered: "skipped" }, ["member"]);
    expect(skipped.pins).toBeNull();
    expect(starredHrefs(skipped, sections)).toBeNull();
  });

  it("an answer stars every held Personal Center app, and the preset's apps", () => {
    const personal = PANES.filter((p) => p.team === "personal" && !p.setting && hrefs(sections).has(p.href)).map(
      (p) => p.href,
    );
    expect(personal).toEqual(["/tasks", "/calendar", "/email", "/whatsapp", "/artifacts"]);
    expect(answerStars(presetById("founder")!, sections)).toEqual([...personal, "/projects", "/chat", "/approvals"]);
    // Approvals is starred for every answer of a member who holds it.
    expect(answerStars(presetById("accounts-assistant")!, sections)).toEqual([...personal, "/approvals"]);
    // A member who lacks an app gets no star for it.
    const few = visibleSections(["tasks", "projects"], false);
    expect(answerStars(presetById("founder")!, few)).toEqual(["/tasks", "/calendar", "/projects"]);
    // Each answer names only its extras, which the member holds.
    expect(presetExtras(presetById("founder")!, sections).map((p) => p.label)).toEqual([
      "Projects",
      "Chat",
      "Approvals",
    ]);
  });

  // Orchestrator decision, 2026-10-11: the sidebar row is an approver's main
  // door to the queue, because the bell and My Day are not reliably on.
  it("an approver who answers Engineer gets Approvals starred, and a member without it does not", () => {
    const engineer = presetById("engineer")!;
    expect(engineer.pins).not.toContain("/approvals");
    expect(answerStars(engineer, visibleSections(["tasks", "approvals"], false))).toContain("/approvals");
    expect(answerStars(engineer, visibleSections(["tasks"], false))).not.toContain("/approvals");
  });

  it("a new app shows starred until a save covers it (the known rule)", () => {
    const stars = { pins: ["/projects"], known: ["/projects", "/people", "/tasks"] };
    const shown = starredHrefs(stars, sections)!;
    // Known and not starred: hidden.
    expect(shown.has("/people")).toBe(false);
    expect(shown.has("/tasks")).toBe(false);
    // Not known: new to the member, so it shows.
    expect(shown.has("/chat")).toBe(true);
    expect(shown.has("/email")).toBe(true);
    // A save writes every held app as known, so it is no longer new.
    const saved = starsToSave(shown, sections, stars);
    expect([...saved.known].sort()).toEqual(heldApps().sort());
    const hidden = toggleStar(saved, sections, "/chat");
    expect(hidden.pins).not.toContain("/chat");
    expect(starredHrefs(hidden, sections)!.has("/chat")).toBe(false);
  });

  // NS-7's pins were a "My apps" list on top of the whole sidebar, never a
  // filter. So a row with no `known` shows every held app, as on the day
  // before the merge.
  it("an NS-7 row with no known shows every held app, as the day before", () => {
    const old = { pins: ["/tasks", "/projects"], known: null };
    expect(starredHrefs(old, sections)).toBeNull();
    expect(shellSidebar(sections, old)).toEqual(shellSidebar(sections));
  });

  it("an NS-7 row that pinned and then unpinned (pins: [], no known) still shows every app", () => {
    const emptied = { pins: [], known: null };
    expect(starredHrefs(emptied, sections)).toBeNull();
    expect(shellSidebar(sections, emptied)).toEqual(shellSidebar(sections));
    for (const h of heldApps()) expect(isStarred(emptied, sections, h)).toBe(true);
  });

  it("the first star-model save of an NS-7 row writes known, and the stars filter from then on", () => {
    const old = { pins: ["/tasks"], known: null };
    const saved = toggleStar(old, sections, "/chat");
    expect([...saved.known].sort()).toEqual(heldApps().sort());
    expect(saved.pins).not.toContain("/chat");
    expect(saved.pins).toContain("/projects");
    expect(starredHrefs(saved, sections)!.has("/chat")).toBe(false);
  });

  it("toggles a star on and off, and saves every held app as known", () => {
    const all = toggleStar({ pins: null, known: null }, sections, "/chat");
    expect(all.pins).not.toContain("/chat");
    expect(all.pins).toContain("/projects");
    const back = toggleStar(all, sections, "/chat");
    expect(back.pins).toContain("/chat");
    expect([...back.known].sort()).toEqual(heldApps().sort());
  });

  it("keeps a star for an app the member lost, so it shows again when the grant returns", () => {
    const few = visibleSections(["tasks"], false);
    const saved = starsToSave(["/tasks"], few, { pins: ["/projects"], known: ["/projects"] });
    expect(saved.pins).toEqual(["/tasks", "/projects"]);
    // Integrations is held by every member since D-EA-9 (2026-10-14).
    expect(saved.known).toEqual(["/tasks", "/calendar", "/integrations"]);
  });
});

describe("card order", () => {
  it("puts the named cards first and keeps every other card", () => {
    expect(orderCards(["needs", "today", "next"], ["next", "today", "needs"])).toEqual(["next", "today", "needs"]);
    expect(orderCards(["needs", "today", "next"], ["today", "next"])).toEqual(["today", "next", "needs"]);
    // A card that is not built (Team pulse, NS-5) draws nothing.
    expect(orderCards(["needs", "today"], ["needs", "today", "team-pulse"])).toEqual(["needs", "today"]);
  });
});
