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
  isDrawableOutlook,
  peopleLine,
  shortDate,
  slipRange,
  velocityLine,
  LEAVE_NOT_COUNTED,
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
      in_directory: 5,
      hours_per_week: 60,
      absences_applied: true,
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

  // WS-27bm S11, projects_ai_chat.md §17.3 rule 10 and §17.4 item 10.
  it("⚠️ says leave is not counted when absences_applied is false", () => {
    const line = capacityLine(report().capacity, false);
    expect(line.detail).toContain(
      "This forecast does not count leave. An admin sees it with leave.",
    );
    expect(line.detail).toContain(LEAVE_NOT_COUNTED.trim());
  });

  it("says nothing about leave when absences_applied is true", () => {
    const line = capacityLine(report().capacity, true);
    expect(line.detail).not.toContain("leave");
  });

  it("says nothing about leave when an older server sends no flag", () => {
    expect(capacityLine(report().capacity).detail).not.toContain("leave");
  });

  it("names working hours, never stated hours", () => {
    expect(capacityLine(report().capacity).detail).toContain("working hours");
  });
});

describe("S11 — no string in outlook.ts says stated", () => {
  it("the hours are the schedule's, so the word is gone", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const src = readFileSync(
      fileURLToPath(new URL("./outlook.ts", import.meta.url)),
      "utf-8",
    );
    expect(src.toLowerCase()).not.toContain("stated");
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
        people: { ...report().people, holding_open_work: 7, in_directory: 2 },
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
      report({ people: { ...report().people, in_directory: 0 } })
    );
    expect(line.detail).toContain("working hours are unknown");
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

  it("rounds the gap to one decimal", () => {
    // 16.8 − 8.1 printed as "8.700000000000001 weeks" (visual review, 2026-09-26).
    const g = forecastGap(
      report({
        velocity: { ...report().velocity, weeks_remaining: 16.8 },
        capacity: { ...report().capacity, weeks_remaining: 8.1 },
      })
    );
    expect(g?.headline).toBe("8.7 weeks");
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

describe("⚠️ reading a response this panel cannot trust", () => {
  // Found by routing /analytics/outlook to `{}` and LOOKING, 2026-09-17.
  // `headlineVerdict` read `o.velocity.verdict` and threw, which took the
  // whole Projects PAGE down — not the panel, the page. Fourth time this
  // class has hit this component: `api.call` casts, it does not validate.

  it("refuses to draw an empty object", () => {
    expect(isDrawableOutlook({} as never)).toBe(false);
  });

  it("refuses null, undefined and a scalar", () => {
    expect(isDrawableOutlook(null)).toBe(false);
    expect(isDrawableOutlook(undefined)).toBe(false);
    expect(isDrawableOutlook(7 as never)).toBe(false);
  });

  it("refuses a velocity block with no verdict", () => {
    expect(
      isDrawableOutlook({ velocity: { finished_per_week: 3 } } as never)
    ).toBe(false);
  });

  it("draws a real report", () => {
    expect(isDrawableOutlook(report())).toBe(true);
  });

  it("⚠️ every line builder survives a missing block rather than throwing", () => {
    // The guard above proves `velocity.verdict`. It proves nothing about
    // `plan`, `capacity` or `people`, and each of those is read downstream.
    const bare = { velocity: { verdict: "converging" } } as never;
    expect(() => headlineVerdict(bare)).not.toThrow();
    expect(() => forecastGap(bare)).not.toThrow();
    expect(() => peopleLine(bare)).not.toThrow();
    expect(() => capacityLine(undefined as never)).not.toThrow();
    expect(() => velocityLine(undefined as never)).not.toThrow();
  });

  it("says so plainly when the forecast block is absent", () => {
    const v = headlineVerdict({ plan: {} } as never);
    expect(v.headline).toBe("—");
    expect(v.detail).toContain("No forecast");
    expect(v.tone).toBe("quiet");
  });

  it("a converging report with NO plan block still reads", () => {
    const v = headlineVerdict({
      velocity: { ...report().velocity },
    } as never);
    expect(v.headline).toContain("On track for");
  });
});

describe("slipRange — the plan against the forecast (WS-27bn R3a)", () => {
  it("places the plan and the forecast on a track from today", () => {
    // Forecast in 10 weeks (70 days), 23 days after the plan, so the plan
    // sits 47 days out. The forecast is the far end of the track.
    const r = slipRange(report())!;
    expect(r.forecast).toBe(100);
    expect(r.plan).toBeCloseTo((47 / 70) * 100, 5);
    expect(r.start).toBeCloseTo(r.plan, 5);
    expect(r.width).toBeCloseTo(100 - r.plan, 5);
    expect(r.slip).toBe("23 days late");
    expect(r.planLabel).toBe("17 Feb 2027");
    expect(r.forecastLabel).toBe("12 Mar 2027");
    expect(r.tone).toBe("bad");
    expect(r.planPassed).toBe(false);
  });

  it("an early forecast puts the plan at the far end, in a good tone", () => {
    const r = slipRange(report({ plan: { ...report().plan, slip_days: -14 } }))!;
    expect(r.plan).toBe(100);
    expect(r.forecast).toBeCloseTo((70 / 84) * 100, 5);
    expect(r.slip).toBe("14 days early");
    expect(r.tone).toBe("good");
  });

  it("a plan date already past sits at today and says so", () => {
    const r = slipRange(report({ plan: { ...report().plan, slip_days: 90 } }))!;
    expect(r.plan).toBe(0);
    expect(r.planPassed).toBe(true);
    expect(r.slip).toBe("90 days late");
  });

  it("one day is a day, and zero is the plan date", () => {
    expect(slipRange(report({ plan: { ...report().plan, slip_days: 1 } }))!.slip).toBe("1 day late");
    const on = slipRange(report({ plan: { ...report().plan, slip_days: 0 } }))!;
    expect(on.slip).toBe("on the plan date");
    expect(on.width).toBeGreaterThanOrEqual(1);
  });

  it("draws nothing when either date is absent", () => {
    for (const verdict of ["not_converging", "no_history", "nothing_left"] as const) {
      const o = report({
        velocity: { ...report().velocity, verdict, finish_date: null, weeks_remaining: null },
        plan: { ...report().plan, slip_days: null },
      });
      expect(slipRange(o), verdict).toBeNull();
    }
    expect(slipRange(report({ plan: { ...report().plan, planned_finish: null, slip_days: null } }))).toBeNull();
    expect(slipRange({} as never)).toBeNull();
    expect(slipRange(null)).toBeNull();
  });
});
