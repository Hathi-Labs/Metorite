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
  isReportEmailEnabled,
  periodLabel,
  reportEmail,
  sendReportEmail,
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
