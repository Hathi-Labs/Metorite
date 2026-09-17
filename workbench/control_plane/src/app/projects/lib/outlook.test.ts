/**
 * The refusals are the product. These tests are mostly about them.
 *
 * ⚠️ The server half is `tests/unit/test_projects_analytics_outlook.py`,
 * which proves the arithmetic. This half proves the client turns each verdict
 * into a sentence a manager can act on, rather than into a dash — which is
 * what every "unknown" state in this app has historically rendered as.
 */
import { describe, expect, it } from "vitest";

import type { OutlookReport } from "./api";
import {
  capacityLine,
  forecastGap,
  headlineVerdict,
  peopleLine,
  shortDate,
  velocityLine,
} from "./outlook";

function report(over: Partial<OutlookReport> = {}): OutlookReport {
  return {
    project_id: "P",
    scope: "node",
    weeks: 6,
    velocity: {
      weeks_sampled: 6,
      finished_per_week: 4.2,
      created_per_week: 1.1,
      net_per_week: 3.1,
      remaining_tasks: 31,
      weeks_remaining: 10,
      finish_date: "2027-03-12",
      verdict: "converging",
    },
    capacity: {
      hours_per_week: 60,
      hours_left: 240,
      estimate_coverage: 1,
      weeks_remaining: 4,
      finish_date: "2026-10-15",
      verdict: "ok",
    },
    plan: {
      planned_finish: "2027-02-17",
      dated: 31,
      tasks: 31,
      slip_days: 23,
    },
    people: {
      holding_open_work: 5,
      with_stated_capacity: 5,
      with_schedule_only: 0,
      hours_per_week: 60,
      leaving_within_90d: 0,
    },
    ...over,
  };
}

describe("shortDate", () => {
  it("⚠️ parses by hand, never through new Date()", () => {
    // `new Date("2027-01-01")` is midnight UTC — a day west of Greenwich,
    // which names the wrong day on the reader's screen.
    expect(shortDate("2027-01-01")).toBe("1 Jan 2027");
    expect(shortDate("2027-12-31")).toBe("31 Dec 2027");
  });

  it("says nothing rather than Invalid Date", () => {
    expect(shortDate(null)).toBe("—");
    expect(shortDate("")).toBe("—");
    expect(shortDate("not-a-date")).toBe("—");
  });
});

describe("velocityLine", () => {
  it("⚠️ NOT CONVERGING carries both rates, because they are the finding", () => {
    const line = velocityLine({
      weeks_sampled: 6,
      finished_per_week: 4.2,
      created_per_week: 5.1,
      net_per_week: -0.9,
      remaining_tasks: 71,
      weeks_remaining: null,
      finish_date: null,
      verdict: "not_converging",
    });
    expect(line.headline).toBe("Not converging");
    expect(line.detail).toContain("4.2/wk");
    expect(line.detail).toContain("5.1/wk");
    expect(line.tone).toBe("bad");
  });

  it("⚠️ tells 'cannot tell yet' apart from 'will never finish'", () => {
    // The two must not wear the same colour. One is a data problem and the
    // other is a delivery problem.
    const thin = velocityLine({
      ...report().velocity,
      verdict: "no_history",
      finish_date: null,
    });
    expect(thin.tone).toBe("quiet");
    expect(thin.headline).toBe("Too early to say");
  });

  it("gives the date and the NET rate when it converges", () => {
    const line = velocityLine(report().velocity);
    expect(line.headline).toBe("12 Mar 2027");
    expect(line.detail).toContain("3.1/wk net");
    expect(line.tone).toBe("good");
  });

  it("says nothing open rather than forecasting an empty backlog", () => {
    const line = velocityLine({
      ...report().velocity,
      verdict: "nothing_left",
      remaining_tasks: 0,
    });
    expect(line.headline).toBe("Nothing open");
  });

  it("survives a verdict this client has never heard of", () => {
    const line = velocityLine({
      ...report().velocity,
      verdict: "teleported" as never,
    });
    expect(line.headline).toBe("—");
    expect(line.detail.length).toBeGreaterThan(0);
  });
});

describe("capacityLine", () => {
  it("⚠️ NOTHING SIZED is not zero hours", () => {
    // Zero hours and nothing sized render identically as "0h", and only one
    // of them means the project is finished.
    const line = capacityLine({
      hours_per_week: 60,
      hours_left: 0,
      estimate_coverage: 0,
      weeks_remaining: null,
      finish_date: null,
      verdict: "no_estimates",
    });
    expect(line.headline).toBe("Nothing sized");
    expect(line.detail).toContain("estimate");
  });

  it("⚠️ refuses to assume forty hours when no capacity is on file", () => {
    const line = capacityLine({
      hours_per_week: 0,
      hours_left: 100,
      estimate_coverage: 1,
      weeks_remaining: null,
      finish_date: null,
      verdict: "no_capacity",
    });
    expect(line.headline).toBe("No capacity on file");
    expect(line.detail).not.toMatch(/40/);
  });

  it("warns when the hours rest on thin coverage", () => {
    const line = capacityLine({
      ...report().capacity,
      estimate_coverage: 0.3,
    });
    expect(line.tone).toBe("warn");
    expect(line.detail).toContain("30%");
    expect(line.detail).toContain("higher");
  });

  it("says so plainly when every task is sized", () => {
    const line = capacityLine(report().capacity);
    expect(line.detail).toContain("every open task");
    expect(line.tone).toBe("good");
  });
});

describe("peopleLine", () => {
  it("⚠️ nobody assigned is a finding, not an empty state", () => {
    const line = peopleLine(
      report({ people: { ...report().people, holding_open_work: 0 } })
    );
    expect(line.headline).toBe("Nobody assigned");
    expect(line.tone).toBe("warn");
  });

  it("says how much of the team's capacity is actually known", () => {
    const line = peopleLine(
      report({
        people: { ...report().people, holding_open_work: 7, with_stated_capacity: 2 },
      })
    );
    expect(line.headline).toBe("7 people");
    expect(line.detail).toContain("2 of 7");
  });

  it("⚠️ flags an engagement ending inside the window", () => {
    // A risk no velocity can see: somebody leaving takes their throughput
    // with them, and the forecast will not notice until after it happens.
    const line = peopleLine(
      report({ people: { ...report().people, leaving_within_90d: 1 } })
    );
    expect(line.detail).toContain("engagement ends");
    expect(line.tone).toBe("warn");
  });

  it("stays quiet when nobody's hours are on file", () => {
    const line = peopleLine(
      report({ people: { ...report().people, with_stated_capacity: 0 } })
    );
    expect(line.detail).toContain("capacity is unknown");
    expect(line.tone).toBe("quiet");
  });
});

describe("headlineVerdict — the answer before the evidence", () => {
  it("⚠️ leads with the SLIP, not the forecast date", () => {
    // Photographed 2026-09-17: "79 days late" sat in the third quadrant of
    // one card, at the same size as "3 people". It is the finding; the date
    // is only how we know it.
    const v = headlineVerdict(report());
    expect(v.headline).toBe("23 days late");
    expect(v.detail).toContain("12 Mar 2027");
    expect(v.detail).toContain("17 Feb 2027");
    expect(v.tone).toBe("bad");
  });

  it("a small slip warns rather than alarms", () => {
    const v = headlineVerdict(
      report({ plan: { ...report().plan, slip_days: 4 } })
    );
    expect(v.tone).toBe("warn");
  });

  it("not converging outranks everything, including a plan", () => {
    const v = headlineVerdict(
      report({
        velocity: {
          ...report().velocity,
          verdict: "not_converging",
          finished_per_week: 4.2,
          created_per_week: 5.1,
          finish_date: null,
        },
      })
    );
    expect(v.headline).toBe("Not converging");
    expect(v.detail).toContain("5.1");
    expect(v.detail).toContain("4.2");
    expect(v.tone).toBe("bad");
  });

  it("says on track when there is no plan to be late against", () => {
    const v = headlineVerdict(
      report({ plan: { ...report().plan, planned_finish: null, slip_days: null } })
    );
    expect(v.headline).toContain("On track for");
    expect(v.tone).toBe("good");
  });

  it("reports running early", () => {
    const v = headlineVerdict(
      report({ plan: { ...report().plan, slip_days: -6 } })
    );
    expect(v.headline).toBe("6 days early");
    expect(v.tone).toBe("good");
  });

  it("⚠️ carries the due-date coverage into the sentence", () => {
    // Inherited from the removed `slipLine`. "Planned 17 Feb" over 4 of 71
    // open tasks is a claim about 4, and it reads like a claim about 71.
    const v = headlineVerdict(
      report({ plan: { ...report().plan, dated: 4, tasks: 71 } })
    );
    expect(v.detail).toContain("4 of 71");
    expect(v.detail).toContain("6%");
  });

  it("stays quiet about coverage when every task is dated", () => {
    expect(headlineVerdict(report()).detail).not.toContain("carry a due date");
  });

  it("refuses when there is not enough history", () => {
    const v = headlineVerdict(
      report({ velocity: { ...report().velocity, verdict: "no_history" } })
    );
    expect(v.headline).toBe("Too early to forecast");
    expect(v.tone).toBe("quiet");
  });
});

describe("forecastGap — the disagreement IS the finding", () => {
  it("⚠️ names the gap when the two methods diverge", () => {
    // They sat five months apart on screen, both as calm coloured dates,
    // and nothing said so.
    const g = forecastGap(
      report({
        velocity: { ...report().velocity, weeks_remaining: 22 },
        capacity: { ...report().capacity, weeks_remaining: 4 },
      })
    );
    expect(g?.headline).toBe("18 weeks");
    expect(g?.tone).toBe("warn");
  });

  it("blames thin coverage when that is the likelier cause", () => {
    const g = forecastGap(
      report({
        velocity: { ...report().velocity, weeks_remaining: 22 },
        capacity: {
          ...report().capacity,
          weeks_remaining: 4,
          estimate_coverage: 0.4,
        },
      })
    );
    expect(g?.detail).toContain("40%");
    expect(g?.detail).toContain("missing work");
  });

  it("⚠️ says ONE WEEK, not 1 weeks", () => {
    // Photographed 2026-09-17. A plural bug in a sentence whose whole job is
    // to be believed costs more than it looks like it should.
    const g = forecastGap(
      report({
        velocity: { ...report().velocity, weeks_remaining: 23 },
        capacity: { ...report().capacity, weeks_remaining: 1 },
      })
    );
    expect(g?.detail).toContain("1 week of");
    expect(g?.detail).not.toContain("1 weeks");
  });

  it("stays silent when the two agree closely enough", () => {
    // Under a month apart is two methods agreeing, not a finding. Saying
    // something here would be noise that trains people to ignore the box.
    expect(
      forecastGap(
        report({
          velocity: { ...report().velocity, weeks_remaining: 10 },
          capacity: { ...report().capacity, weeks_remaining: 8 },
        })
      )
    ).toBeNull();
  });

  it("says nothing when either side refused to forecast", () => {
    expect(
      forecastGap(
        report({ velocity: { ...report().velocity, verdict: "no_history" } })
      )
    ).toBeNull();
    expect(
      forecastGap(
        report({ capacity: { ...report().capacity, verdict: "no_capacity" } })
      )
    ).toBeNull();
  });

  it("notes when the team is beating its own estimates", () => {
    const g = forecastGap(
      report({
        velocity: { ...report().velocity, weeks_remaining: 4 },
        capacity: { ...report().capacity, weeks_remaining: 20 },
      })
    );
    expect(g?.tone).toBe("good");
    expect(g?.detail).toContain("faster");
  });
});
