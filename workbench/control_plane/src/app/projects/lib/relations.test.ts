/**
 * Projects · dependencies and subtasks in the browser (WS-27p).
 *
 * One table carries three relationships, and each means something different
 * depending on which end you stand at. Showing them under one heading would
 * tell people the opposite of the truth half the time, so which link lands in
 * which section is what these assert.
 */

import { describe, expect, it } from "vitest";

import {
  completeRelations,
  CLOSED,
  type Direction,
  type LinkType,
  type RelatedTask,
  type Relations,
  cardSummary,
  isResolved,
  populated,
  progressLabel,
  progressPercent,
  section,
} from "./relations";

const link = (
  type: LinkType,
  direction: Direction,
  title = "other"
): RelatedTask => ({
  id: `t-${title}`,
  link_id: `l-${title}-${direction}`,
  link_type: type,
  direction,
  title,
});

const relations = (over: Partial<Relations> = {}): Relations => ({
  subtasks: [],
  progress: { done: 0, total: 0 },
  links: [],
  blocked_by: [],
  ...over,
});

describe("section", () => {
  const links = [
    link("blocks", "outgoing", "holds-up"),
    link("blocks", "incoming", "waiting-on"),
    link("relates_to", "outgoing", "related"),
  ];

  it("keeps the two directions of blocks apart", () => {
    // Outgoing is "this holds those up"; incoming is "this is waiting". One
    // heading for both tells people the opposite of the truth half the time.
    expect(section(links, "blocks", "outgoing").map((l) => l.title)).toEqual([
      "holds-up",
    ]);
    expect(section(links, "blocks", "incoming").map((l) => l.title)).toEqual([
      "waiting-on",
    ]);
  });

  it("does not mix link types", () => {
    expect(section(links, "relates_to", "outgoing")).toHaveLength(1);
    expect(section(links, "duplicates", "outgoing")).toEqual([]);
  });
});

describe("populated", () => {
  it("drops empty sections rather than heading them", () => {
    // Six empty headings on every task is how a panel becomes something people
    // scroll past.
    const got = populated([link("blocks", "incoming")]);
    expect(got).toHaveLength(1);
    expect(got[0].label).toBe("Blocked by");
  });

  it("puts Blocked by FIRST, because it is the only one that changes what to do next", () => {
    const got = populated([
      link("relates_to", "outgoing", "a"),
      link("blocks", "incoming", "b"),
    ]);
    expect(got.map((s) => s.label)).toEqual(["Blocked by", "Related"]);
  });

  it("labels the two directions of duplicates differently", () => {
    const got = populated([
      link("duplicates", "outgoing", "a"),
      link("duplicates", "incoming", "b"),
    ]);
    expect(got.map((s) => s.label)).toEqual(["Duplicates", "Duplicated by"]);
  });

  it("is empty for a task with no links at all", () => {
    expect(populated([])).toEqual([]);
  });
});

describe("progress", () => {
  it("reads as a count, not a percentage", () => {
    // 33% is a worse answer than "1 of 3" to the question people are asking.
    expect(progressLabel({ done: 1, total: 3 })).toBe("1 of 3");
  });

  it("gives a bar a number rather than NaN when there is nothing", () => {
    expect(progressPercent({ done: 0, total: 0 })).toBe(0);
  });

  it("rounds to a whole percent", () => {
    expect(progressPercent({ done: 1, total: 3 })).toBe(33);
    expect(progressPercent({ done: 3, total: 3 })).toBe(100);
  });
});

describe("isResolved", () => {
  it("counts cancelled as resolved, like the gateway does", () => {
    expect(isResolved("done")).toBe(true);
    expect(isResolved("cancelled")).toBe(true);
  });

  it("treats an open or missing category as unresolved", () => {
    expect(isResolved("in_progress")).toBe(false);
    expect(isResolved(null)).toBe(false);
    expect(isResolved(undefined)).toBe(false);
  });

  it("mirrors the gateway's closing categories", () => {
    expect([...CLOSED].sort()).toEqual(["cancelled", "done"]);
  });
});

describe("cardSummary", () => {
  it("says nothing when there is nothing to say", () => {
    // A card with no relations must not grow an extra row.
    expect(cardSummary(relations())).toBeNull();
    expect(cardSummary(undefined)).toBeNull();
  });

  it("shows subtask progress when there are subtasks", () => {
    expect(cardSummary(relations({ progress: { done: 1, total: 4 } }))).toBe("1 of 4");
  });

  it("puts BLOCKED ahead of progress", () => {
    // A task with subtasks and an unfinished blocker cannot be started, and
    // that is the more urgent fact.
    const got = cardSummary(
      relations({
        progress: { done: 1, total: 4 },
        blocked_by: [link("blocks", "incoming")],
      })
    );
    expect(got).toBe("Blocked by 1");
  });

  it("says nothing for a task whose blockers have all finished", () => {
    // The gateway already filtered them out; this is the consequence — a card
    // that stayed marked blocked after its dependency shipped is a card people
    // learn to ignore.
    expect(cardSummary(relations({ blocked_by: [] }))).toBeNull();
  });
});

describe("completeRelations", () => {
  /**
   * ⚠️ These are the fence for a blank page, not for a tidy type.
   *
   * `RelationsBlock` guarded `!data` and then read `data.links`. A body one
   * field short is truthy, so it threw — and because `TaskPanel` renders
   * outside `LayoutBoundary`, the throw reached the React root and blanked the
   * whole document. Measured 2026-09-03 with the visual-review rig.
   */
  it("fills every array a reader walks", () => {
    const got = completeRelations({} as never);
    expect(got).toEqual({
      subtasks: [],
      links: [],
      blocked_by: [],
      progress: { done: 0, total: 0 },
    });
  });

  it("fills only what is missing", () => {
    const rows = [link("blocks", "incoming")];
    const got = completeRelations({ blocked_by: rows } as never);
    expect(got?.blocked_by).toBe(rows);
    expect(got?.links).toEqual([]);
  });

  it("keeps null meaning null, because the caller renders nothing for it", () => {
    expect(completeRelations(null)).toBeNull();
    expect(completeRelations(undefined)).toBeNull();
  });
});
