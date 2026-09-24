/**
 * WS-27bk §9.12.8 slice 2 — a rendered report, as an email body.
 *
 * The owner drew the line: *"analytics is what you look at, a report is what
 * gets delivered."* Slice 1 renders in the app. This turns the SAME render
 * into a message.
 *
 * ⚠️ **It formats. It does NOT compute.** Every number arrives from
 * `GET /projects/reports/{id}/render`, which re-runs §9.12.7's own SQL. A
 * total worked out here would be the second set of numbers §9.12.8 exists to
 * prevent, and it would be the copy a person reads and forwards.
 *
 * ⚠️ **NO COLOUR — an email renders outside the theme system.** A hex value
 * here can never follow the org's look, and the reader's client may invert it
 * to something unreadable. Layout only, exactly as `inviteEmail` does, and
 * `reportEmail.test.ts` fails on any hex, `rgb()` or `hsl()`.
 *
 * ⚠️ **ONE outbound seam.** `resendSender` and `emailOtpFrom` come from
 * `@/lib/emailOtp` (AGENTS.md rule 4). A second transport would put a second
 * bearer mint in the route tree, and `gateway.test.ts`'s allow-list refuses it
 * by name. There is no second `from` either — D49 item 4: two sender settings
 * is two verified addresses to keep in step, and the second to drift is the
 * one that starts bouncing.
 */
import {
  type EmailOtpEnv,
  type ResendSender,
  emailOtpFrom,
} from "@/lib/emailOtp";

/**
 * The environment this feature reads.
 *
 * ⚠️ `PROJECT_REPORT_EMAIL_ENABLED` is the flag §9.12.8 requires, and it is
 * **OFF** unless the deployment says otherwise. The spec is explicit: *"the
 * schedule is owner-gated to arm. Build it dark, default off."* A job that
 * emails people on a timer is an outward act, and it is not mine to start.
 */
export interface ReportEmailEnv extends EmailOtpEnv {
  PROJECT_REPORT_EMAIL_ENABLED?: string;
}

/** Whether the deployment has ARMED scheduled report delivery. */
export function isReportEmailEnabled(env: ReportEmailEnv): boolean {
  // ⚠️ An exact match on "true". `Boolean("false")` is true, and a flag that
  // arms on the string "false" is the worst possible default.
  return env.PROJECT_REPORT_EMAIL_ENABLED === "true";
}

/** What `GET /projects/reports/{id}/render` answers, as this file needs it. */
export interface RenderedReport {
  report: { name: string; scope: "portfolio" | "node" };
  period_start: string;
  period_end: string;
  sections: {
    finished?: {
      projects: { name: string; completed: number; cancelled: number }[];
      total_completed: number;
      total_cancelled: number;
    };
    throughput?: { median_hours: number | null; measured: number };
    load?: {
      people: { assignee: string | null; open_tasks: number; overdue: number }[];
      total_tasks: number;
    };
    /**
     * WS-27bm S7a. Opt-in, and the HR half is absent for a reader without
     * `admin:members:read` — so every hours field is optional.
     */
    capacity?: {
      people: {
        assignee: string | null;
        name: string | null;
        kind: string;
        open_tasks: number;
        hours_basis?: boolean;
        spare_hours_horizon?: number | null;
        hours_note?: string | null;
      }[];
      total_tasks: number;
      hr_visible: boolean;
      horizon_days: number;
    };
    stuck?: {
      overdue: { name: string; overdue: number }[];
      overdue_total: number;
    };
  };
}

/**
 * ⚠️ Every interpolated value is customer-chosen — a report name and project
 * names are typed by a member. The same escape `inviteEmail` uses, and for the
 * same reason: an org name from the browser must not put arbitrary markup into
 * a message we send from our own verified sender.
 */
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** "29 Jun – 20 Sep 2026", built without `new Date()`. */
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

function day(iso: string): string {
  // ⚠️ Parsed by hand. These are floating calendar dates, and
  // `new Date("2026-09-20")` reads midnight UTC — a day west of Greenwich the
  // report would name the wrong week.
  const [y, m, d] = iso.split("-");
  return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

export function periodLabel(from: string, to: string): string {
  const [fy, fm, fd] = from.split("-");
  const [ty, tm, td] = to.split("-");
  const tail = `${MONTHS[Number(tm) - 1]} ${ty}`;
  // ⚠️ Say each part ONCE. "7 Sep – 13 Sep 2026" repeats a month the reader
  // already has, and a subject line is the one place that costs something:
  // mail clients truncate it, and the repeated word pushes out the year.
  if (fy === ty && fm === tm) return `${Number(fd)} – ${Number(td)} ${tail}`;
  if (fy === ty) {
    return `${Number(fd)} ${MONTHS[Number(fm) - 1]} – ${Number(td)} ${tail}`;
  }
  return `${day(from)} – ${day(to)}`;
}

/** Hours as a person reads them. Mirrors the panel, deliberately. */
function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "not measured";
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${Math.round(hours)} hours`;
  return `${Math.round(hours / 24)} days`;
}

/** How many project lines a message carries before it stops being readable. */
export const MAX_EMAIL_ROWS = 10;

/**
 * Subject, text and HTML for one rendered report.
 *
 * ⚠️ **The subject names the PERIOD, not the send date.** Two sends of one
 * weekly report are told apart by the week they describe. A subject reading
 * only "Weekly report" collapses in a threaded mail client, and the reader
 * opens last week's copy believing it is this week's.
 */
export function reportEmail(
  rendered: RenderedReport,
): { subject: string; text: string; html: string } {
  const { report, sections } = rendered;
  const period = periodLabel(rendered.period_start, rendered.period_end);
  const subject = `${report.name} — ${period}`;

  const scope =
    report.scope === "portfolio" ? "Every space you can see" : "This project";

  const lines: string[] = [`${report.name}`, period, scope, ""];
  const blocks: string[] = [
    `<h2>${escapeHtml(report.name)}</h2>`,
    `<p>${escapeHtml(period)} · ${escapeHtml(scope)}</p>`,
  ];

  const fin = sections.finished;
  if (fin) {
    lines.push(
      `Finished: ${fin.total_completed}` +
        (fin.total_cancelled ? ` (${fin.total_cancelled} cancelled)` : ""),
    );
    blocks.push(
      `<p><strong>Finished: ${fin.total_completed}</strong>` +
        (fin.total_cancelled
          ? ` · ${fin.total_cancelled} cancelled`
          : "") +
        `</p>`,
    );
    const rows = fin.projects.slice(0, MAX_EMAIL_ROWS);
    if (rows.length) {
      const li = rows.map(
        (p) =>
          `<li>${escapeHtml(p.name)}: ${p.completed}` +
          (p.cancelled ? ` (${p.cancelled} cancelled)` : "") +
          `</li>`,
      );
      blocks.push(`<ul>${li.join("")}</ul>`);
      for (const p of rows) {
        lines.push(
          `  ${p.name}: ${p.completed}` +
            (p.cancelled ? ` (${p.cancelled} cancelled)` : ""),
        );
      }
      // ⚠️ Said out loud when the list is cut. A truncated list that does not
      // admit it reads as the whole of the work.
      if (fin.projects.length > rows.length) {
        const more = `  …and ${fin.projects.length - rows.length} more projects`;
        lines.push(more);
        blocks.push(`<p>${escapeHtml(more.trim())}</p>`);
      }
    }
    lines.push("");
  }

  const thr = sections.throughput;
  if (thr) {
    const text = `Median time to finish: ${duration(thr.median_hours)}`;
    // The denominator travels with the median. A median over four tasks and a
    // median over four hundred are not the same claim.
    const over = ` (over ${thr.measured} measured)`;
    lines.push(text + over, "");
    blocks.push(`<p>${escapeHtml(text + over)}</p>`);
  }

  const stuck = sections.stuck;
  if (stuck && stuck.overdue_total > 0) {
    lines.push(`Overdue: ${stuck.overdue_total}`);
    blocks.push(`<p><strong>Overdue: ${stuck.overdue_total}</strong></p>`);
    const rows = stuck.overdue.slice(0, MAX_EMAIL_ROWS);
    blocks.push(
      `<ul>${rows
        .map((o) => `<li>${escapeHtml(o.name)}: ${o.overdue}</li>`)
        .join("")}</ul>`,
    );
    for (const o of rows) lines.push(`  ${o.name}: ${o.overdue}`);
    lines.push("");
  }

  const load = sections.load;
  if (load) {
    lines.push(`Open work: ${load.total_tasks}`);
    blocks.push(`<p>Open work: ${load.total_tasks}</p>`);
    const busiest = load.people.slice(0, MAX_EMAIL_ROWS);
    if (busiest.length) {
      blocks.push(
        `<ul>${busiest
          .map(
            (p) =>
              `<li>${escapeHtml(p.assignee ?? "Unassigned")}: ` +
              `${p.open_tasks}` +
              (p.overdue ? ` (${p.overdue} overdue)` : "") +
              `</li>`,
          )
          .join("")}</ul>`,
      );
      for (const p of busiest) {
        lines.push(
          `  ${p.assignee ?? "Unassigned"}: ${p.open_tasks}` +
            (p.overdue ? ` (${p.overdue} overdue)` : ""),
        );
      }
    }
    lines.push("");
  }

  const cap = sections.capacity;
  if (cap) {
    const head = `Who has the hours (next ${cap.horizon_days} days): ${cap.total_tasks} open`;
    lines.push(head);
    blocks.push(`<p>${escapeHtml(head)}</p>`);
    const described = cap.people.slice(0, MAX_EMAIL_ROWS).map((p) => {
      const who =
        p.kind === "unassigned" || !p.assignee
          ? "Unassigned"
          : p.name || p.assignee;
      // ⚠️ The spare figure is the server's, verbatim, and it is printed
      // ONLY when the server said the hours mean something. A missing figure
      // is "no hours", never "0h" — zero reads as free.
      const hoursPart =
        p.hours_basis && typeof p.spare_hours_horizon === "number"
          ? `, ${p.spare_hours_horizon}h spare`
          : p.hours_basis === false
            ? ", no hours"
            : "";
      return `${who}: ${p.open_tasks} open${hoursPart}`;
    });
    if (described.length) {
      blocks.push(
        `<ul>${described.map((d) => `<li>${escapeHtml(d)}</li>`).join("")}</ul>`,
      );
      for (const d of described) lines.push(`  ${d}`);
    }
    if (!cap.hr_visible) {
      const note = "Hours need HR read access.";
      lines.push(`  ${note}`);
      blocks.push(`<p>${escapeHtml(note)}</p>`);
    }
    lines.push("");
  }

  return {
    subject,
    text: lines.join("\n").trimEnd(),
    // ⚠️ No style attribute, no colour, no table. See the module header.
    html: `<div>${blocks.join("")}</div>`,
  };
}

/**
 * Build the message and hand it to the injected transport.
 *
 * ⚠️ **ONE recipient per call, and the caller loops.** A single message with
 * many addresses in `to` discloses the recipient list to every reader, and a
 * report's audience is not public inside a company.
 *
 * ⚠️ **It REFUSES when the flag is off**, rather than quietly doing nothing.
 * A send path that returns success while sending nothing is how a scheduled
 * report is believed to be running for a month.
 */
export async function sendReportEmail(
  send: ResendSender,
  opts: { to: string; rendered: RenderedReport; env: ReportEmailEnv },
): Promise<void> {
  if (!isReportEmailEnabled(opts.env)) {
    throw new Error(
      "Report email is not armed on this deployment " +
        "(PROJECT_REPORT_EMAIL_ENABLED). Arming it is an owner decision.",
    );
  }
  const { subject, text, html } = reportEmail(opts.rendered);
  await send({
    to: opts.to,
    from: emailOtpFrom(opts.env),
    subject,
    text,
    html,
  });
}
