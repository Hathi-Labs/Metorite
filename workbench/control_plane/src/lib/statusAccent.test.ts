/**
 * The shared status colour vocabulary (WS-27ad).
 *
 * The property worth pinning is the PRECEDENCE, not the class strings: this
 * module exists because two apps knew different things about a lane and each
 * needs the most authoritative fact it has to win. So most of these assert on
 * `resolveHue`, and only the shape test touches classes.
 *
 * The regression it also guards: /tasks was rendering from `stageColors.ts`
 * before this merge, and its behaviour had to survive byte-for-byte. Every
 * keyword and the positional fallback are asserted here for that reason.
 */

import { describe, expect, it } from "vitest";

import {
  ACCENT_HUES,
  type AccentHue,
  PROJECT_STATE_ORDER,
  PROJECT_STATES,
  accentForHue,
  projectState,
  projectStateAccent,
  resolveHue,
  statusAccent,
} from "./statusAccent";

describe("precedence", () => {
  it("a stored colour beats everything else", () => {
    // The owner picked it. A category or a keyword overruling a human choice is
    // the UI editing somebody's decision behind their back.
    expect(
      resolveHue({
        color: "red",
        category: "done",
        name: "Done",
        index: 3,
        total: 4,
        lastIsDone: true,
      }),
    ).toBe("red");
  });

  it("a category beats a name that disagrees with it", () => {
    // A lane named "Shipped" whose category is `in_progress` is in progress —
    // the name would have said green, and the category is the better fact.
    expect(resolveHue({ category: "in_progress", name: "Shipped" })).toBe("blue");
  });

  it("a name keyword beats position", () => {
    expect(resolveHue({ name: "Blocked on legal", index: 1, total: 4 })).toBe("amber");
  });

  it("falls through to position when nothing is known", () => {
    expect(resolveHue({ index: 1, total: 4 })).toBe("blue");
  });
});

describe("stored colour names", () => {
  it.each([
    ["gray", "gray"],
    ["grey", "gray"],
    ["red", "red"],
    ["amber", "amber"],
    ["green", "green"],
    ["blue", "blue"],
    ["violet", "violet"],
    ["purple", "violet"],
    ["orange", "amber"],
    ["yellow", "amber"],
  ])("resolves %s → %s", (stored, hue) => {
    expect(resolveHue({ color: stored })).toBe(hue);
  });

  it("is case- and whitespace-insensitive", () => {
    expect(resolveHue({ color: "  GREEN " })).toBe("green");
  });

  it("falls THROUGH an unknown colour rather than throwing or blanking", () => {
    // A board that renders no columns because somebody typed "chartreuse" is a
    // worse failure than a column that comes out the category's colour.
    expect(resolveHue({ color: "chartreuse", category: "done" })).toBe("green");
    expect(resolveHue({ color: "chartreuse" })).toBe("gray");
  });
});

describe("the six status categories all resolve", () => {
  // Mirrors `STATUS_CATEGORIES` in routes/projects/core.py. A category the
  // gateway can store and this cannot colour is a lane that silently goes grey.
  it.each([
    ["backlog", "gray"],
    ["todo", "gray"],
    ["in_progress", "blue"],
    ["done", "green"],
    ["cancelled", "red"],
    ["triage", "violet"],
  ])("%s → %s", (category, hue) => {
    expect(resolveHue({ category })).toBe(hue);
  });

  it("falls through an unknown category", () => {
    expect(resolveHue({ category: "parked", name: "Done" })).toBe("green");
  });

  // THE fence for this module's whole reason to exist.
  //
  // /projects learns what a lane means from its `category`; /tasks can only
  // read the words in its name. If those two routes disagreed, the same lane
  // would draw one colour in one app and another colour next door — which is
  // the divergence this module was built to end, reintroduced one layer down.
  //
  // It happened: the category map originally mirrored the seeded colours
  // (`todo` blue, `in_progress` amber) while the keyword rules said gray and
  // blue, so a default board still mismatched in two of its four lanes.
  //
  // Each pair below is a category and a lane name a person would plausibly type
  // for it. `backlog`/`todo` share a hue on purpose (see CATEGORY_HUES).
  it.each([
    ["backlog", "Backlog"],
    ["todo", "To do"],
    ["in_progress", "In progress"],
    ["done", "Done"],
  ])("category %s and the name %j resolve to the same hue", (category, name) => {
    expect(resolveHue({ category })).toBe(resolveHue({ name }));
  });
});

describe("name keywords — /tasks' stages, ported unchanged", () => {
  it.each([
    ["Done", "green"],
    ["Complete", "green"],
    ["Closed", "green"],
    ["Finished", "green"],
    ["Shipped", "green"],
    ["Waiting", "amber"],
    ["Blocked", "amber"],
    ["On hold", "amber"],
    ["Paused", "amber"],
    ["Stuck", "amber"],
    ["In progress", "blue"],
    ["Doing", "blue"],
    ["Active", "blue"],
    ["Working", "blue"],
    ["In review", "blue"],
    ["In-process", "blue"],
    ["To do", "gray"],
    ["Todo", "gray"],
    ["Backlog", "gray"],
    ["New", "gray"],
    ["Open", "gray"],
    ["Inbox", "gray"],
  ])("%s → %s", (name, hue) => {
    expect(resolveHue({ name })).toBe(hue);
  });

  it("has no `cancel` rule — adding one would repaint a /tasks stage", () => {
    // Recorded as a test rather than a comment: the omission is deliberate and
    // the next author's instinct will be to "fix" it.
    expect(resolveHue({ name: "Cancelled", index: 1, total: 4 })).toBe("blue");
  });
});

describe("positional fallback", () => {
  it("gives neighbouring lanes different hues", () => {
    const hues = [0, 1, 2, 3].map((index) =>
      resolveHue({ name: "Lane", index, total: 8 }),
    );
    expect(hues).toEqual(["gray", "blue", "violet", "amber"]);
  });

  it("wraps past the end of the palette instead of failing", () => {
    expect(resolveHue({ name: "Lane", index: 4, total: 8 })).toBe("gray");
  });

  it("paints the last lane green only when the caller asked", () => {
    // /tasks: the final stage IS the done stage (dropping there completes).
    expect(resolveHue({ name: "Later", index: 3, total: 4, lastIsDone: true })).toBe("green");
    // Projects: a lane's meaning comes from its category, never its position.
    expect(resolveHue({ name: "Later", index: 3, total: 4 })).toBe("amber");
  });

  it("survives being asked about nothing at all", () => {
    expect(resolveHue({})).toBe("gray");
  });
});

describe("the accent shape", () => {
  it("gives every hue all five slots, from tokens only", () => {
    for (const hue of ACCENT_HUES) {
      const accent = accentForHue(hue as AccentHue);
      for (const slot of ["dot", "soft", "text", "bar", "chip"] as const) {
        expect(accent[slot], `${hue}.${slot}`).toBeTruthy();
        // DESIGN_SYSTEM.md rule 1, asserted where the palette actually lives.
        expect(accent[slot], `${hue}.${slot} must not name a raw colour`).not.toMatch(
          /#[0-9a-f]{3,6}\b|rgba?\(|hsla?\(/i,
        );
      }
    }
  });

  /**
   * ⚠️ THE FENCE FOR THE ACCENT DEFECT (R7).
   *
   * `--primary` is the accent a member sets at Settings → Appearance. While
   * `blue` and `violet` read it, a board's "In progress" lane was not blue —
   * it was whatever colour the viewer chose, and an accent set to green made
   * an active lane the same colour as Done.
   *
   * Nothing caught it, because every other test here asserts a hue NAME.
   * `project-state.spec.ts` says so in its own header: a colour column existed
   * for months while every lane drew the same grey.
   *
   * This asserts the CLASS, which is the closest a node-environment test can
   * get to the paint. The browser-side half — that the lane's computed colour
   * does not move when `--primary` moves — belongs to the visual-review rig's
   * `underAccents` helper.
   */
  it("never paints a status with the member's accent", () => {
    for (const hue of ACCENT_HUES) {
      const accent = accentForHue(hue as AccentHue);
      for (const slot of ["dot", "soft", "text", "bar"] as const) {
        expect(
          accent[slot],
          `${hue}.${slot} reads --primary, so this status changes colour when a member changes their accent`,
        ).not.toMatch(/primary/);
      }
    }
  });

  it("gives blue and violet different hues, not one hue twice", () => {
    // `violet` was `bg-primary/60` — blue, one step fainter. So two of the
    // five lanes on a positionally-coloured board never differed at all.
    const blue = accentForHue("blue");
    const violet = accentForHue("violet");
    for (const slot of ["dot", "soft", "text", "bar"] as const) {
      expect(violet[slot], `violet.${slot}`).not.toBe(blue[slot]);
    }
    expect(violet.dot.replace(/\/\d+$/, "")).not.toBe(blue.dot.replace(/\/\d+$/, ""));
  });

  it("keeps /tasks' stage classes byte-identical", () => {
    // The five strings `stageColors.ts` returned for a green stage. /tasks
    // renders from these on its board, its list headers and its status pill,
    // so a change here is a change to a shipped surface.
    expect(statusAccent({ name: "Done" })).toMatchObject({
      dot: "bg-success",
      soft: "bg-success/10",
      text: "text-success",
      bar: "border-l-success",
    });
    expect(statusAccent({ name: "Backlog" })).toMatchObject({
      dot: "bg-muted-foreground/60",
      soft: "bg-muted/40",
      text: "text-muted-foreground",
      bar: "border-l-muted-foreground/40",
    });
    // ⚠️ CHANGED ON PURPOSE, 2026-09-03 — the third positional lane.
    // It was `--primary` at 60%, 5% and 80%: the member's accent, three
    // opacities deep. So lane 3 was lane 2 one step fainter, and both moved
    // when the member changed their accent. `violet` is a real token now.
    expect(statusAccent({ name: "Lane", index: 2, total: 8 })).toMatchObject({
      dot: "bg-violet",
      soft: "bg-violet/10",
      text: "text-violet",
      bar: "border-l-violet",
    });
  });

  it("keeps the tag chip classes byte-identical", () => {
    // `chipClass` in app/projects/lib/tags.ts returned exactly these.
    expect(statusAccent({ color: "gray" }).chip).toBe("bg-secondary text-muted-foreground");
    expect(statusAccent({ color: "red" }).chip).toBe("bg-destructive/10 text-destructive");
    expect(statusAccent({ color: "amber" }).chip).toBe("bg-warning/10 text-warning");
    expect(statusAccent({ color: "green" }).chip).toBe("bg-success/10 text-success");
    // ⚠️ CHANGED ON PURPOSE, 2026-09-03. This read `bg-primary/10
    // text-primary`, and `--primary` is the member's accent — so a blue tag
    // was not blue, it was whatever colour the viewer had chosen. The port
    // this test guards was faithful; the thing it was faithful to was wrong.
    expect(statusAccent({ color: "blue" }).chip).toBe("bg-info/10 text-info");
    expect(statusAccent({ color: "violet" }).chip).toBe("bg-accent text-accent-foreground");
  });
});

/* ── Project run state (WS-27bg / D-PM-27) ────────────────────────────────── */

describe("the project run-state vocabulary", () => {
  it("resolves each state to the hue the owner ruled", () => {
    // D-PM-27, verbatim. If this table is edited, the decision was overruled —
    // which is an owner's act, not a refactor.
    expect(PROJECT_STATES.active.hue).toBe("green");
    expect(PROJECT_STATES.on_hold.hue).toBe("amber");
    expect(PROJECT_STATES.stopped.hue).toBe("red");
    expect(PROJECT_STATES.queued.hue).toBe("gray");
    expect(PROJECT_STATES.done.hue).toBe("blue");
  });

  it("is NOT reachable from resolveHue — the keywordHue trap", () => {
    // 🔴 The fence D-PM-27 asks for by name. `keywordHue` maps the literal word
    // "active" to blue and "done" to green, so a "simplification" that routed a
    // run state through the generic resolver would silently produce the
    // OPPOSITE of the decision on two of five states. Asserting the two
    // DISAGREE is what catches that: if the project map ever delegates, these
    // converge and this test goes red.
    expect(resolveHue({ name: "active" })).toBe("blue");
    expect(PROJECT_STATES.active.hue).toBe("green");
    expect(resolveHue({ name: "done" })).toBe("green");
    expect(PROJECT_STATES.done.hue).toBe("blue");
  });

  it("labels the stored values rather than showing them", () => {
    // D-PM-25: `active`/`on_hold` are not renamed in the database (R6 forbids
    // renaming in place), so the display name lives in the vocabulary.
    expect(PROJECT_STATES.active.label).toBe("Ongoing");
    expect(PROJECT_STATES.on_hold.label).toBe("Paused");
  });

  it("gives every state a distinct glyph, so hue is never the only channel", () => {
    const icons = PROJECT_STATE_ORDER.map((s) => PROJECT_STATES[s].icon);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("offers no `archived` state — that is the other axis", () => {
    // D-PM-25. Offering it in a run-state picker is how the two-facts-one-column
    // defect would come back through the UI.
    expect(PROJECT_STATE_ORDER).not.toContain("archived");
    expect(PROJECT_STATES.archived).toBeUndefined();
  });

  it("draws an unknown state grey instead of throwing or blanking", () => {
    expect(projectState("something-new")).toBeNull();
    expect(projectStateAccent("something-new")).toEqual(accentForHue("gray"));
    expect(projectStateAccent(null)).toEqual(accentForHue("gray"));
  });

  it("accepts the stored spelling case-insensitively", () => {
    expect(projectState("ON_HOLD")?.label).toBe("Paused");
  });
});
