"use client";

/**
 * WS-27bk §9.12.8 — reports, rendered IN THE APP.
 *
 * §9.12.8's order is *"render first, deliver second"*, and the reason it gives
 * for the render is not convenience: *"it is useful alone, and it is how the
 * content gets reviewed before anything sends."* Nobody should be able to
 * schedule a weekly email to their colleagues without first seeing, on screen,
 * exactly what that email will say.
 *
 * ⚠️ **Written in the same PR as nothing else, because wave 4 taught the
 * lesson twice.** `/analytics/stuck`, `/analytics/load` and
 * `/analytics/throughput` all shipped with tests and no surface, and the gap
 * was invisible until somebody went looking. An endpoint with no consumer is
 * not half a feature — it is a feature nobody has.
 *
 * ⚠️ **Nothing here computes.** Every number arrives from
 * `GET /projects/reports/{id}/render`, which re-runs §9.12.7's own SQL. This
 * file chooses words and order. The rule is §9.12.8's own: a report with its
 * own arithmetic is the second set of numbers, and two sets disagree.
 */
import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import { statusAccent } from "@/lib/statusAccent";

import {
  type FinishedReport,
  type ReportRow,
  type RenderedReportBody,
  projectsApi,
} from "../lib/api";

/** Hours as a person reads them. Mirrors `AnalyticsPanels`, deliberately. */
function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "not measured";
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/**
 * "7 – 13 Sep 2026".
 *
 * ⚠️ Parsed by hand, never through `new Date()`. These are floating calendar
 * dates, and `new Date("2026-01-01")` is midnight UTC — a day west of
 * Greenwich, which would name the wrong week on the reader's screen.
 */
function periodLabel(from: string, to: string): string {
  const [fy, fm, fd] = from.split("-");
  const [ty, tm, td] = to.split("-");
  const tail = `${MONTHS[Number(tm) - 1]} ${ty}`;
  if (fy === ty && fm === tm) return `${Number(fd)} – ${Number(td)} ${tail}`;
  if (fy === ty) {
    return `${Number(fd)} ${MONTHS[Number(fm) - 1]} – ${Number(td)} ${tail}`;
  }
  return `${Number(fd)} ${MONTHS[Number(fm) - 1]} ${fy} – ${Number(td)} ${tail}`;
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-border pt-3">
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      {children}
    </section>
  );
}

/** One project line: a name, a count, and the cancellations beside it. */
function Row({
  name,
  value,
  aside,
  tone,
  title,
}: {
  name: string;
  value: number;
  aside?: string;
  tone?: string;
  title: string;
}) {
  return (
    <li className="flex items-baseline gap-2 text-[11px]" title={title}>
      <span className="min-w-0 truncate pr-px">{name}</span>
      <span className={`ml-auto font-medium tabular-nums ${tone ?? ""}`}>
        {value}
      </span>
      {aside && (
        <span className="tabular-nums text-muted-foreground">{aside}</span>
      )}
    </li>
  );
}

/**
 * The rendered body — what the email will say, on screen first.
 *
 * Exported so it can be rendered against fixture data without a session. The
 * pane around it needs the API; this part is pure, and the part worth looking
 * at before a report is scheduled to anybody.
 */
export function RenderedBody({ body }: { body: RenderedReportBody }) {
  const done = statusAccent({ category: "done" });
  const late = statusAccent({ category: "cancelled" });
  const { sections } = body;

  return (
    // ⚠️ A readable MEASURE, not the panel's full width. Photographed
    // 2026-09-17: at 1440 the rows ran the whole pane, so "Mobile App" sat
    // about 1200px from its own number and the eye could not join them. A
    // report body is a document, and a document has a column width.
    <div className="max-w-2xl space-y-3">
      <header>
        <h3 className="text-sm font-semibold">{body.report.name}</h3>
        <p className="text-[11px] text-muted-foreground">
          {periodLabel(body.period_start, body.period_end)} ·{" "}
          {body.report.scope === "portfolio"
            ? "Every space you can see"
            : "This project"}
        </p>
      </header>

      {sections.finished && (
        <Section title="What we finished">
          <p
            className={`mb-1 text-lg font-semibold tabular-nums ${done.text}`}
            title={`${sections.finished.total_completed} tasks reached a done status in this period`}
          >
            {sections.finished.total_completed}
          </p>
          <ul className="space-y-0.5">
            {sections.finished.projects.slice(0, 8).map((p) => (
              <Row
                key={p.project_id}
                name={p.name}
                value={p.completed}
                aside={p.cancelled ? `−${p.cancelled}` : undefined}
                title={
                  `${p.completed} finished in ${p.name}` +
                  (p.cancelled ? `, ${p.cancelled} cancelled` : "")
                }
              />
            ))}
          </ul>
          {sections.finished.total_cancelled > 0 && (
            <p
              className="mt-1 text-[11px] text-muted-foreground"
              // ⚠️ Beside the finished count, never added to it. A team that
              // cancelled nine did not finish nine.
              title="Cancellations are never counted as finished work."
            >
              {sections.finished.total_cancelled} cancelled
            </p>
          )}
        </Section>
      )}

      {sections.throughput && (
        <Section title="How long it took">
          <p
            className="text-[11px]"
            title={
              sections.throughput.median_hours === null
                ? "No task in this period recorded both a start and a finish."
                : `Half of the ${sections.throughput.measured} measured tasks took less than this, from first In progress until done.`
            }
          >
            Median{" "}
            <strong className="tabular-nums">
              {duration(sections.throughput.median_hours)}
            </strong>{" "}
            <span className="text-muted-foreground">
              over {sections.throughput.measured} measured
            </span>
          </p>
        </Section>
      )}

      {sections.stuck && sections.stuck.overdue_total > 0 && (
        <Section title="Overdue">
          <p
            className={`mb-1 text-lg font-semibold tabular-nums ${late.text}`}
            title={`${sections.stuck.overdue_total} open tasks are past their due date`}
          >
            {sections.stuck.overdue_total}
          </p>
          <ul className="space-y-0.5">
            {sections.stuck.overdue.slice(0, 8).map((o) => (
              <Row
                key={o.project_id}
                name={o.name}
                value={o.overdue}
                tone={late.text}
                title={`${o.overdue} open tasks in ${o.name} are past their due date`}
              />
            ))}
          </ul>
        </Section>
      )}

      {sections.load && (
        <Section title="Open work">
          <p
            className="mb-1 text-[11px] text-muted-foreground"
            // ⚠️ The rows sum past this. A task with two assignees sits on
            // both plates, so the total is counted over tasks.
            title="Counted over tasks. A task assigned to two people appears in both rows, so the rows add up to more than this."
          >
            {sections.load.total_tasks} open
          </p>
          <ul className="space-y-0.5">
            {sections.load.people.slice(0, 8).map((p) => (
              <Row
                key={p.assignee ?? "__unassigned"}
                name={p.assignee ?? "Unassigned"}
                value={p.open_tasks}
                aside={p.overdue ? `${p.overdue} late` : undefined}
                title={
                  `${p.open_tasks} open for ${p.assignee ?? "nobody"}` +
                  (p.overdue ? `, ${p.overdue} overdue` : "")
                }
              />
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

export default function ReportsView({
  finished,
}: {
  /** The portfolio's finished roll-up, used to offer a first report. */
  finished: FinishedReport | null;
}) {
  const [rows, setRows] = useState<ReportRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [body, setBody] = useState<RenderedReportBody | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let off = false;
    projectsApi.reports().then(
      (r) => !off && setRows(r.reports),
      () => !off && setRows([])
    );
    return () => {
      off = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) {
      setBody(null);
      return;
    }
    let off = false;
    setBody(null);
    setError(null);
    projectsApi.renderReport(selected).then(
      (r) => !off && setBody(r),
      (e) => !off && setError(e?.message ?? "That report could not be rendered.")
    );
    return () => {
      off = true;
    };
  }, [selected]);

  async function createWeekly() {
    setBusy(true);
    setError(null);
    try {
      const made = await projectsApi.createReport({
        name: "Weekly delivery",
        // ⚠️ No config: the server's defaults ARE the report shape — one whole
        // week, ending last Sunday. Sending a config from here would be a
        // second place for that decision to live.
      });
      setRows((prev) => [made, ...(prev ?? [])]);
      setSelected(made.id);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "That report could not be saved."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="mb-3 flex items-baseline gap-2">
        <h2 className="text-sm font-semibold">Reports</h2>
        <p className="text-[11px] text-muted-foreground">
          A saved question, answered from the same numbers the dashboard shows.
        </p>
        <button
          type="button"
          onClick={createWeekly}
          disabled={busy}
          className="ml-auto rounded-md border border-border px-2 py-1 text-[11px] hover:bg-card disabled:opacity-50"
        >
          {busy ? "Saving…" : "New weekly report"}
        </button>
      </div>

      {error && (
        <p className="mb-3 text-[11px] text-destructive" role="alert">
          {error}
        </p>
      )}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
        <aside className="rounded-lg border border-border bg-card p-2">
          {rows === null ? (
            <p className="text-[11px] text-muted-foreground">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              No reports yet.{" "}
              {finished
                ? `There are ${finished.total_completed} finished tasks to report on.`
                : ""}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {rows.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(r.id)}
                    className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[11px] hover:bg-muted ${
                      selected === r.id ? "bg-muted font-medium" : ""
                    }`}
                  >
                    <Icon name="FileText" className="h-3 w-3 shrink-0" />
                    <span className="min-w-0 truncate pr-px">{r.name}</span>
                    <span
                      className="ml-auto shrink-0 text-muted-foreground"
                      title={
                        r.scope === "portfolio"
                          ? "Every space you can see"
                          : "One project and what is under it"
                      }
                    >
                      {r.scope === "portfolio" ? "All" : "Node"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="rounded-lg border border-border bg-card p-3">
          {!selected ? (
            <p className="text-[11px] text-muted-foreground">
              Choose a report to see exactly what it says.
            </p>
          ) : body === null && !error ? (
            <p className="text-[11px] text-muted-foreground">Rendering…</p>
          ) : body ? (
            <RenderedBody body={body} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
