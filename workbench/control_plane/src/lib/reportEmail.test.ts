/**
 * WS-27bk §9.12.8 slice 2 — the report email body.
 *
 * §9.12.8's Done-when names three things this file has to hold:
 *
 *   1. it "renders to an email body with **no colour**",
 *   2. delivery uses the **one** outbound seam,
 *   3. "the scheduled send exists behind a flag that is **off**".
 *
 * Sibling of `inviteEmail.test.ts`, which polices the same seam the same way.
 */
import { describe, expect, it } from "vitest";

import {
  MAX_EMAIL_ROWS,
  type RenderedReport,
  TEXT_BAR_WIDTH,
  isReportEmailEnabled,
  periodLabel,
  reportDocument,
  reportEmail,
  sendReportEmail,
  textBar,
} from "./reportEmail";

const rendered: RenderedReport = {
  report: { name: "Weekly delivery", scope: "portfolio" },
  period_start: "2026-09-07",
  period_end: "2026-09-13",
  sections: {
    finished: {
      projects: [
        { name: "Mobile App", completed: 12, cancelled: 1 },
        { name: "Billing", completed: 5, cancelled: 0 },
      ],
      total_completed: 17,
      total_cancelled: 1,
    },
    throughput: { median_hours: 27, measured: 15 },
    stuck: {
      overdue: [{ name: "Mobile App", overdue: 4 }],
      overdue_total: 4,
    },
    load: {
      people: [
        { assignee: null, open_tasks: 9, overdue: 2 },
        { assignee: "ana@example.test", open_tasks: 6, overdue: 0 },
      ],
      total_tasks: 15,
    },
  },
};

const built = reportEmail(rendered);

describe("reportEmail", () => {
  it("writes NO colour — an email renders outside the theme system", () => {
    // The same two assertions `inviteEmail.test.ts` makes. A hex here can
    // never follow the org's look, and a reader's client may invert it into
    // something unreadable.
    expect(built.html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(built.html).not.toMatch(/\b(rgb|hsl)a?\(/);
    expect(built.html).not.toMatch(/style=/);
  });

  it("names the PERIOD in the subject, not the send date", () => {
    // ⚠️ Two sends of one weekly report are told apart by the week they
    // describe. "Weekly report" alone collapses in a threaded client, and the
    // reader opens last week's copy believing it is this week's.
    expect(built.subject).toContain("Weekly delivery");
    expect(built.subject).toContain("7 – 13 Sep 2026");
  });

  it("carries every headline number the render gave it", () => {
    for (const n of ["17", "27 hours", "4", "15"]) {
      expect(built.text, `text lost ${n}`).toContain(n);
      expect(built.html, `html lost ${n}`).toContain(n);
    }
  });

  it("COMPUTES nothing — a total it invented would be a second answer", () => {
    // 12 + 5 is 17 here, and the body must print the server's 17 rather than
    // add the rows. Proven by feeding a total that does NOT match the rows:
    // the body must say what the server said.
    const odd = reportEmail({
      ...rendered,
      sections: {
        ...rendered.sections,
        finished: {
          projects: [{ name: "Mobile App", completed: 12, cancelled: 0 }],
          total_completed: 99,
          total_cancelled: 0,
        },
      },
    });
    expect(odd.text).toContain("Finished: 99");
    expect(odd.text).not.toContain("Finished: 12");
  });

  it("keeps the denominator beside the median", () => {
    // A median over four tasks and a median over four hundred are not the
    // same claim, and the reader cannot tell them apart without this.
    expect(built.text).toMatch(/27 hours \(over 15 measured\)/);
  });

  it("says 'not measured' rather than inventing a zero", () => {
    const none = reportEmail({
      ...rendered,
      sections: { throughput: { median_hours: null, measured: 0 } },
    });
    expect(none.text).toContain("not measured");
    expect(none.text).not.toMatch(/\b0 hours\b/);
  });

  it("names Unassigned rather than leaving a blank line", () => {
    expect(built.text).toContain("Unassigned: 9");
    expect(built.html).toContain("Unassigned");
  });

  it("ADMITS a truncated project list", () => {
    // ⚠️ A cut list that does not say so reads as the whole of the work.
    const many = reportEmail({
      ...rendered,
      sections: {
        finished: {
          projects: Array.from({ length: MAX_EMAIL_ROWS + 4 }, (_, i) => ({
            name: `Project ${i}`,
            completed: 1,
            cancelled: 0,
          })),
          total_completed: MAX_EMAIL_ROWS + 4,
          total_cancelled: 0,
        },
      },
    });
    expect(many.text).toContain("and 4 more projects");
    expect(many.html).toContain("and 4 more projects");
  });

  it("escapes a report name and a project name, which members type", () => {
    const nasty = reportEmail({
      ...rendered,
      report: { name: '<img src=x onerror="alert(1)">', scope: "node" },
      sections: {
        finished: {
          projects: [{ name: "<script>bad()</script>", completed: 1, cancelled: 0 }],
          total_completed: 1,
          total_cancelled: 0,
        },
      },
    });
    expect(nasty.html).not.toContain("<img");
    expect(nasty.html).not.toContain("<script>");
    expect(nasty.html).toContain("&lt;img");
    expect(nasty.html).toContain("&lt;script&gt;");
  });

  it("omits a section the render did not include", () => {
    const only = reportEmail({
      ...rendered,
      sections: { finished: rendered.sections.finished },
    });
    expect(only.text).toContain("Finished: 17");
    expect(only.text).not.toContain("Overdue");
    expect(only.text).not.toContain("Open work");
  });
});

describe("periodLabel", () => {
  it("says one year once", () => {
    expect(periodLabel("2026-06-29", "2026-09-20")).toBe("29 Jun – 20 Sep 2026");
  });

  it("says both years when the window crosses one", () => {
    expect(periodLabel("2025-12-29", "2026-01-04")).toBe(
      "29 Dec 2025 – 4 Jan 2026",
    );
  });

  it("does not move a date west of Greenwich", () => {
    // ⚠️ `new Date("2026-01-01")` is midnight UTC, which is 31 Dec in every
    // timezone behind it. These are floating calendar dates and are parsed by
    // hand for exactly this reason.
    const label = periodLabel("2026-01-01", "2026-01-07");
    expect(label).toBe("1 – 7 Jan 2026");
    // The failure this guards is the date landing in the PREVIOUS year.
    expect(label).not.toContain("Dec");
    expect(label).not.toContain("2025");
  });
});

describe("the schedule is built DARK", () => {
  it("is off unless the deployment says the exact string true", () => {
    // ⚠️ `Boolean("false")` is true. A flag that arms on "false" is the worst
    // possible default for something that emails real people.
    expect(isReportEmailEnabled({})).toBe(false);
    expect(isReportEmailEnabled({ PROJECT_REPORT_EMAIL_ENABLED: "false" })).toBe(
      false,
    );
    expect(isReportEmailEnabled({ PROJECT_REPORT_EMAIL_ENABLED: "1" })).toBe(
      false,
    );
    expect(isReportEmailEnabled({ PROJECT_REPORT_EMAIL_ENABLED: "true" })).toBe(
      true,
    );
  });

  it("REFUSES to send while the flag is off, rather than doing nothing", async () => {
    // A send path that returns success while sending nothing is how a
    // scheduled report is believed to be running for a month.
    const sent: unknown[] = [];
    await expect(
      sendReportEmail(async (a) => void sent.push(a), {
        to: "someone@example.test",
        rendered,
        env: {},
      }),
    ).rejects.toThrow(/not armed/i);
    expect(sent).toHaveLength(0);
  });

  it("sends exactly ONE message, to one address", async () => {
    // ⚠️ One recipient per call. Many addresses in `to` discloses the
    // recipient list to every reader, and a report's audience is not public.
    const sent: { to: string; from: string }[] = [];
    await sendReportEmail(
      async (a) => void sent.push({ to: a.to, from: a.from }),
      {
        to: "someone@example.test",
        rendered,
        env: {
          PROJECT_REPORT_EMAIL_ENABLED: "true",
          EMAIL_OTP_FROM: "Metorite <no-reply@metorite.com>",
        },
      },
    );
    expect(sent).toHaveLength(1);
    expect(sent[0].to).toBe("someone@example.test");
    // The OTP sender, never a second `from` — D49 item 4.
    expect(sent[0].from).toBe("Metorite <no-reply@metorite.com>");
  });
});

// WS-27bm S7a — the opt-in capacity section.
describe("reportEmail · capacity", () => {
  const withCapacity: RenderedReport = {
    ...rendered,
    sections: {
      capacity: {
        people: [
          {
            assignee: "ana@example.test",
            name: "Ana",
            kind: "person",
            open_tasks: 3,
            hours_basis: true,
            spare_hours_horizon: 12.5,
          },
          {
            assignee: "bo@example.test",
            name: null,
            kind: "person",
            open_tasks: 2,
            hours_basis: false,
            hours_note: "2 open tasks with no estimate.",
          },
          { assignee: null, name: null, kind: "unassigned", open_tasks: 1 },
        ],
        total_tasks: 5,
        hr_visible: true,
        horizon_days: 14,
      },
    },
  };

  it("prints the server's spare hours verbatim", () => {
    const { text } = reportEmail(withCapacity);
    expect(text).toContain("Who has the hours (next 14 days): 5 open");
    expect(text).toContain("Ana: 3 open, 12.5h spare");
    expect(text).toContain("Unassigned: 1 open");
  });

  it("never prints 0h for a row whose hours mean nothing", () => {
    const { text, html } = reportEmail(withCapacity);
    expect(text).toContain("bo@example.test: 2 open, no hours");
    expect(text).not.toMatch(/\b0h\b/);
    expect(html).not.toMatch(/style=/);
  });

  it("says the hours are hidden for a reader without the grant", () => {
    const hidden: RenderedReport = {
      ...withCapacity,
      sections: {
        capacity: {
          people: [
            { assignee: "ana@example.test", name: "Ana", kind: "person", open_tasks: 3 },
          ],
          total_tasks: 3,
          hr_visible: false,
          horizon_days: 14,
        },
      },
    };
    const { text } = reportEmail(hidden);
    expect(text).toContain("Ana: 3 open");
    expect(text).not.toContain("spare");
    expect(text).toContain("Hours need HR read access.");
  });
});

// WS-27bm S7c — the opt-in conflicts section.
describe("reportEmail · conflicts", () => {
  const withConflicts: RenderedReport = {
    ...rendered,
    sections: {
      conflicts: {
        rows: [
          {
            kind: "blocker_late",
            severity: "high",
            sentence: '"Order <steel>" was due on 2026-09-20 and is still open.',
          },
          {
            kind: "parallel_person",
            severity: "medium",
            sentence: "Bo holds 3 open tasks on 2026-09-25, in 2 top-level projects.",
          },
        ],
        total: 2,
        hr_visible: true,
        horizon_days: 14,
      },
    },
  };

  it("prints the server's total and its sentences verbatim, with the severity", () => {
    const { text } = reportEmail(withConflicts);
    expect(text).toContain("Where the plan conflicts: 2");
    expect(text).toContain('High: "Order <steel>" was due on 2026-09-20');
    expect(text).toContain("Medium: Bo holds 3 open tasks");
  });

  it("escapes a title inside a sentence, and carries no colour", () => {
    const { html } = reportEmail(withConflicts);
    expect(html).toContain("Order &lt;steel&gt;");
    expect(html).not.toContain("<steel>");
    expect(html).not.toMatch(/style=/);
  });

  it("says four kinds are hidden for a reader without the grant", () => {
    const hidden: RenderedReport = {
      ...withConflicts,
      sections: {
        conflicts: { ...withConflicts.sections.conflicts!, hr_visible: false },
      },
    };
    expect(reportEmail(hidden).text).toContain("Four kinds need HR read access.");
  });
});

// ---------------------------------------------------------------------------
// WS-27bm S8 — the same layout, as a FILE (spec projects_ai_chat.md §14)
// ---------------------------------------------------------------------------

describe("reportDocument", () => {
  const doc = reportDocument(rendered);

  it("writes the render as Markdown, headline first", () => {
    expect(doc.markdown).toBe(
      [
        "# Weekly delivery",
        "",
        "7 – 13 Sep 2026 · Every space you can see",
        "",
        "**Finished: 17** · 1 cancelled",
        "",
        "- Mobile App: 12 (1 cancelled) · ███████░░░ 12 of 17",
        "- Billing: 5 · ███░░░░░░░ 5 of 17",
        "",
        "Median time to finish: 27 hours (over 15 measured)",
        "",
        "**Overdue: 4**",
        "",
        "- Mobile App: 4 · ██████████ 4 of 4",
        "",
        "Open work: 15",
        "",
        "- Unassigned: 9 (2 overdue) · ██░░░░░░░░ 2 of 9 overdue",
        "- ana@example.test: 6 · ░░░░░░░░░░ 0 of 6 overdue",
        "",
      ].join("\n"),
    );
  });

  it("names the file after the report and its period", () => {
    expect(doc.basename).toBe("Weekly delivery 2026-09-07 to 2026-09-13");
    const odd = reportDocument({
      ...rendered,
      report: { name: 'Q3/Q4: "board" *view*', scope: "node" },
    });
    expect(odd.basename).not.toMatch(/[\/:*?"<>|]/);
  });

  it("carries EVERY row — a file is the whole report, unlike the email", () => {
    const long = {
      ...rendered,
      sections: {
        finished: {
          projects: Array.from({ length: MAX_EMAIL_ROWS + 4 }, (_, i) => ({
            name: `Project ${i}`,
            completed: 1,
            cancelled: 0,
          })),
          total_completed: MAX_EMAIL_ROWS + 4,
          total_cancelled: 0,
        },
      },
    };
    const file = reportDocument(long);
    expect(file.markdown).toContain(`- Project ${MAX_EMAIL_ROWS + 3}: 1 ·`);
    expect(file.markdown).not.toContain("more projects");
    expect(reportEmail(long).text).toContain("and 4 more projects");
  });

  it("makes member text inert in Markdown and in the PDF's HTML", () => {
    const nasty = reportDocument({
      ...rendered,
      report: { name: "*Q3* [x](http://evil) <b>", scope: "portfolio" },
      sections: {
        finished: {
          projects: [{ name: "<script>bad()</script>", completed: 1, cancelled: 0 }],
          total_completed: 1,
          total_cancelled: 0,
        },
      },
    });
    expect(nasty.markdown).toContain(String.raw`# \*Q3\* \[x\](http://evil) \<b\>`);
    expect(nasty.html).not.toContain("<script>");
    expect(nasty.html).toContain("&lt;script&gt;");
  });

  it("draws the PDF from the email's own HTML renderer", () => {
    // One formatter: the email and the file differ only in the row cap.
    expect(doc.html).toBe(reportEmail(rendered).html);
    expect(doc.html).not.toMatch(/style=/);
  });
});

// ---------------------------------------------------------------------------
// Fix round 1 — the email HTML, pinned byte for byte
// ---------------------------------------------------------------------------
//
// WS-27bm S8 moved the email onto one shared layout. These two strings are
// what `origin/main`'s formatter produced before that move, captured by
// running it, so a one-byte change to a separator, a tag or an escape fails
// here. The contains-tests above could not see one (the verifier changed
// " · " to " : " and "<p>" to "<P>", and every test stayed green).
//
// WS-27bn R2b changed them ON PURPOSE: each row now carries its text bar
// and the two figures the bar draws. Nothing else in either string moved.
const GOLDEN_A: RenderedReport = {
  report: { name: "Weekly delivery", scope: "portfolio" },
  period_start: "2026-09-07",
  period_end: "2026-09-13",
  sections: {
    finished: {
      projects: [
        { name: "Mobile App", completed: 12, cancelled: 1 },
        { name: "Billing", completed: 5, cancelled: 0 },
      ],
      total_completed: 17,
      total_cancelled: 1,
    },
    throughput: { median_hours: 27, measured: 15 },
    stuck: { overdue: [{ name: "Mobile App", overdue: 4 }], overdue_total: 4 },
    load: {
      people: [
        { assignee: null, open_tasks: 9, overdue: 2 },
        { assignee: "ana@example.test", open_tasks: 6, overdue: 0 },
      ],
      total_tasks: 15,
    },
  },
};
const GOLDEN_B: RenderedReport = {
  report: { name: "R&D <core>", scope: "node" },
  period_start: "2025-12-29",
  period_end: "2026-01-04",
  sections: {
    finished: {
      projects: Array.from({ length: 12 }, (_, i) => ({ name: `P${i}`, completed: i, cancelled: 0 })),
      total_completed: 66,
      total_cancelled: 0,
    },
    capacity: {
      people: [
        { assignee: "ana@example.test", name: "Ana", kind: "person", open_tasks: 3, hours_basis: true, spare_hours_horizon: 12.5 },
        { assignee: "bo@example.test", name: null, kind: "person", open_tasks: 2, hours_basis: false },
        { assignee: null, name: null, kind: "unassigned", open_tasks: 1 },
      ],
      total_tasks: 6,
      hr_visible: false,
      horizon_days: 14,
    },
    conflicts: {
      rows: [
        { kind: "blocker_late", severity: "high", sentence: '"Order <steel>" is late.' },
        { kind: "parallel_person", severity: "medium", sentence: "Bo holds 3." },
      ],
      total: 2,
      hr_visible: false,
      horizon_days: 14,
    },
  },
};

describe("reportEmail · the exact HTML", () => {
  it("draws the four core sections exactly", () => {
    expect(reportEmail(GOLDEN_A).html).toBe(
      [
      "<div><h2>Weekly delivery</h2>",
      "<p>7 – 13 Sep 2026 · Every space you can see</p>",
      "<p><strong>Finished: 17</strong> · 1 cancelled</p>",
      "<ul><li>Mobile App: 12 (1 cancelled) · ███████░░░ 12 of 17</li><li>Billing: 5 · ███░░░░░░░ 5 of 17</li></ul>",
      "<p>Median time to finish: 27 hours (over 15 measured)</p>",
      "<p><strong>Overdue: 4</strong></p>",
      "<ul><li>Mobile App: 4 · ██████████ 4 of 4</li></ul>",
      "<p>Open work: 15</p>",
      "<ul><li>Unassigned: 9 (2 overdue) · ██░░░░░░░░ 2 of 9 overdue</li><li>ana@example.test: 6 · ░░░░░░░░░░ 0 of 6 overdue</li></ul>",
      "</div>",
      ].join(""),
    );
  });

  it("draws a cut list, capacity, conflicts and the HR notes exactly", () => {
    expect(reportEmail(GOLDEN_B).html).toBe(
      [
      "<div><h2>R&amp;D &lt;core&gt;</h2>",
      "<p>29 Dec 2025 – 4 Jan 2026 · This project</p>",
      "<p><strong>Finished: 66</strong></p>",
      "<ul>" +
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
          .map((i) => `<li>P${i}: ${i} · ${i === 0 ? "░░░░░░░░░░" : "█░░░░░░░░░"} ${i} of 66</li>`)
          .join("") +
        "</ul>",
      "<p>…and 2 more projects</p>",
      "<p>Who has the hours (next 14 days): 6 open</p>",
      "<ul><li>Ana: 3 open, 12.5h spare</li><li>bo@example.test: 2 open, no hours</li><li>Unassigned: 1 open</li></ul>",
      "<p>Hours need HR read access.</p>",
      "<p>Where the plan conflicts: 2</p>",
      "<ul><li>High: &quot;Order &lt;steel&gt;&quot; is late.</li><li>Medium: Bo holds 3.</li></ul>",
      "<p>Four kinds need HR read access.</p>",
      "</div>",
      ].join(""),
    );
  });
});

// ---------------------------------------------------------------------------
// WS-27bn R2b — the text bars (spec projects_reports.md §8 R2b)
// ---------------------------------------------------------------------------

const FULL = "\u2588";
const LIGHT = "\u2591";
const cells = (full: number) =>
  FULL.repeat(full) + LIGHT.repeat(TEXT_BAR_WIDTH - full);

describe("textBar", () => {
  it("draws full blocks for the value and light shade for the rest", () => {
    expect(textBar(5, 10)).toBe(cells(5));
    expect(textBar(10, 10)).toBe(cells(10));
    expect(textBar(0, 10)).toBe(cells(0));
  });

  it("stops at full above 100 percent, and the figures say the rest", () => {
    expect(textBar(46, 40)).toBe(cells(10));
  });

  it("shows one cell for a small value, so it never reads as nothing", () => {
    expect(textBar(1, 1000)).toBe(cells(1));
  });

  it("draws an all-light bar when there is nothing to be a share of", () => {
    expect(textBar(3, 0)).toBe(cells(0));
    expect(textBar(Number.NaN, 5)).toBe(cells(0));
  });
});

describe("reportEmail · the text bars", () => {
  it("finished: each project, completed of total_completed", () => {
    expect(built.text).toContain(`Mobile App: 12 (1 cancelled) · ${cells(7)} 12 of 17`);
  });

  it("stuck: each project, overdue of overdue_total", () => {
    expect(built.text).toContain(`Mobile App: 4 · ${cells(10)} 4 of 4`);
  });

  it("load: each person, overdue of open_tasks, never of total_tasks", () => {
    expect(built.text).toContain(`Unassigned: 9 (2 overdue) · ${cells(2)} 2 of 9 overdue`);
    expect(built.text).not.toMatch(/2 of 15/);
  });

  it("throughput: one bar a week, scaled to the peak week, with no 'of'", () => {
    const { text } = reportEmail({
      ...rendered,
      sections: {
        throughput: {
          median_hours: 27,
          measured: 15,
          series: [
            { week_start: "2026-09-07", completed: 4 },
            { week_start: "2026-09-14", completed: 8 },
            { week_start: "2026-09-21", completed: 0 },
          ],
        },
      },
    });
    expect(text).toContain(`Week of 7 Sep 2026: ${cells(5)} 4`);
    expect(text).toContain(`Week of 14 Sep 2026: ${cells(10)} 8`);
    expect(text).toContain(`Week of 21 Sep 2026: ${cells(0)} 0`);
    expect(text).not.toMatch(/Week of [^\n]* of /);
  });

  it("capacity: committed of working h, with the HR half and hours_basis only", () => {
    const { text } = reportEmail({
      ...rendered,
      sections: {
        capacity: {
          people: [
            {
              assignee: "ana@example.test",
              name: "Ana",
              kind: "person",
              open_tasks: 3,
              hours_basis: true,
              spare_hours_horizon: 0,
              committed_hours_horizon: 46,
              working_hours_horizon: 40,
            },
            {
              assignee: "bo@example.test",
              name: "Bo",
              kind: "person",
              open_tasks: 2,
              hours_basis: false,
              committed_hours_horizon: 5,
              working_hours_horizon: 40,
            },
          ],
          total_tasks: 5,
          hr_visible: true,
          horizon_days: 14,
        },
      },
    });
    expect(text).toContain(`Ana: 3 open, 0h spare · ${cells(10)} 46 of 40 h committed`);
    expect(text).toContain("Bo: 2 open, no hours");
    expect(text).not.toContain("5 of 40");
  });

  it("carries no colour with the bars in it", () => {
    expect(built.html).toContain(FULL);
    expect(built.html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(built.html).not.toMatch(/style=/);
  });
});

describe("reportEmail · outlook (WS-27bn R3a)", () => {
  const withOutlook = (outlook: NonNullable<RenderedReport["sections"]["outlook"]>) =>
    reportEmail({ ...rendered, sections: { outlook } });

  it("says the plan, the forecast and the slip in words, with no colour", () => {
    const got = withOutlook({
      velocity: {
        verdict: "converging",
        finish_date: "2027-02-18",
        remaining_tasks: 7701,
        finished_per_week: 4.2,
        created_per_week: 1.1,
        weeks_sampled: 6,
      },
      plan: {
        planned_finish: "2026-12-01T00:00:00+00:00",
        dated: 30,
        tasks: 31,
        slip_days: 79,
      },
    });
    expect(got.text).toContain("Outlook: converging (7701 open)");
    expect(got.text).toContain("Planned finish: 1 Dec 2026 (30 of 31 open tasks carry a due date)");
    expect(got.text).toContain("Forecast finish: 18 Feb 2027");
    expect(got.text).toContain("79 days late");
    expect(got.text).toContain("Finishing 4.2 a week, adding 1.1 a week, over 6 weeks");
    expect(got.html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(got.html).not.toMatch(/\b(rgb|hsl)a?\(|style=/);
  });

  it("prints no date and no broken word when the verdict has no forecast", () => {
    for (const verdict of ["not_converging", "no_history", "nothing_left"]) {
      const got = withOutlook({
        velocity: { verdict, finish_date: null, remaining_tasks: 12 },
        plan: { planned_finish: null, dated: 0, tasks: 12, slip_days: null },
      });
      for (const word of ["undefined", "NaN", "Forecast finish", "days late", "days early"]) {
        expect(got.text, `${verdict}: ${word}`).not.toContain(word);
      }
      expect(got.text).toContain("Outlook: ");
    }
  });
});
