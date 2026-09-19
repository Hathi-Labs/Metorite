/**
 * WS-27s — the seam between a `TaskRow` and the shared card.
 *
 * The translation is small, which is exactly why it is worth pinning: the
 * failure mode is not a crash, it is a card that quietly draws nothing because
 * a snake_case field was read by its camelCase name and came back `undefined`.
 * A board full of tasks with no badges looks like a board full of simple tasks.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { accentForHue } from "@/lib/statusAccent";
import { TASK_SOURCES, chipKind, taskMeta } from "@/lib/taskCard";

import type { TaskRow } from "./api";
import {
  CHIP_FIELD,
  cardChips,
  tagColours,
  taskDeepLink,
  taskFacts,
  taskRef,
  typeFacts,
  visibleChips,
} from "./card";
import { DEFAULT_SHOWN, FIELD_KEYS } from "./shownFields";
import { chipClass } from "./tags";

const NOW = Date.parse("2026-08-07T12:00:00Z");
const hours = (n: number) => new Date(NOW + n * 3_600_000).toISOString();

const row = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: "Ship it",
  ...over,
});

describe("taskFacts", () => {
  it("reads every field off the snake_case row", () => {
    // ⚠️ The whole point of this test. A camelCase typo here is `undefined`,
    // and `undefined` draws no chip — a silent blank, not an error.
    expect(
      taskFacts(
        row({
          due_at: hours(-2),
          completed_at: hours(-1),
          subtasks: { done: 1, total: 3 },
          blocked_by_count: 2,
          tags: ["ops", "urgent"],
          estimate_mins: 45,
        }),
      ),
    ).toEqual({
      type: null,
      source: null,
      dueAt: hours(-2),
      completedAt: hours(-1),
      subtasks: { done: 1, total: 3 },
      blockedByCount: 2,
      tags: [
        { name: "ops", color: undefined },
        { name: "urgent", color: undefined },
      ],
      estimateMins: 45,
    });
  });

  it("defaults the counts a card must not guess at", () => {
    expect(taskFacts(row())).toEqual({
      type: null,
      source: null,
      dueAt: undefined,
      completedAt: undefined,
      subtasks: null,
      blockedByCount: 0,
      tags: [],
      estimateMins: undefined,
    });
  });

  // ── The task TYPE (WS-27bh) ─────────────────────────────────────

  it("resolves type_id through the registry", () => {
    const types = typeFacts([
      { id: "t1", name: "Epic", icon: "Mountain", color: "#7c3aed" },
    ]);
    expect(taskFacts(row({ type_id: "t1" }), undefined, types).type).toEqual({
      name: "Epic",
      icon: "Mountain",
      color: "#7c3aed",
    });
  });

  it("⚠️ yields NO type when the id is not in the registry", () => {
    // A type deleted after the task was written. The card must not fall back
    // to drawing the uuid, which is what a naive `?? task.type_id` does — and
    // a uuid on a card looks like a bug in the data, not in the lookup.
    const types = typeFacts([{ id: "t1", name: "Epic" }]);
    expect(taskFacts(row({ type_id: "gone" }), undefined, types).type).toBeNull();
    expect(taskFacts(row({ type_id: "t1" }), undefined, undefined).type).toBeNull();
  });

  it("yields no type when the row carries none", () => {
    const types = typeFacts([{ id: "t1", name: "Epic" }]);
    expect(taskFacts(row(), undefined, types).type).toBeNull();
  });

  it("keys the registry by ID, not by name", () => {
    // A tag rides the row by NAME, so `tagColours` keys by a lowercased name.
    // A type rides it as `type_id`. Keying this by name would need a second
    // lookup that does not exist, and every chip would silently vanish.
    const types = typeFacts([{ id: "t1", name: "Epic" }]);
    expect(types.get("t1")?.name).toBe("Epic");
    expect(types.get("Epic")).toBeUndefined();
  });

  it("claims no attachments, because the list endpoint does not count them", () => {
    // Honest absence. A plausible zero would have the card assert something
    // the endpoint never told it — attachments are counted on the single-task
    // read alone (WS-27i).
    const facts = taskFacts(row()) as Record<string, unknown>;
    expect(facts.attachmentCount).toBeUndefined();
  });

  it("DOES read the estimate, which the list has always returned (S6)", () => {
    // ⚠️ This file used to assert the opposite, on the belief that there was
    // "no estimate column at all". `pm_tasks.estimate_mins` is on `TaskModel`
    // and the table's Estimate column reads it — so the card was the one
    // surface silently dropping the fact.
    expect(taskFacts(row({ estimate_mins: 90 })).estimateMins).toBe(90);
  });

  it("colours a tag from the registry, folding case as the registry does", () => {
    // A task's `tags` array carries whatever spelling was saved; the registry
    // owns the canonical one. A chip that lost its colour because somebody
    // typed `Bug` is the drift this folds away.
    const colours = tagColours([
      { name: "bug", color: "red" },
      { name: "ops", color: "blue" },
    ]);
    expect(taskFacts(row({ tags: ["BUG", "nope"] }), colours).tags).toEqual([
      { name: "BUG", color: "red" },
      { name: "nope", color: undefined },
    ]);
  });
});

describe("cardChips", () => {
  it("turns a loaded row into the strip the board draws", () => {
    expect(
      cardChips(
        row({
          due_at: hours(-2),
          subtasks: { done: 1, total: 3 },
          blocked_by_count: 1,
          importance: 3,
          tags: ["ops"],
        }),
        NOW,
      ).map((c) => [c.key, c.label]),
    ).toEqual([
      ["blocked", "1"],
      // S6 — priority sits between "do not start this" and "when": that is
      // the order a reader scans a column in.
      ["importance", "Urgent"],
      ["due", "2h ago"],
      ["subtasks", "1/3"],
      ["tags:ops", "ops"],
    ]);
  });

  it("puts priority first when nothing is blocking", () => {
    // The splice is relative to the `blocked` chip, which is usually absent —
    // `findIndex` returning -1 must land it at 0, not at the end.
    expect(
      cardChips(row({ importance: 2, due_at: hours(4) }), NOW).map((c) => c.key),
    ).toEqual(["importance", "due"]);
  });

  it("leaves a plain task with a bare card", () => {
    expect(cardChips(row(), NOW)).toEqual([]);
  });

  it("stops calling a finished task overdue", () => {
    const chips = cardChips(
      row({ due_at: hours(-48), completed_at: hours(-1) }),
      NOW,
    );
    expect(chips.map((c) => c.tone)).toEqual(["muted"]);
  });
});

describe("the priority chip (S6)", () => {
  const chip = (importance: number | null | undefined) =>
    cardChips(row({ importance }), NOW).find((c) => c.key === "importance");

  it("says nothing at all when nobody set a priority", () => {
    // ⚠️ `null` is "nobody said" and MUST NOT read as Low. The select's empty
    // row PATCHes null precisely so that distinction survives.
    expect(chip(null)).toBeUndefined();
    expect(chip(undefined)).toBeUndefined();
  });

  it("draws Low, which is a decision somebody took", () => {
    // The falsy trap: `if (!importance)` would silence exactly this row.
    expect(chip(0)?.label).toBe("Low");
  });

  it("speaks the table cell's vocabulary, word for word", () => {
    // One label source (`table.importanceLabel`), two surfaces. A second map
    // here is how the card starts calling a 2 "Medium".
    expect([0, 1, 2, 3].map((v) => chip(v)?.label)).toEqual([
      "Low",
      "Normal",
      "High",
      "Urgent",
    ]);
  });

  it("escalates the tone only at the top of the scale", () => {
    // A scale where three of four levels are loud is a scale nobody reads.
    expect([0, 1, 2, 3].map((v) => chip(v)?.tone)).toEqual([
      "muted",
      "muted",
      "warning",
      "danger",
    ]);
  });

  it("gives every level its own glyph, so the tone is never the only signal", () => {
    const icons = [0, 1, 2, 3].map((v) => chip(v)!.icon!);
    expect(new Set(icons).size).toBe(4);
    // Reused glyphs would collide with the chips beside it: `AlertTriangle`
    // is overdue's, `Ban` is blocked's.
    expect(icons).not.toContain("AlertTriangle");
    expect(icons).not.toContain("Ban");
  });

  it("ignores a value outside the vocabulary rather than inventing a chip", () => {
    // An imported workspace can carry a 7. A chip labelled "7" says nothing.
    expect(chip(7)).toBeUndefined();
  });
});

describe("the tag chips (S6)", () => {
  it("carries the registry's colour through to the card", () => {
    // ⚠️ The cross-surface fence: the chip a card draws and the chip the tag
    // picker draws for the SAME tag must be the same class string, or one tag
    // is two colours in one project.
    const colours = tagColours([{ name: "bug", color: "red" }]);
    const chip = cardChips(row({ tags: ["bug"] }), NOW, colours)[0];
    expect(accentForHue(chip.hue!).chip).toBe(chipClass("red"));
  });

  it("falls back to the same gray the picker uses for an unknown tag", () => {
    const chip = cardChips(row({ tags: ["mystery"] }), NOW)[0];
    expect(accentForHue(chip.hue!).chip).toBe(chipClass(undefined));
  });
});

describe("visibleChips — the shown-fields gate (WS-27x)", () => {
  // A row that earns every chip the list endpoint can produce.
  const loaded = row({
    due_at: hours(-2),
    subtasks: { done: 1, total: 3 },
    blocked_by_count: 1,
    importance: 2,
    tags: ["ops"],
  });

  it("draws every earned chip under the default shown set", () => {
    // The gate is a VISIBILITY layer: with the defaults it must change
    // nothing about what a card drew before shown-fields existed.
    expect(visibleChips(loaded, DEFAULT_SHOWN, NOW)).toEqual(
      cardChips(loaded, NOW),
    );
  });

  it("silences exactly the chip whose field was hidden", () => {
    const shown = DEFAULT_SHOWN.filter((key) => key !== "due_at");
    expect(visibleChips(loaded, shown, NOW).map((c) => c.key)).toEqual([
      "blocked",
      "importance",
      "subtasks",
      "tags:ops",
    ]);
  });

  it("hides EVERY tag chip when the view hides tags", () => {
    // ⚠️ The namespaced-key trap: `tags:ops` is not in any shown-fields list,
    // so a whole-key lookup would hide the tags no matter what the view said
    // — and hiding tags would look like it worked.
    const many = row({ tags: ["a", "b", "c", "d"] });
    expect(visibleChips(many, ["tags"], NOW)).toHaveLength(4);
    expect(visibleChips(many, DEFAULT_SHOWN.filter((k) => k !== "tags"), NOW))
      .toHaveLength(0);
  });

  it("gates priority on the field the table's Priority column already uses", () => {
    // One key, one decision: turning Priority off in the field picker must
    // silence the cell AND the chip, or the picker is lying about its scope.
    expect(visibleChips(loaded, ["importance"], NOW).map((c) => c.key)).toEqual([
      "importance",
    ]);
  });

  it("produces no chips at all when every field is hidden", () => {
    // "This view surfaces nothing" and "this task earned nothing" must read
    // identically — no placeholder, no dimmed chip.
    expect(visibleChips(loaded, [], NOW)).toEqual([]);
  });

  it("keeps the fact layer intact — gating filters, never re-derives", () => {
    const [chip] = visibleChips(loaded, ["blocked"], NOW);
    expect(chip).toEqual(cardChips(loaded, NOW)[0]);
  });

  it("maps every chip kind taskMeta can emit onto a field key", () => {
    // An unmapped chip kind would bypass the gate silently. Derived from
    // `taskMeta` itself over fully-loaded facts, so a chip added there
    // without a mapping here fails loudly.
    const everyChip = taskMeta(
      {
        dueAt: hours(-2),
        subtasks: { done: 1, total: 2 },
        blockedByCount: 1,
        tags: [{ name: "ops" }],
        attachmentCount: 1,
        estimateMins: 30,
      },
      NOW,
    ).map((c) => c.key);
    expect(everyChip.length).toBeGreaterThanOrEqual(6);
    for (const key of everyChip) {
      expect(
        CHIP_FIELD[chipKind(key)],
        `chip '${key}' has no shown-field mapping`,
      ).toBeDefined();
    }
  });

  it("maps every chip kind THIS app can emit, including the spliced ones", () => {
    // ⚠️ Wider than the row above, and that is the point: `cardChips` splices
    // in a chip `taskMeta` never emits (priority), so a fence derived from
    // `taskMeta` alone would never see it. A row full of every fact the list
    // endpoint returns.
    const everything = cardChips(
      row({
        due_at: hours(-2),
        subtasks: { done: 1, total: 2 },
        blocked_by_count: 1,
        importance: 3,
        tags: ["ops", "api", "web", "db"],
        estimate_mins: 30,
      }),
      NOW,
    ).map((c) => chipKind(c.key));
    expect(new Set(everything)).toEqual(
      new Set(["blocked", "importance", "due", "subtasks", "tags", "estimate"]),
    );
    for (const kind of everything) {
      const field = CHIP_FIELD[kind];
      expect(field, `chip kind '${kind}' has no shown-field mapping`).toBeDefined();
      // …and the field it maps to has to be a field a view can actually turn
      // off. A mapping onto a key the picker does not offer is a chip nobody
      // can hide, which is the same defect as a chip nobody can gate.
      expect(FIELD_KEYS as readonly string[]).toContain(field);
    }
  });
});

describe("taskRef", () => {
  // WS-27w item 6 — one formatter, three surfaces (board, list, panel).
  it("formats the per-root number as the id people quote", () => {
    expect(taskRef(row({ task_number: 42 }))).toBe("#42");
  });

  it("is null — never '#undefined' — when the number is absent", () => {
    // Each surface keeps its own honest fallback; the formatter must not
    // invent one, or an imported task without a number renders as a lie.
    expect(taskRef(row())).toBeNull();
    expect(taskRef(row({ task_number: null }))).toBeNull();
  });
});

describe("taskDeepLink", () => {
  it("builds the ?task= link the board already reads", () => {
    // The same shape NotificationBell emits (WS-28b): a third spelling would
    // be a copied link that opens nothing.
    expect(taskDeepLink(row(), "https://cc.example")).toBe(
      "https://cc.example/projects?task=t1",
    );
  });

  it("is origin-relative when no origin is given, and encodes the id", () => {
    expect(taskDeepLink({ id: "a b" })).toBe("/projects?task=a%20b");
  });
});

describe("the task SOURCE chip (WS-27bh)", () => {
  const sourceChip = (source: string | null) =>
    cardChips(row({ source } as Partial<TaskRow>)).find((c) => c.key === "source");

  it("names the origins that are not manual", () => {
    expect(sourceChip("email")?.label).toBe("Email");
    expect(sourceChip("agent")?.label).toBe("Agent");
    expect(sourceChip("automation")?.label).toBe("Automation");
    expect(sourceChip("import")?.label).toBe("Imported");
  });

  it("⚠️ draws NOTHING for a manual task", () => {
    // `manual` is the overwhelming majority of rows. A badge on every card
    // is a badge nobody reads, and it would crowd out the chips that vary.
    expect(sourceChip("manual")).toBeUndefined();
    expect(sourceChip(null)).toBeUndefined();
  });

  it("⚠️ draws nothing for a value the vocabulary does not know", () => {
    // The CHECK constraint can gain a value before this table does. An
    // unknown source must be silent, never a chip reading the raw column.
    expect(sourceChip("telepathy")).toBeUndefined();
  });

  it("covers exactly the non-manual half of the migration-146 CHECK", () => {
    // The fence against the vocabularies drifting apart. `pm_tasks.source`
    // is CHECK (source IN ('manual','import','email','agent','automation')),
    // and every value but `manual` earns a chip.
    expect(Object.keys(TASK_SOURCES).sort()).toEqual([
      "agent",
      "automation",
      "email",
      "import",
    ]);
  });
});

describe("🔴 every chip surface must pass the type registry", () => {
  // The defect this pins, found by review 2026-09-19.
  //
  // `CalendarView` built its `typeHues` map and then called `visibleChips`
  // with FOUR arguments, so the type chip drew on the board and the list and
  // never on the calendar. Nothing caught it: the fifth parameter is
  // optional so `tsc` is happy, and `noUnusedLocals` is off so the dead memo
  // was silent too.
  //
  // A source scan, because the defect is an omitted ARGUMENT — there is no
  // behavioural assertion that distinguishes "this surface has no types" from
  // "this surface forgot to pass them".
  const SURFACES = [
    "TaskBoard.tsx",
    "TaskList.tsx",
    "CalendarView.tsx",
    "TimelineView.tsx",
  ];

  it("passes the registry at every visibleChips call site", () => {
    for (const name of SURFACES) {
      const source = readFileSync(
        new URL(`../components/${name}`, import.meta.url),
        "utf-8",
      );
      // Every call, with whitespace and newlines collapsed so a multi-line
      // call reads the same as a one-line one — which is exactly how the
      // calendar's slipped through a bulk edit.
      const flat = source.replace(/\s+/g, " ");
      const calls = flat.match(/visibleChips\([^)]*\)/g) ?? [];
      for (const call of calls) {
        expect(
          call,
          `${name}: visibleChips must receive the type registry`,
        ).toContain("typeHues");
      }
    }
  });
});
