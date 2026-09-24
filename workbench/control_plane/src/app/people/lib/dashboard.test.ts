/**
 * WS-28j1 — the dashboard's presentation half.
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.7.1, §5.7.2 · **D-PC-14**.
 *
 * **The arithmetic is deliberately not tested here, because it is not here.**
 * The pills, the reasons and the at-risk list are computed once in
 * `gateway/workload.py` and covered by `tests/unit/test_people_dashboard.py`; a
 * second implementation in the browser would be a second answer to "is this
 * person overloaded". What these cases pin is ordering, grouping, wording, and
 * the two refusals: no ranking of people, and no confident number built on a
 * missing estimate.
 */

import { describe, expect, it } from "vitest";

import {
  type DashboardRow,
  NO_DEPARTMENT,
  PILL_HUE,
  PILL_LABEL,
  type Rollup,
  article,
  capacityBar,
  describeActivity,
  describeDeadline,
  describeRollup,
  describeScope,
  describeSpread,
  groupByDepartment,
  hours,
  pillTotals,
  sortRows,
  worstShortfall,
} from "./dashboard";

const TODAY = new Date("2026-08-13T09:00:00");

function row(over: Partial<DashboardRow> = {}): DashboardRow {
  return {
    person_id: "p1",
    name: "Priya",
    department: "Engineering",
    kind: "person",
    open_tasks: 4,
    overdue: 0,
    unestimated: 0,
    committed_hours: 20,
    committed_this_week: 20,
    contracted_hours: 40,
    projects: [],
    projects_total: 0,
    at_risk: [],
    away_this_week: false,
    pill: "on_track",
    flags: [],
    hours_basis: true,
    ...over,
  };
}

function group(over: Partial<Rollup> = {}): Rollup {
  return {
    department: "Engineering",
    headcount: 12,
    contracted_hours: 480,
    committed_hours: 300,
    pills: { behind: 0, at_risk: 0, overloaded: 0, idle: 0, on_track: 12 },
    away: [],
    no_open_work: [],
    unestimated_people: 0,
    needs_attention: 0,
    strain: 0,
    spread: null,
    ...over,
  };
}

function risk(shortfall: number) {
  return {
    task_id: "t1",
    title: "Ship it",
    due_on: "2026-08-14",
    own_hours: 8,
    needed_hours: 8 + shortfall,
    available_hours: 8,
    shortfall_hours: shortfall,
  };
}

describe("sortRows", () => {
  it("puts what needs a decision at the top", () => {
    const rows = [
      row({ name: "OnTrack", pill: "on_track" }),
      row({ name: "Idle", pill: "idle" }),
      row({ name: "Behind", pill: "behind", overdue: 1 }),
      row({ name: "AtRisk", pill: "at_risk", at_risk: [risk(4)] }),
      row({ name: "Over", pill: "overloaded" }),
    ];
    expect(sortRows(rows).map((r) => r.name)).toEqual([
      "Behind",
      "AtRisk",
      "Over",
      "Idle",
      "OnTrack",
    ]);
  });

  it("separates two rows in one pill by the SIZE OF THE PROBLEM", () => {
    // Still an ordering of work, not of people (D-PC-14): the row with the
    // bigger shortfall is the conversation to have first.
    const rows = [
      row({ name: "Small", pill: "at_risk", at_risk: [risk(2)] }),
      row({ name: "Large", pill: "at_risk", at_risk: [risk(20)] }),
    ];
    expect(sortRows(rows).map((r) => r.name)).toEqual(["Large", "Small"]);
  });

  it("orders the behind by how many dates were missed", () => {
    const rows = [
      row({ name: "One", pill: "behind", overdue: 1 }),
      row({ name: "Five", pill: "behind", overdue: 5 }),
    ];
    expect(sortRows(rows).map((r) => r.name)).toEqual(["Five", "One"]);
  });

  it("sinks the unpilled rows — an agent is not idle or behind", () => {
    const rows = [
      row({ name: "Bot", kind: "agent", pill: null }),
      row({ name: "Zara", pill: "on_track" }),
    ];
    expect(sortRows(rows).map((r) => r.name)).toEqual(["Zara", "Bot"]);
  });

  it("is alphabetical where nothing has to be acted on", () => {
    const rows = [
      row({ name: "Zara", pill: "on_track" }),
      row({ name: "Ana", pill: "on_track" }),
    ];
    expect(sortRows(rows).map((r) => r.name)).toEqual(["Ana", "Zara"]);
  });

  it("does not mutate what it is given", () => {
    const rows = [row({ name: "Z", pill: "on_track" }), row({ name: "A", pill: "behind" })];
    sortRows(rows);
    expect(rows[0].name).toBe("Z");
  });
});

describe("worstShortfall", () => {
  it("is the largest gap, not the first one", () => {
    expect(
      worstShortfall(row({ at_risk: [risk(3), risk(11), risk(5)] }))
    ).toBe(11);
  });

  it("is zero for a row with nothing at risk", () => {
    expect(worstShortfall(row())).toBe(0);
  });
});

describe("groupByDepartment", () => {
  it("trails the people nobody has placed rather than dropping them", () => {
    const groups = groupByDepartment([
      row({ name: "A", department: null }),
      row({ name: "B", department: "Sales" }),
      row({ name: "C", department: "Engineering" }),
    ]);
    expect(groups.map((g) => g.department)).toEqual([
      "Engineering",
      "Sales",
      NO_DEPARTMENT,
    ]);
  });

  it("treats a blank department as unassigned", () => {
    const groups = groupByDepartment([row({ department: "   " })]);
    expect(groups[0].department).toBe(NO_DEPARTMENT);
  });
});

describe("capacityBar", () => {
  it("is committed against contracted, for the week", () => {
    const bar = capacityBar(row({ committed_this_week: 20 }));
    expect(bar.percent).toBe(50);
    expect(bar.unknown).toBe(false);
    expect(bar.label).toContain("20h of 40h this week");
  });

  it("refuses a percentage where nothing is estimated", () => {
    // A confident 0% is how somebody holding thirty un-estimated tasks gets
    // handed a thirty-first.
    const bar = capacityBar(
      row({ open_tasks: 30, unestimated: 30, committed_this_week: 0, hours_basis: false })
    );
    expect(bar.unknown).toBe(true);
    expect(bar.percent).toBe(0);
    expect(bar.label).toContain("30 with no estimate");
  });

  it("says so plainly when there is nothing assigned", () => {
    const bar = capacityBar(
      row({ open_tasks: 0, committed_this_week: 0, hours_basis: false })
    );
    expect(bar.label).toBe("Nothing assigned");
  });

  it("carries the unestimated count alongside a real figure", () => {
    expect(capacityBar(row({ unestimated: 3 })).label).toContain(
      "3 with no estimate"
    );
  });

  it("clamps rather than drawing a bar past its track", () => {
    expect(capacityBar(row({ committed_this_week: 400 })).percent).toBe(100);
  });
});

describe("describeDeadline", () => {
  it("counts the days it is late by", () => {
    expect(describeDeadline("2026-08-10T00:00:00", TODAY)).toBe(
      "Overdue by 3 days"
    );
    expect(describeDeadline("2026-08-12T00:00:00", TODAY)).toBe(
      "Overdue by a day"
    );
  });

  it("uses the words people use for the near ones", () => {
    expect(describeDeadline("2026-08-13T17:00:00", TODAY)).toBe("Due today");
    expect(describeDeadline("2026-08-14T00:00:00", TODAY)).toBe("Due tomorrow");
    expect(describeDeadline("2026-08-18T00:00:00", TODAY)).toBe("Due in 5 days");
  });

  it("falls back to a date further out", () => {
    // ⚠️ Asserted by PARTS: `toLocaleDateString` renders in the viewer's
    // locale, and pinning one here would make the PRODUCT wrong to make the
    // TEST tidy (the call `absence.test.ts` already made).
    const line = describeDeadline("2026-09-30T00:00:00", TODAY);
    expect(line).toMatch(/30/);
    expect(line).toMatch(/Sep/);
  });

  it("says there is no deadline rather than inventing one", () => {
    expect(describeDeadline(null, TODAY)).toBe("No deadline");
  });

  it("shows an unparseable date rather than swallowing it", () => {
    expect(describeDeadline("not-a-date", TODAY)).toBe("not-a-date");
  });
});

describe("describeActivity", () => {
  it("distinguishes absent from fresh", () => {
    // "Quiet because nothing is due" and "quiet because nothing is happening"
    // are different facts, and a missing timestamp must not read as today.
    expect(describeActivity(null, TODAY)).toBe("No recent activity");
    expect(describeActivity("2026-08-13T08:00:00", TODAY)).toBe("Active today");
  });

  it("reads in the units a person thinks in", () => {
    expect(describeActivity("2026-08-12T08:00:00", TODAY)).toBe(
      "Active yesterday"
    );
    expect(describeActivity("2026-08-08T08:00:00", TODAY)).toBe(
      "Active 5 days ago"
    );
    expect(describeActivity("2026-07-01T08:00:00", TODAY)).toBe(
      "Active 6 weeks ago"
    );
  });
});

describe("pillTotals", () => {
  it("counts every pill, including the ones at zero", () => {
    const totals = pillTotals([
      row({ pill: "behind" }),
      row({ pill: "behind" }),
      row({ pill: "idle" }),
      row({ kind: "agent", pill: null }),
    ]);
    expect(totals).toEqual({
      behind: 2,
      at_risk: 0,
      overloaded: 0,
      idle: 1,
      on_track: 0,
    });
  });
});

describe("describeRollup", () => {
  it("leads with what to act on, not with headcount", () => {
    // "12 people" is true and useless; "3 of 12 need attention" is where to
    // look first.
    const line = describeRollup(group({ needs_attention: 3 }));
    expect(line.startsWith("3 of 12 need attention")).toBe(true);
  });

  it("says so plainly when nothing is overdue", () => {
    expect(describeRollup(group())).toContain("12 people, nothing overdue");
  });

  it("carries the hours both ways round", () => {
    expect(describeRollup(group())).toContain("300h due against 480h");
  });

  it("names who is away, and counts who has nothing", () => {
    const line = describeRollup(
      group({ away: ["Priya", "Ravi"], no_open_work: ["Sam"] })
    );
    expect(line).toContain("Priya, Ravi away this week");
    expect(line).toContain("1 with no open work");
  });

  it("carries the unestimated caveat with the total", () => {
    // A sum over rows that are half unestimated is a confident number built on
    // missing data — the same reason a person row carries `unestimated`.
    expect(describeRollup(group({ unestimated_people: 4 }))).toContain(
      "4 with nothing estimated"
    );
  });

  it("says person, not people, for one", () => {
    expect(describeRollup(group({ headcount: 1 }))).toContain("1 person");
  });
});

describe("describeSpread", () => {
  it("is the sentence that starts the conversation", () => {
    const line = describeSpread(
      group({
        spread: {
          gap_hours: 40,
          most: {
            person_id: "a",
            name: "Priya",
            committed_hours: 46,
            contracted_hours: 40,
            percent: 115,
          },
          least: {
            person_id: "b",
            name: "Ravi",
            committed_hours: 6,
            contracted_hours: 40,
            percent: 15,
          },
        },
      })
    );
    // ⚠️ Both people named, both figures in HOURS. "Priya has 46h due, Ravi has
    // 6h" is arguable and actionable; a bare percentage gap is a score with two
    // names attached (D-PC-14).
    expect(line).toContain("Priya has 46h due this week");
    expect(line).toContain("Ravi has 6h");
    expect(line).toContain("40h gap");
  });

  it("says nothing where a spread means nothing", () => {
    // Rendering "0h" for a one-person department would read as a balanced team.
    expect(describeSpread(group())).toBeNull();
  });

  /**
   * ⚠️ The article was hardcoded `a`, so a gap of 8, 11, 18 or anything in the
   * eighties read "a 18h gap". Not an edge case — those are ordinary gaps, and
   * this one helper is rendered by BOTH `/people/dashboard` and
   * `/people/overview`. Found by reading the rendered page on 2026-09-23.
   */
  it("picks the article from how the number is said", () => {
    const gap = (gap_hours: number) =>
      describeSpread(
        group({
          spread: {
            gap_hours,
            most: { person_id: "a", name: "Priya", committed_hours: 46, contracted_hours: 40, percent: 115 },
            least: { person_id: "b", name: "Ravi", committed_hours: 6, contracted_hours: 40, percent: 15 },
          },
        })
      );
    expect(gap(18)).toContain("— an 18h gap");
    expect(gap(8)).toContain("— an 8h gap");
    expect(gap(11)).toContain("— an 11h gap");
    expect(gap(85)).toContain("— an 85h gap");
    expect(gap(40)).toContain("— a 40h gap");
    expect(gap(1)).toContain("— a 1h gap");
    // 118 is "one hundred and eighteen" — the leading sound is what decides,
    // not the digits the number happens to end in.
    expect(gap(118)).toContain("— a 118h gap");
  });
});

describe("article", () => {
  it("answers on the leading sound, not the spelling", () => {
    for (const n of [8, 11, 18, 80, 85, 89, 800, 880]) {
      expect(article(n), `${n}`).toBe("an");
    }
    for (const n of [1, 2, 7, 9, 10, 12, 19, 40, 90, 100, 118, 180]) {
      expect(article(n), `${n}`).toBe("a");
    }
  });

  it("ignores a sign and a fraction", () => {
    expect(article(-18)).toBe("an");
    expect(article(18.5)).toBe("an");
    expect(article(8.25)).toBe("an");
  });
});

describe("describeScope", () => {
  it("says the counts are partial rather than presenting them as whole", () => {
    // §5.7.5: a silently-truncated total is worse than no total, because it
    // looks authoritative.
    expect(describeScope({ partial: true, work_visible: true })).toContain(
      "projects you can open"
    );
  });

  it("distinguishes 'not your surface' from 'nobody has work'", () => {
    expect(describeScope({ partial: false, work_visible: false })).toContain(
      "Projects app"
    );
  });

  it("says nothing when there is nothing to qualify", () => {
    expect(describeScope({ partial: false, work_visible: true })).toBeNull();
  });
});

describe("the vocabulary", () => {
  it("resolves every pill through the shared hue names", () => {
    // AGENTS.md rule 4: `src/lib/statusAccent.ts` is the one place a status
    // becomes a colour. These are NAMES resolved there, never class strings
    // written here — a page-local palette is what looks fine until somebody
    // switches the theme.
    expect(Object.keys(PILL_HUE).sort()).toEqual(
      Object.keys(PILL_LABEL).sort()
    );
    for (const hue of Object.values(PILL_HUE)) {
      expect(hue).toMatch(/^(gray|red|amber|green|blue|violet)$/);
    }
  });

  it("gives every pill a distinct hue — five signals, five colours", () => {
    expect(new Set(Object.values(PILL_HUE)).size).toBe(
      Object.keys(PILL_HUE).length
    );
  });
});

describe("hours", () => {
  it("drops a trailing zero on a figure people read at a glance", () => {
    expect(hours(40)).toBe("40h");
    expect(hours(37.5)).toBe("37.5h");
    expect(hours(37.25)).toBe("37.3h");
  });

  it("renders an absent figure as absent, not as zero", () => {
    expect(hours(null)).toBe("—");
    expect(hours(undefined)).toBe("—");
  });
});
