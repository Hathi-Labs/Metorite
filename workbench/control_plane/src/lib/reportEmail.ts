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
    throughput?: {
      median_hours: number | null;
      measured: number;
      /** The weekly counts. Each week draws one text bar (WS-27bn R2b). */
      series?: { week_start: string; completed: number }[];
    };
    /**
     * WS-27bn R3a. Opt-in. The outlook route's body for the report's scope.
     * Every date is null when the verdict carries no forecast.
     */
    outlook?: {
      velocity?: {
        verdict?: string;
        finish_date?: string | null;
        remaining_tasks?: number;
        finished_per_week?: number;
        created_per_week?: number;
        weeks_sampled?: number;
      };
      plan?: {
        planned_finish?: string | null;
        dated?: number;
        tasks?: number;
        slip_days?: number | null;
      };
    };
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
        /** WS-27bn R2b: the text bar, "committed of working h". */
        committed_hours_horizon?: number;
        working_hours_horizon?: number | null;
      }[];
      total_tasks: number;
      hr_visible: boolean;
      horizon_days: number;
    };
    stuck?: {
      overdue: { name: string; overdue: number }[];
      overdue_total: number;
    };
    /**
     * WS-27bn R3c. Opt-in. `hygiene_body`, read now. A task counts in each
     * kind that it breaks, so the counts do not add up to `open_total`.
     */
    hygiene?: {
      open_total: number;
      stale_days?: number;
      by_kind?: Record<string, number | undefined>;
      rows?: { kind: string; title: string; project_name?: string }[];
    };
    /**
     * WS-27bm S7c. Opt-in. The four HR kinds are absent for a reader without
     * `admin:members:read`, so `hr_visible` travels.
     */
    conflicts?: {
      rows: { kind: string; severity: string; sentence: string }[];
      total: number;
      hr_visible: boolean;
      horizon_days: number;
    };
    /**
     * WS-27bn R3b. Opt-in. Without `admin:members:read` the two lists are
     * ABSENT, and the email says so in one line.
     */
    rebalance?: {
      hr_visible: boolean;
      at_risk?: {
        title: string;
        due_on: string | null;
        holder?: { name?: string; email?: string };
        candidates?: { name?: string }[];
      }[];
      at_risk_total?: number;
      pickups?: { name: string; tasks?: { title?: string }[] }[];
      pickups_total?: number;
      idle_total?: number;
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

/**
 * WS-27bn R3a. A date from the outlook, which may be a full timestamp.
 * `day` splits on "-", so it reads the calendar date only.
 */
function dateOnly(iso: string): string {
  return day(iso.slice(0, 10));
}

/** The outlook's verdict, in words. The server decides it. */
const OUTLOOK_VERDICT: Record<string, string> = {
  converging: "converging",
  not_converging: "not converging, because work arrives as fast as it finishes",
  no_history: "too early to forecast",
  nothing_left: "nothing open",
};

/**
 * The hygiene kinds, in the server's order, with the panel's words.
 * `app/projects/lib/hygiene.test.ts` holds them equal to `HYGIENE_KINDS`.
 */
export const HYGIENE_WORDS: readonly [string, string][] = [
  ["no_assignee", "No assignee"],
  ["no_due_date", "No due date"],
  ["no_estimate", "No estimate"],
  ["stale_in_progress", "Stale in progress"],
];

/** How many project lines a message carries before it stops being readable. */
export const MAX_EMAIL_ROWS = 10;

/** How many cells a text bar has. */
export const TEXT_BAR_WIDTH = 10;

/**
 * WS-27bn R2b — a bar an email can carry: full blocks (U+2588) for `value`,
 * light shade (U+2591) for the rest of `of`.
 *
 * ⚠️ **It draws two server figures and computes neither.** The only
 * arithmetic is the cell count, the email's version of a bar width. The
 * figures print beside the bar, so the bar is never the only carrier: a
 * client that drops the glyphs, or a PDF font without them, still says
 * "4 of 17".
 *
 * A value above `of` fills the bar and stops. A value above zero shows one
 * cell at least, because an empty bar reads as nothing. With `of` at zero
 * or below, the bar is all light shade.
 */
export function textBar(value: number, of: number, width = TEXT_BAR_WIDTH): string {
  if (!Number.isFinite(value) || !Number.isFinite(of) || of <= 0 || value <= 0) {
    return "\u2591".repeat(width);
  }
  const cells = Math.min(width, Math.max(1, Math.round((value / of) * width)));
  return "\u2588".repeat(cells) + "\u2591".repeat(width - cells);
}

/** A person-readable figure: "12.5", never "12.500000001". */
function figure(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(1)));
}

/**
 * One section of a report, as words. Built ONCE by {@link reportLayout} and
 * drawn three ways: the email's text and HTML ({@link reportEmail}) and the
 * downloaded Markdown and PDF ({@link reportDocument}).
 *
 * ⚠️ **This is the one formatter (WS-27bm S8).** The Reports download and the
 * chat's report card write their files from this layout, so a file and an
 * email of one render cannot disagree. The gateway's PDF route lays out the
 * HTML it is given and formats nothing.
 */
export interface ReportPart {
  /** The headline. `lead` is bold when `strong`, and `extra` follows it. */
  head: { lead: string; strong?: boolean; extra?: string };
  /** One line per row. Member strings are raw here and escaped per renderer. */
  items: string[];
  /** Lines after the rows: a cut list admitted, an HR note. */
  notes: string[];
}

export interface ReportLayout {
  title: string;
  period: string;
  scope: string;
  parts: ReportPart[];
}

/**
 * The words of one rendered report, in order. It formats and never computes:
 * every number is the render route's.
 *
 * `maxRows` caps each list. An email carries {@link MAX_EMAIL_ROWS}. A
 * download passes `Infinity`, because a file is the whole of the report.
 */
export function reportLayout(
  rendered: RenderedReport,
  maxRows: number = MAX_EMAIL_ROWS,
): ReportLayout {
  const { report, sections } = rendered;
  const parts: ReportPart[] = [];

  const fin = sections.finished;
  if (fin) {
    const rows = fin.projects.slice(0, maxRows);
    const notes: string[] = [];
    // ⚠️ Said out loud when the list is cut. A truncated list that does not
    // admit it reads as the whole of the work.
    if (rows.length && fin.projects.length > rows.length) {
      notes.push(`…and ${fin.projects.length - rows.length} more projects`);
    }
    parts.push({
      head: {
        lead: `Finished: ${fin.total_completed}`,
        strong: true,
        extra: fin.total_cancelled ? `${fin.total_cancelled} cancelled` : undefined,
      },
      items: rows.map(
        (p) =>
          `${p.name}: ${p.completed}` +
          (p.cancelled ? ` (${p.cancelled} cancelled)` : "") +
          ` · ${textBar(p.completed, fin.total_completed)}` +
          ` ${p.completed} of ${fin.total_completed}`,
      ),
      notes,
    });
  }

  const thr = sections.throughput;
  if (thr) {
    // The denominator travels with the median. A median over four tasks and a
    // median over four hundred are not the same claim.
    parts.push({
      head: {
        lead:
          `Median time to finish: ${duration(thr.median_hours)}` +
          ` (over ${thr.measured} measured)`,
      },
      // One bar a week, scaled to the busiest week, with no "of": a week's
      // count is not a share of anything.
      items: (() => {
        const weeks = thr.series ?? [];
        const peak = Math.max(0, ...weeks.map((w) => w.completed));
        return weeks.map(
          (w) =>
            `Week of ${day(w.week_start)}: ${textBar(w.completed, peak)} ${w.completed}`,
        );
      })(),
      notes: [],
    });
  }

  const out = sections.outlook;
  if (out) {
    // ⚠️ Words and the server's figures only. No date is computed here, and
    // a verdict with no forecast date prints no date line at all.
    const v = out.velocity ?? {};
    const p = out.plan ?? {};
    const items: string[] = [];
    if (p.planned_finish) {
      items.push(
        `Planned finish: ${dateOnly(p.planned_finish)}` +
          (typeof p.dated === "number" && typeof p.tasks === "number"
            ? ` (${p.dated} of ${p.tasks} open tasks carry a due date)`
            : ""),
      );
    }
    if (v.finish_date) items.push(`Forecast finish: ${dateOnly(v.finish_date)}`);
    if (typeof p.slip_days === "number") {
      const n = Math.abs(p.slip_days);
      items.push(
        p.slip_days === 0
          ? "On the plan date"
          : `${n} ${n === 1 ? "day" : "days"} ${p.slip_days > 0 ? "late" : "early"}`,
      );
    }
    if (
      typeof v.finished_per_week === "number" &&
      typeof v.created_per_week === "number"
    ) {
      items.push(
        `Finishing ${figure(v.finished_per_week)} a week, adding` +
          ` ${figure(v.created_per_week)} a week` +
          (typeof v.weeks_sampled === "number"
            ? `, over ${v.weeks_sampled} weeks`
            : ""),
      );
    }
    parts.push({
      head: {
        lead: `Outlook: ${OUTLOOK_VERDICT[v.verdict ?? ""] ?? "no forecast"}`,
        strong: true,
        extra:
          typeof v.remaining_tasks === "number"
            ? `${v.remaining_tasks} open`
            : undefined,
      },
      items,
      notes: [],
    });
  }

  const stuck = sections.stuck;
  if (stuck && stuck.overdue_total > 0) {
    parts.push({
      head: { lead: `Overdue: ${stuck.overdue_total}`, strong: true },
      items: stuck.overdue
        .slice(0, maxRows)
        .map(
          (o) =>
            `${o.name}: ${o.overdue} · ${textBar(o.overdue, stuck.overdue_total)}` +
            ` ${o.overdue} of ${stuck.overdue_total}`,
        ),
      notes: [],
    });
  }

  const load = sections.load;
  if (load) {
    parts.push({
      head: { lead: `Open work: ${load.total_tasks}` },
      items: load.people
        .slice(0, maxRows)
        // ⚠️ The bar is "overdue of open_tasks", never "of total_tasks". The
        // rows per person add up to more than the total, because a task with
        // two assignees is on both plates.
        .map(
          (p) =>
            `${p.assignee ?? "Unassigned"}: ${p.open_tasks}` +
            (p.overdue ? ` (${p.overdue} overdue)` : "") +
            ` · ${textBar(p.overdue, p.open_tasks)}` +
            ` ${p.overdue} of ${p.open_tasks} overdue`,
        ),
      notes: [],
    });
  }

  const cap = sections.capacity;
  if (cap) {
    parts.push({
      head: {
        lead: `Who has the hours (next ${cap.horizon_days} days): ${cap.total_tasks} open`,
      },
      items: cap.people.slice(0, maxRows).map((p) => {
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
        // The bar only with the HR half and `hours_basis`, as on screen. The
        // text states both figures, because a bar clips above 100 percent.
        const barPart =
          p.hours_basis &&
          typeof p.committed_hours_horizon === "number" &&
          typeof p.working_hours_horizon === "number"
            ? ` · ${textBar(p.committed_hours_horizon, p.working_hours_horizon)}` +
              ` ${figure(p.committed_hours_horizon)} of ${figure(p.working_hours_horizon)} h committed`
            : "";
        return `${who}: ${p.open_tasks} open${hoursPart}${barPart}`;
      }),
      notes: cap.hr_visible ? [] : ["Hours need HR read access."],
    });
  }

  const hyg = sections.hygiene;
  if (hyg) {
    // One text bar a kind, "n of open_total", then its rows up to `maxRows`.
    // The counts are the server's, and each bar draws two of them.
    const items: string[] = [];
    const notes: string[] = [
      "A task can miss more than one thing, so the counts do not add up to the open total.",
    ];
    for (const [kind, label] of HYGIENE_WORDS) {
      const n = hyg.by_kind?.[kind];
      if (typeof n !== "number") continue;
      items.push(
        `${label}: ${n} · ${textBar(n, hyg.open_total)} ${n} of ${hyg.open_total}`,
      );
      const rows = (hyg.rows ?? []).filter((r) => r.kind === kind);
      const shown = rows.slice(0, maxRows);
      for (const r of shown) {
        items.push(`  ${r.title}${r.project_name ? ` · ${r.project_name}` : ""}`);
      }
      if (n > shown.length && shown.length > 0) {
        items.push(`  …and ${n - shown.length} more`);
      }
    }
    parts.push({
      head: {
        lead: `Data hygiene: ${hyg.open_total} open`,
        strong: true,
        extra:
          typeof hyg.stale_days === "number"
            ? `stale after ${hyg.stale_days} days`
            : undefined,
      },
      items,
      notes,
    });
  }

  const conf = sections.conflicts;
  if (conf) {
    // ⚠️ The sentence is the server's, verbatim, and it carries task titles
    // a member typed. Every renderer escapes it like any other member string.
    parts.push({
      head: { lead: `Where the plan conflicts: ${conf.total}` },
      items: conf.rows
        .slice(0, maxRows)
        .map((r) => `${r.severity === "high" ? "High" : "Medium"}: ${r.sentence}`),
      notes: conf.hr_visible ? [] : ["Four kinds need HR read access."],
    });
  }

  const reb = sections.rebalance;
  if (reb && reb.hr_visible === false) {
    // ⚠️ The title and one line, and no rows. A helper names who holds which
    // skill, and a zero here would read as "nobody is at risk". H-186 item 3:
    // the part keeps its title, as every other part does.
    parts.push({
      head: { lead: "Who could help", strong: true },
      items: [],
      notes: ["Rebalancing needs HR read access. An admin can see it."],
    });
  } else if (reb) {
    // Words and the server's names only. No colour and no bar: a helper is
    // not a share of anything.
    const tasks = (reb.at_risk ?? []).slice(0, maxRows);
    const people = (reb.pickups ?? []).slice(0, maxRows);
    const notes: string[] = [];
    if (typeof reb.at_risk_total === "number" && reb.at_risk_total > tasks.length) {
      notes.push(`…and ${reb.at_risk_total - tasks.length} more tasks at risk`);
    }
    if (typeof reb.pickups_total === "number" && reb.pickups_total > people.length) {
      notes.push(`…and ${reb.pickups_total - people.length} more people who could take work`);
    }
    parts.push({
      head: {
        lead: `Who could help: ${reb.at_risk_total ?? tasks.length} at risk`,
        strong: true,
        extra:
          typeof reb.idle_total === "number" ? `${reb.idle_total} idle` : undefined,
      },
      items: [
        ...tasks.map((t) => {
          const helpers = (t.candidates ?? [])
            .slice(0, 3)
            .map((c) => c.name)
            .filter(Boolean)
            .join(", ");
          return (
            `${t.title} — held by ${t.holder?.name || t.holder?.email || "somebody"}` +
            `, due ${t.due_on ? day(t.due_on.slice(0, 10)) : "with no date"}` +
            `: helpers ${helpers || "none"}`
          );
        }),
        ...people.map(
          (p) =>
            `${p.name} could take: ` +
            (p.tasks ?? []).map((t) => t.title).filter(Boolean).join(", "),
        ),
      ],
      notes,
    });
  }

  return {
    title: report.name,
    period: periodLabel(rendered.period_start, rendered.period_end),
    scope:
      report.scope === "portfolio" ? "Every space you can see" : "This project",
    parts,
  };
}

function textOf(layout: ReportLayout): string {
  const lines: string[] = [layout.title, layout.period, layout.scope, ""];
  for (const part of layout.parts) {
    lines.push(
      part.head.lead + (part.head.extra ? ` (${part.head.extra})` : ""),
    );
    for (const item of part.items) lines.push(`  ${item}`);
    for (const note of part.notes) lines.push(`  ${note}`);
    lines.push("");
  }
  return lines.join("\n").trimEnd();
}

function htmlOf(layout: ReportLayout): string {
  const blocks: string[] = [
    `<h2>${escapeHtml(layout.title)}</h2>`,
    `<p>${escapeHtml(layout.period)} · ${escapeHtml(layout.scope)}</p>`,
  ];
  for (const part of layout.parts) {
    const lead = escapeHtml(part.head.lead);
    blocks.push(
      `<p>${part.head.strong ? `<strong>${lead}</strong>` : lead}` +
        (part.head.extra ? ` · ${escapeHtml(part.head.extra)}` : "") +
        `</p>`,
    );
    if (part.items.length) {
      blocks.push(
        `<ul>${part.items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`,
      );
    }
    for (const note of part.notes) blocks.push(`<p>${escapeHtml(note)}</p>`);
  }
  // ⚠️ No style attribute, no colour, no table. See the module header.
  return `<div>${blocks.join("")}</div>`;
}

/**
 * A member string made inert in Markdown. A project named `*Q3*` must print
 * its asterisks, not turn italic, and a `[link](…)` in a title must not become
 * a link in the file.
 */
export function escapeMarkdown(value: string): string {
  return value.replace(/([\\`*_[\]<>#|])/g, "\\$1");
}

function markdownOf(layout: ReportLayout): string {
  const out: string[] = [
    `# ${escapeMarkdown(layout.title)}`,
    "",
    `${escapeMarkdown(layout.period)} · ${escapeMarkdown(layout.scope)}`,
  ];
  for (const part of layout.parts) {
    const lead = escapeMarkdown(part.head.lead);
    out.push(
      "",
      (part.head.strong ? `**${lead}**` : lead) +
        (part.head.extra ? ` · ${escapeMarkdown(part.head.extra)}` : ""),
    );
    if (part.items.length) {
      out.push("", ...part.items.map((i) => `- ${escapeMarkdown(i)}`));
    }
    for (const note of part.notes) out.push("", escapeMarkdown(note));
  }
  return `${out.join("\n")}\n`;
}

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
  const layout = reportLayout(rendered, MAX_EMAIL_ROWS);
  return {
    subject: `${layout.title} — ${layout.period}`,
    text: textOf(layout),
    html: htmlOf(layout),
  };
}

/** A file name no file system refuses: no slash, colon, quote or control. */
function fileSafe(value: string): string {
  const cleaned = value.replace(/[\\/:*?"<>|\x00-\x1f]+/g, "-").trim();
  return cleaned.slice(0, 120) || "report";
}

/**
 * One rendered report as a FILE (WS-27bm S8, spec `projects_ai_chat.md` §14):
 * the Markdown a member downloads, the HTML the gateway lays out as a PDF, and
 * the base name both files share.
 *
 * The same layout as the email, with every row: a file is the whole report,
 * and a cut list in a file is one nobody admits to.
 */
export function reportDocument(rendered: RenderedReport): {
  basename: string;
  markdown: string;
  html: string;
} {
  const layout = reportLayout(rendered, Infinity);
  return {
    basename: fileSafe(
      `${layout.title} ${rendered.period_start} to ${rendered.period_end}`,
    ),
    markdown: markdownOf(layout),
    html: htmlOf(layout),
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
