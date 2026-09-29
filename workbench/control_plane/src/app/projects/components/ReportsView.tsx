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
 *
 * WS-27bn R1 (`projects_reports.md` §6.2) adds the builder: a scope, a period,
 * a subtree toggle, the sections and a name, with a live preview from
 * `POST /projects/reports/preview`. The preview is a server render too. The
 * builder's choices live in `lib/reportBuilder.ts`, as pure functions.
 *
 * WS-27bn R2 (§6.1) adds the home screen: "Your reports" and the template
 * gallery, "Start from a question". The catalogue comes from
 * `GET /projects/reports/templates`, and this file holds no copy of it.
 *
 * WS-27bn R5b-1 (§8 R5b) adds the subject chip, "About: [subject]", beside
 * the scope chip. It reads `GET /projects/reports/subjects` only. A link
 * from `reportLink` opens the builder filled in, and this file removes the
 * link's keys from the address after it reads them once.
 *
 * The Reports UX pass (2026-09-29, `projects_reports.md` §6.5) lays the
 * builder out beside its preview, names the subject and the scope in the
 * rendered header, folds the coming-soon templates, splits the rail into
 * yours and shared, and offers Delete in edit mode.
 */
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { LayoutBoundary } from "@/components/LayoutBoundary";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import Input from "@/components/ui/Input";
import { SelectButton } from "@/components/ui/SelectButton";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { periodLabel } from "@/lib/reportEmail";
import { accentForHue, statusAccent } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  type NodeSummary,
  type PreviewReportBody,
  type ReportRow,
  type ReportTemplate,
  type RenderedReportBody,
  projectsApi,
  projectsKey,
} from "../lib/api";
import { capacityReportRows } from "../lib/capacity";
import { conflictsReportRows } from "../lib/conflicts";
import { deleteReportCopy } from "../lib/deleteCopy";
import {
  HYGIENE_KINDS,
  OVERLAP_NOTE,
  hygieneCount,
  hygieneRows,
  kindTitle,
} from "../lib/hygiene";
import { headlineVerdict, shortDate } from "../lib/outlook";
import { PANEL_HINTS } from "../lib/panelHints";
import {
  focusWhy,
  helpLine,
  pulseFocus,
  pulseName,
  pulseRows,
  statusMark,
} from "../lib/pulse";
import {
  REBALANCE_HR_HINT,
  helpersLine,
  pickupLine,
  rebalancePickups,
  rebalanceTasks,
} from "../lib/rebalance";
import {
  capacityPanelData,
  conflictsPanelData,
  finishedPanelData,
  hygienePanelData,
  loadPanelData,
  outlookPanelData,
  pulsePanelData,
  rebalancePanelData,
  reportTiles,
  stuckPanelData,
  throughputPanelData,
} from "../lib/reportPanels";
import {
  AS_OF_TODAY,
  type BuilderState,
  MAX_REPORT_NAME,
  NEW_REPORT_NAME,
  OVERVIEW_NAME,
  REPORT_LINK_KEYS,
  SAVED_PERIOD,
  SUBJECTS_FAILED,
  WHOLE_ORGANIZATION,
  asOfDay,
  builderName,
  builderStateFrom,
  builderStateFromLink,
  builderStateFromTemplate,
  builderSubject,
  configFor,
  createPayload,
  deleteShown,
  editTitle,
  errorChip,
  hiddenTeamHint,
  homeEmptyLine,
  newBuilderState,
  overviewState,
  overviewTableShown,
  parseReportLink,
  parseSubjectValue,
  patchPayload,
  periodFree,
  periodKey,
  periodOptions,
  projectOnly,
  railGroups,
  reportCardLine,
  reportHeaderLine,
  requiredSubject,
  saveAsReportState,
  saveRefusal,
  scopeChoices,
  scopeOptions,
  scopePhrase,
  scopePrompt,
  sectionBlockedBySubject,
  sectionGroups,
  selfSubject,
  startingTeam,
  subjectChipNote,
  subjectChipShown,
  subjectChipStatus,
  subjectLabel,
  subjectOptions,
  subjectPrompt,
  subjectSectionNote,
  subjectValue,
  templateLabel,
  toggleSection,
  withPeriod,
  withSubject,
  yourReports,
} from "../lib/reportBuilder";
import {
  CapacityPanel,
  ConflictsPanel,
  FinishedPanel,
  HygienePanel,
  LoadPanel,
  OutlookPanel,
  PulsePanel,
  RebalancePanel,
  Stat,
  StuckPanel,
  ThroughputPanel,
} from "./AnalyticsPanels";
import { ReportFileButtons } from "./ReportFileButtons";
import SpaceSummary from "./SpaceSummary";

/** Hours as a person reads them. Mirrors `AnalyticsPanels`, deliberately. */
function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "not measured";
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

/**
 * A section's table, folded under its panel (WS-27bn R2b).
 *
 * The picture comes first and the figures second. `keepMounted` keeps the
 * folded table in the page, so find-in-page reaches it and a render test
 * can see that the panel and the table agree.
 *
 * ⚠️ `title` is the section's name in the report's own words, which the
 * chat's report card borrows (`skill_projects/views.py`
 * `REPORT_CARD_SECTIONS`, fenced by `test_projects_agent.py`). The panel
 * above carries the heading, so here the words name the disclosure only.
 */
function Table({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <CollapsibleSection
      label={`${title}, as a table`}
      count={count}
      defaultOpen={false}
      keepMounted
    >
      {children}
    </CollapsibleSection>
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
    <li className="flex items-baseline gap-2 text-xs" title={title}>
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
 *
 * WS-27bn R2b (`projects_reports.md` §8): four summary tiles, then each
 * section as its Analytics panel, then its table folded under it. The panel
 * is the SAME component the Analytics app draws, fed through
 * `lib/reportPanels.ts`. The panel carries the heading, so the section has
 * no heading of its own. Each `sections.<name>` access stays literal,
 * because `test_projects_report_sections_lockstep.py` reads them.
 *
 * ⚠️ WS-27bn R5f rule 15. **Each panel is the only child of its own
 * `LayoutBoundary`.** The Analytics app had this rule (WS-27bm S12), and
 * Overview is now where those panels live. The reports branch of `page.tsx`
 * sits outside every other boundary, so one panel that throws on a missing
 * field would blank the whole page. `layoutBoundary.test.ts` holds each tag
 * to this shape.
 */
export function RenderedBody({
  body,
  headerLine,
  hiddenHint,
  lead,
}: {
  body: RenderedReportBody | PreviewReportBody;
  /**
   * The line under the title (§6.5 item 13): "About [subject] ·
   * [scope] · [period]", from `reportHeaderLine`. The caller knows the
   * names. Without it the line names the scope word and the period.
   */
  headerLine?: string;
  /** §6.5 item 16. What the hidden-people line adds, in the builder only. */
  hiddenHint?: string | null;
  /**
   * WS-27bn R5f rule 6. What Overview draws above the sections: the KPI
   * strip and the space table (`SpaceSummary`). A saved report has none.
   */
  lead?: React.ReactNode;
}) {
  const done = statusAccent({ category: "done" });
  const late = statusAccent({ category: "cancelled" });
  const { sections } = body;
  const tiles = reportTiles(sections);
  const tone = { done: done.text, late: late.text };

  return (
    // ⚠️ A readable MEASURE, not the panel's full width. Photographed
    // 2026-09-17: at 1440 the rows ran the whole pane, so "Mobile App" sat
    // about 1200px from its own number and the eye could not join them. A
    // report body is a document, and a document has a column width.
    <div className="max-w-2xl space-y-3">
      <header>
        <h3 className="text-sm font-semibold">{body.report.name}</h3>
        <p className="text-xs text-muted-foreground">
          {headerLine ??
            `${body.report.scope === "portfolio" ? "Whole organization" : "This project"} · ${periodLabel(body.period_start, body.period_end)}`}
        </p>
      </header>

      {tiles.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {tiles.map((t) => (
            <Stat
              key={t.key}
              label={t.label}
              value={t.value}
              display={t.key === "median" ? duration(t.hours) : undefined}
              tone={t.tone ? tone[t.tone] : undefined}
              title={t.title}
            />
          ))}
        </div>
      )}

      {lead}

      {sections.finished && (
        <div className="space-y-1">
          <LayoutBoundary layout="finished work">
            <FinishedPanel data={finishedPanelData(sections.finished, body)} />
          </LayoutBoundary>
          <Table title="What we finished" count={sections.finished.projects.length}>
            <p
              className={`mb-1 text-xs font-medium tabular-nums ${done.text}`}
              title={`${sections.finished.total_completed} tasks reached a done status in this period`}
            >
              {sections.finished.total_completed} finished
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
                className="mt-1 text-xs text-muted-foreground"
                // ⚠️ Beside the finished count, never added to it. A team that
                // cancelled nine did not finish nine.
                title="Cancellations are never counted as finished work."
              >
                {sections.finished.total_cancelled} cancelled
              </p>
            )}
          </Table>
        </div>
      )}

      {sections.throughput && (
        <div className="space-y-1">
          <LayoutBoundary layout="throughput">
            <ThroughputPanel
              data={throughputPanelData(sections.throughput, body)}
            />
          </LayoutBoundary>
          <Table title="How long it took" count={sections.throughput.series.length}>
            <p
              className="text-xs"
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
            <ul className="mt-1 space-y-0.5">
              {sections.throughput.series.map((w) => (
                <Row
                  key={w.week_start}
                  name={`Week of ${w.week_start}`}
                  value={w.completed}
                  title={`${w.completed} finished in the week of ${w.week_start}`}
                />
              ))}
            </ul>
          </Table>
        </div>
      )}

      {/* WS-27bn R3a. Opt-in. The outlook route's own body, for this scope.
          The table says each figure the panel draws, in words. */}
      {sections.outlook && (
        <div className="space-y-1">
          <LayoutBoundary layout="forecast">
            <OutlookPanel data={outlookPanelData(sections.outlook)} />
          </LayoutBoundary>
          <Table title="Forecast">
            <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
              <dt className="text-muted-foreground">Forecast</dt>
              <dd className="font-medium">
                {headlineVerdict(sections.outlook).headline}
              </dd>
              <dt className="text-muted-foreground">Planned finish</dt>
              <dd className="tabular-nums">
                {shortDate(sections.outlook.plan?.planned_finish)}
              </dd>
              <dt className="text-muted-foreground">Forecast finish</dt>
              <dd className="tabular-nums">
                {shortDate(sections.outlook.velocity?.finish_date)}
              </dd>
              <dt className="text-muted-foreground">Open tasks</dt>
              <dd
                className="tabular-nums"
                title="Open tasks in this scope that the forecast must clear."
              >
                {sections.outlook.velocity?.remaining_tasks ?? "—"}
              </dd>
            </dl>
          </Table>
        </div>
      )}

      {sections.stuck && (
        <div className="space-y-1">
          <LayoutBoundary layout="stuck work">
            <StuckPanel data={stuckPanelData(sections.stuck, body)} />
          </LayoutBoundary>
          <Table title="Overdue" count={sections.stuck.overdue.length}>
            <p
              className={`mb-1 text-xs font-medium tabular-nums ${late.text}`}
              title={`${sections.stuck.overdue_total} open tasks are past their due date`}
            >
              {sections.stuck.overdue_total} overdue
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
          </Table>
        </div>
      )}

      {sections.load && (
        <div className="space-y-1">
          <LayoutBoundary layout="load">
            <LoadPanel data={loadPanelData(sections.load, body)} />
          </LayoutBoundary>
          <Table title="Open work" count={sections.load.people.length}>
            <p
              className="mb-1 text-xs text-muted-foreground"
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
          </Table>
        </div>
      )}

      {/* WS-27bm S7a. Opt-in: only a report that asked for `capacity` has it.
          The rows and the hours are the capacity route's, verbatim. */}
      {sections.capacity && (
        <div className="space-y-1">
          <LayoutBoundary layout="capacity">
            <CapacityPanel data={capacityPanelData(sections.capacity, body)} />
          </LayoutBoundary>
          <Table title="Who has the hours" count={sections.capacity.people.length}>
            <p
              className="mb-1 text-xs text-muted-foreground"
              title={`Spare hours cover the next ${sections.capacity.horizon_days} days, across all the work the reader can see.`}
            >
              {sections.capacity.total_tasks} open · next{" "}
              {sections.capacity.horizon_days} days
            </p>
            <ul className="space-y-0.5">
              {capacityReportRows(sections.capacity.people).map((p) => (
                <Row
                  key={p.key}
                  name={p.name}
                  value={p.open}
                  aside={p.aside}
                  title={p.title}
                />
              ))}
            </ul>
          </Table>
        </div>
      )}

      {/* WS-27bn R3d. Opt-in. `pulse_body`, read today and not over the
          period. The server removed the cards this reader may not see, and
          the table names each card and each focus task it sent. */}
      {sections.pulse && (
        <div className="space-y-1">
          <LayoutBoundary layout="team pulse">
            <PulsePanel data={pulsePanelData(sections.pulse)} hiddenHint={hiddenHint} />
          </LayoutBoundary>
          <Table title="Team pulse" count={pulseRows(sections.pulse).length}>
            <p
              className="mb-1 text-xs text-muted-foreground"
              title="Every person who holds open work in this scope, before the report hides any card."
            >
              {sections.pulse.people_total} people
            </p>
            <div className="space-y-1.5">
              {pulseRows(sections.pulse).map((r) => (
                <div key={r.assignee} className="text-xs">
                  <ul>
                    <Row
                      name={pulseName(r)}
                      value={r.open_tasks}
                      aside={statusMark(r)?.label}
                      title={
                        `${r.open_tasks} open, ${r.overdue} overdue,` +
                        ` ${r.blocked_count} blocked, ${r.stale_count} stale,` +
                        ` ${r.focus_total} focus tasks` +
                        (typeof r.waiting_count === "number"
                          ? `, ${r.waiting_count} waiting past the date`
                          : "")
                      }
                    />
                  </ul>
                  <ul className="mt-0.5 space-y-0.5 pl-2">
                    {pulseFocus(r).map((f) => (
                      <li
                        key={f.id}
                        className="truncate pr-px text-muted-foreground"
                        title={f.title}
                      >
                        <span className="text-foreground">{f.title}</span> ·{" "}
                        {focusWhy(f)}
                        {f.project_name ? ` · ${f.project_name}` : ""}
                      </li>
                    ))}
                  </ul>
                  {helpLine(r) && (
                    <p className="pl-2 text-muted-foreground">{helpLine(r)}</p>
                  )}
                </div>
              ))}
            </div>
          </Table>
        </div>
      )}

      {/* WS-27bn R3c. Opt-in. `hygiene_body`, read now and not over the
          period. The table names each task the server sent, kind by kind. */}
      {sections.hygiene && (
        <div className="space-y-1">
          <LayoutBoundary layout="data hygiene">
            <HygienePanel data={hygienePanelData(sections.hygiene)} />
          </LayoutBoundary>
          <Table title="Data hygiene" count={sections.hygiene.rows.length}>
            <p
              className="mb-1 text-xs text-muted-foreground"
              title={OVERLAP_NOTE}
            >
              {sections.hygiene.open_total} open
            </p>
            <ul className="space-y-1.5">
              {HYGIENE_KINDS.map(({ kind, label, title }) => (
                <li key={kind} className="text-xs">
                  <span
                    className="font-medium text-foreground"
                    title={kindTitle(title, sections.hygiene)}
                  >
                    {label}
                  </span>
                  <span className="text-muted-foreground">
                    {" "}
                    · {hygieneCount(sections.hygiene, kind) ?? "—"} of{" "}
                    {sections.hygiene?.open_total}
                  </span>
                  <ul className="mt-0.5 space-y-0.5">
                    {hygieneRows(sections.hygiene, kind).map((r) => (
                      <li
                        key={r.id}
                        className="truncate pr-px text-muted-foreground"
                        title={r.title}
                      >
                        <span className="text-foreground">{r.title}</span> ·{" "}
                        {r.project_name}
                        {kind === "stale_in_progress" && r.updated_at
                          ? ` · last change ${shortDate(r.updated_at)}`
                          : ""}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </Table>
        </div>
      )}

      {/* WS-27bm S7c. Opt-in: only a report that asked for `conflicts` has
          it. The rows and the sentences are the conflicts route's, verbatim. */}
      {sections.conflicts && (
        <div className="space-y-1">
          <LayoutBoundary layout="conflicts">
            <ConflictsPanel
              data={conflictsPanelData(sections.conflicts, body)}
            />
          </LayoutBoundary>
          <Table title="Where the plan conflicts" count={sections.conflicts.rows.length}>
            <p
              className="mb-1 text-xs text-muted-foreground"
              title={`Counted by the server over every row. Dated kinds read the next ${sections.conflicts.horizon_days} days.`}
            >
              {sections.conflicts.total} conflicts
            </p>
            <ul className="space-y-1">
              {conflictsReportRows(sections.conflicts.rows).map((c) => (
                <li key={c.key} className="text-xs" title={c.sentence}>
                  {/* The dot carries the severity, as on the Analytics panel. */}
                  <span
                    className={`mr-1.5 inline-block size-1.5 rounded-full align-middle ${accentForHue(c.hue).dot}`}
                    aria-hidden
                  />
                  <span className="font-medium text-foreground">{c.label}</span>
                  <span className="text-muted-foreground"> · {c.sentence}</span>
                </li>
              ))}
            </ul>
          </Table>
        </div>
      )}

      {/* WS-27bn R3b. Opt-in, and read only. The rebalance route's own body.
          Without the HR grant it has no lists, and the table says why. */}
      {sections.rebalance && (
        <div className="space-y-1">
          <LayoutBoundary layout="who could help">
            <RebalancePanel data={rebalancePanelData(sections.rebalance)} />
          </LayoutBoundary>
          {/* H-186 item 2. The table lists the idle people too, so they count. */}
          <Table title="Who could help" count={
            rebalanceTasks(sections.rebalance).length +
            rebalancePickups(sections.rebalance).length
          }>
            {sections.rebalance.hr_visible === false ? (
              <p className="text-xs text-muted-foreground">
                {REBALANCE_HR_HINT}
              </p>
            ) : (
              <ul className="space-y-1">
                {rebalanceTasks(sections.rebalance).map((t) => (
                  <li key={t.task_id} className="text-xs" title={t.title}>
                    <span className="font-medium text-foreground">{t.title}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      · held by {t.holder?.name || t.holder?.email} ·{" "}
                      {t.due_on ? `due ${shortDate(t.due_on)}` : "no due date"} ·
                      helpers {helpersLine(t) ?? "none"}
                    </span>
                  </li>
                ))}
                {rebalancePickups(sections.rebalance).map((p) => (
                  <li key={p.email} className="text-xs" title={p.name}>
                    <span className="font-medium text-foreground">{p.name}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      could take: {pickupLine(p)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Table>
        </div>
      )}
    </div>
  );
}

/** How long the builder waits after a change before it asks for a preview. */
const PREVIEW_DELAY_MS = 400;

/** An error as one sentence a member can read. */
function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/**
 * One chip with the sentence that belongs to it. An error from the server
 * shows under the chip that caused it, in plain words.
 */
function ChipSlot({
  children,
  error,
  note,
}: {
  children: React.ReactNode;
  error?: string | null;
  note?: string | null;
}) {
  return (
    <div className="flex w-full flex-col gap-1 sm:w-auto xl:w-full">
      {children}
      {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
      {error ? (
        <p className="text-xs text-destructive sm:max-w-[16rem] xl:max-w-none" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** A chip's width: the full column on a phone and in the builder's column. */
const CHIP_WIDTH = "w-full sm:w-[16rem] xl:w-full";

/**
 * The subject chip when the subjects read failed (§6.5 item 8).
 *
 * The chip keeps its shape: a disabled chip that says so, and the ONE Retry
 * beside it. The preview line says what failed, and has no second Retry.
 */
export function SubjectChipFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex w-full items-center gap-1 sm:w-auto xl:w-full">
      <div className="min-w-0 flex-1">
        <SelectButton
          label="Subject"
          prefix="About:"
          prompt={SUBJECTS_FAILED}
          widthClass={CHIP_WIDTH}
          value=""
          options={[]}
          disabled
          onChange={() => undefined}
        />
      </div>
      <Button variant="text" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

/**
 * The preview's one line when the report cannot show yet: what to choose,
 * with the icon of the chip to choose it in. It never carries a Retry.
 */
export function PreviewPrompt({ icon, text }: { icon: string; text: string }) {
  return (
    <p className="flex items-center gap-2 text-xs text-foreground">
      <Icon name={icon} className="h-3.5 w-3.5 shrink-0 text-primary" />
      {text}
    </p>
  );
}

/**
 * WS-27bn R1 — the builder, one sentence of chips (§6.2).
 *
 * ⚠️ **The preview is the server's.** Each change asks
 * `POST /projects/reports/preview` after a short delay. The browser computes
 * no figure, so the preview and the saved render are one computation.
 *
 * ⚠️ **An edit sends the WHOLE config.** PATCH replaces `config` on the
 * server, so a partial one would reset the fields it left out. Since R5b
 * that includes the subject.
 *
 * ⚠️ **The scope of a saved report is fixed.** PATCH takes a name and a
 * config, and no `project_id`. So the scope chip is off while editing.
 *
 * WS-27bn R5b-1. The sentence reads "[Template] About: [subject] In:
 * [scope] Over: [period]". The subject chip lists what
 * `GET /projects/reports/subjects` answers, and nothing else. "My day" is
 * locked to the reader. "1:1 prep" asks for a person, and the preview says
 * what to do until one is chosen.
 *
 * The UX pass (2026-09-29, §6.5). At `xl` the controls are a sticky column
 * on the left and the preview sits beside them. Below `xl` the two stack.
 * The name follows the chips until the member types one. A report whose
 * sections all ignore the period says "As of today" in place of the period
 * chip. Edit mode names the report and offers Delete to a reader the server
 * lets delete it.
 *
 * WS-27bn R5f (§8 R5f). `mode="reportsOverview"` is Overview, the start of
 * the one Reports app: the builder with no name field and no Save. It shows
 * the KPI strip and the space table above the sections when
 * `overviewTableShown` allows it. "Save as report" hands its choices to
 * `onSaveAs`, and Overview itself writes no row. The mode is not called
 * `overview`, because `page.tsx` uses that word for a project's overview.
 */
export function ReportBuilder({
  initial,
  editing,
  roots,
  templates,
  onSaved,
  onDeleted,
  onCancel,
  mode = "report",
  onSaveAs,
  onPreview,
  onOpenNode,
}: {
  initial: BuilderState;
  /** The saved report under edit, or `null` for a new one. */
  editing: ReportRow | null;
  roots: Parameters<typeof scopeOptions>[0];
  /** The server's catalogue, or `undefined` while it loads. */
  templates: ReportTemplate[] | undefined;
  onSaved: (row: ReportRow) => void;
  onDeleted: (id: string) => void;
  onCancel: () => void;
  /** R5f. `reportsOverview` is Overview: no name field and no Save. */
  mode?: "report" | "reportsOverview";
  /** R5f rule 10. Overview's "Save as report", with the state on screen. */
  onSaveAs?: (state: BuilderState) => void;
  /** R5f. Each preview the server answers, for the Home empty line. */
  onPreview?: (body: PreviewReportBody) => void;
  /** R5f. Open a row of the space table in the Projects page. */
  onOpenNode?: (id: string) => void;
}) {
  const inOverview = mode === "reportsOverview";
  const [draft, setState] = useState<BuilderState>(initial);
  const [preview, setPreview] = useState<{ key: string; body: PreviewReportBody } | null>(
    null
  );
  const [previewError, setPreviewError] = useState<{ key: string; message: string } | null>(
    null
  );
  /** Moves on "Try again", so the same config asks the server once more. */
  const [previewRound, setPreviewRound] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // WS-27bn R5b. Who the reader may report on, through the one read cache.
  const subjects = useCachedResource(projectsKey("reports/subjects"), () =>
    projectsApi.reportSubjects()
  );
  const answer = subjects.data;
  const template = templates?.find((t) => t.key === draft.template) ?? null;
  const needs = requiredSubject(template);
  const chipShown = subjectChipShown(template) || draft.subject !== null;
  const isEdit = editing !== null;

  // "My day" is always about its AUTHOR. A new one takes the reader, derived
  // here and never typed. An edit keeps the stored subject, so an admin who
  // edits a member's day leaves it about the member (R5b-1 repair). A lead
  // of one team starts Team pulse on that team (§6.5 item 7).
  const self = selfSubject(answer);
  const scopes = useMemo(() => scopeOptions(roots), [roots]);
  const derived = startingTeam(
    builderSubject(draft, needs, self, isEdit),
    template,
    answer,
    isEdit
  );
  // The name follows the chips until the member types one (§6.5 item 5).
  const state: BuilderState = {
    ...derived,
    name: builderName(derived, template, answer, scopes),
  };
  const chipStatus = subjectChipStatus(answer, Boolean(subjects.error));

  // "1:1 prep" is about one person: no "Everyone" and no team.
  const allSubjects = subjectOptions(answer, state.subject, isEdit);
  const people =
    needs === "person" ? allSubjects.filter((o) => o.group === "People") : allSubjects;
  const refusal = saveRefusal(state);
  const personPrompt = subjectPrompt(state, template, chipStatus === "failed");
  const projectPrompt = scopePrompt(state, template, isEdit);
  const prompt = personPrompt ?? projectPrompt;
  const noPeriod = periodFree(state);

  // R5f rules 7 and 8. The strip and the table read the summary routes,
  // which take no subject. So they show only for "Everyone" with the
  // subtree, and a null key asks the server for nothing.
  const tableShown = inOverview && overviewTableShown(state);
  const summaryKey = !tableShown
    ? null
    : state.projectId === null
      ? projectsKey("summary")
      : projectsKey(`nodes/${state.projectId}/summary`);
  const summaryNode = state.projectId;
  const summary = useCachedResource<NodeSummary>(summaryKey, () =>
    summaryNode === null ? projectsApi.portfolio() : projectsApi.summary(summaryNode)
  );
  // The first preview asks at once, so Overview does not wait for the delay
  // on each visit. A change after that waits, as before.
  const previewShown = useRef(false);
  // A ref, so a new callback on each render of the parent does not ask the
  // server again. It is written in an effect, never during render.
  const onPreviewRef = useRef(onPreview);
  useEffect(() => {
    onPreviewRef.current = onPreview;
  });

  // The name is not part of the key: the header shows the typed name, so a
  // keystroke in the name field asks the server for nothing.
  const previewKey = JSON.stringify({
    project_id: state.projectId,
    config: configFor(state),
    blocked: prompt !== null,
    round: previewRound,
  });

  useEffect(() => {
    let off = false;
    const { project_id, config, blocked } = JSON.parse(previewKey) as {
      project_id: string | null;
      config: ReturnType<typeof configFor>;
      blocked: boolean;
    };
    // A template about one person with no person yet, or about one project
    // with no project yet: the server would answer 422, so the preview asks
    // for nothing and says what to do.
    if (blocked) return;
    const timer = setTimeout(() => {
      projectsApi.previewReport({ project_id, name: "", config }).then(
        (body) => {
          if (off) return;
          previewShown.current = true;
          setPreview({ key: previewKey, body });
          setPreviewError(null);
          onPreviewRef.current?.(body);
        },
        (e) => {
          if (off) return;
          // The last preview stays, dimmed. The error line says what failed.
          setPreviewError({
            key: previewKey,
            message: message(e, "The preview could not be drawn."),
          });
        }
      );
    }, previewShown.current ? PREVIEW_DELAY_MS : 0);
    return () => {
      off = true;
      clearTimeout(timer);
    };
  }, [previewKey]);

  async function save() {
    if (refusal || prompt) return;
    setSaving(true);
    setSaveError(null);
    try {
      const row = editing
        ? await projectsApi.patchReport(editing.id, patchPayload(state))
        : await projectsApi.createReport(createPayload(state));
      onSaved(row);
    } catch (e) {
      setSaveError(message(e, "That report could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!editing) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await projectsApi.deleteReport(editing.id);
      setConfirmDelete(false);
      onDeleted(editing.id);
    } catch (e) {
      setConfirmDelete(false);
      setDeleteError(message(e, "That report could not be deleted."));
    } finally {
      setDeleting(false);
    }
  }

  // Each error from the server shows next to the chip that caused it.
  const currentError = previewError?.key === previewKey ? previewError.message : null;
  const serverError = saveError ?? (prompt ? null : currentError);
  const errorAt = serverError ? errorChip(serverError) : null;
  const subjectError = errorAt === "subject" ? serverError : null;
  const scopeError = errorAt === "scope" ? serverError : null;
  const otherSaveError = errorAt === "other" ? saveError : null;
  const chipPreviewError = currentError !== null && errorChip(currentError) !== "other";
  const updating = !prompt && preview !== null && preview.key !== previewKey && !currentError;
  const sectionNote = subjectSectionNote(state);
  // A new project-only report asks for a project. An edit keeps its scope.
  const onlyProjects = projectOnly(template) && !isEdit;

  let subjectChip: React.ReactNode = null;
  if (chipShown) {
    if (chipStatus === "failed") {
      subjectChip = <SubjectChipFailed onRetry={subjects.refresh} />;
    } else if (chipStatus === "loading" || !answer) {
      subjectChip = (
        <ChipSlot>
          <Skeleton className="h-7 w-full sm:w-[16rem] xl:w-full" />
        </ChipSlot>
      );
    } else {
      subjectChip = (
        <ChipSlot
          error={subjectError}
          note={subjectChipNote(needs, answer, state.subject, isEdit)}
        >
          <SelectButton
            label="Subject"
            prefix="About:"
            prompt={needs === "person" ? "choose a person" : undefined}
            widthClass={CHIP_WIDTH}
            value={subjectValue(state.subject)}
            defaultValue={needs === "person" ? "" : subjectValue(null)}
            options={people}
            filterAbove={8}
            disabled={needs === "self"}
            onChange={(value) => {
              const subject = parseSubjectValue(value);
              if (subject === undefined) return;
              setState((s) => ({ ...withSubject(s, subject), subjectTouched: true }));
            }}
          />
        </ChipSlot>
      );
    }
  }

  const headerLine = preview
    ? reportHeaderLine({
        subject: subjectLabel(state.subject, answer),
        scope: scopePhrase(state.projectId, state.includeSubtree, scopes),
        periodStart: preview.body.period_start,
        periodEnd: preview.body.period_end,
        periodFree: noPeriod,
        asOf: asOfDay(preview.body.sections.pulse?.today, new Date()),
      })
    : undefined;
  const hiddenHint = hiddenTeamHint(answer, state.subject, chipShown);

  return (
    <div className="space-y-3">
      <h3 className="min-w-0 truncate pr-px text-sm font-semibold text-foreground">
        {inOverview
          ? OVERVIEW_NAME
          : editing
            ? editTitle(editing)
            : (template?.name ?? "Custom report")}
      </h3>
      <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)] xl:items-start">
        <div className="space-y-3 xl:sticky xl:top-0">
          <div className="flex flex-wrap items-start gap-2 text-xs xl:flex-col">
            {subjectChip}
            <ChipSlot error={scopeError}>
              <SelectButton
                label="Scope"
                prefix="In:"
                prompt={onlyProjects ? "choose a project" : undefined}
                widthClass={CHIP_WIDTH}
                value={state.projectId ?? WHOLE_ORGANIZATION}
                defaultValue={onlyProjects ? "" : WHOLE_ORGANIZATION}
                options={scopeChoices(scopes, template, isEdit)}
                filterAbove={8}
                disabled={isEdit}
                onChange={(value) =>
                  setState((s) => ({
                    ...s,
                    projectId: value === WHOLE_ORGANIZATION ? null : value,
                  }))
                }
              />
            </ChipSlot>
            <ChipSlot>
              {noPeriod ? (
                // §6.5 item 3. Every chosen section reads the state
                // now, so a period chip would change nothing.
                <p
                  className="flex h-7 items-center text-xs text-muted-foreground"
                  title="Each section you chose reads the state now. The period does not change this report."
                >
                  {AS_OF_TODAY}
                </p>
              ) : (
                <SelectButton
                  label="Period"
                  prefix="Over:"
                  widthClass={CHIP_WIDTH}
                  value={periodKey(state) ?? SAVED_PERIOD}
                  defaultValue="last_week"
                  options={periodOptions(state)}
                  onChange={(value) => setState((s) => withPeriod(s, value))}
                />
              )}
            </ChipSlot>
          </div>

          {/* A template that locks the subject does not let each choice
              change. An edit is not "started from a template". */}
          {state.template && needs !== "self" && !isEdit && (
            <p className="text-xs text-muted-foreground">
              Started from a template. You can change each choice.
            </p>
          )}

          {isEdit && (
            <p className="text-xs text-muted-foreground">
              The scope of a saved report stays as saved. To report on another
              scope, start a new report.
            </p>
          )}

          {state.projectId !== null && (
            <label className="flex items-center gap-2 text-xs">
              <Checkbox
                size="sm"
                checked={state.includeSubtree}
                onChange={(e) =>
                  setState((s) => ({ ...s, includeSubtree: e.target.checked }))
                }
              />
              Include the projects under it
            </label>
          )}

          <fieldset className="space-y-2">
            <legend className="mb-1 text-xs font-semibold text-foreground">Sections</legend>
            {/* §6.5 item 4: three small labels, each in SECTIONS
                order. The tooltip is the panel's own sentence. */}
            {sectionGroups().map((group) => (
              <div key={group.label}>
                <p className="mb-0.5 text-xs text-muted-foreground">{group.label}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {group.sections.map((section) => {
                    const blocked = sectionBlockedBySubject(state, section.key);
                    const on = !blocked && state.sections.includes(section.key);
                    const last =
                      on &&
                      state.sections.filter((k) => !sectionBlockedBySubject(state, k))
                        .length === 1;
                    return (
                      <label
                        key={section.key}
                        className={`flex items-center gap-1.5 text-xs ${
                          blocked ? "text-muted-foreground" : ""
                        }`}
                        title={
                          blocked
                            ? "Off for a person or team."
                            : last
                              ? "A report needs at least one section."
                              : PANEL_HINTS[section.key]
                        }
                      >
                        <Checkbox
                          size="sm"
                          checked={on}
                          disabled={blocked || last}
                          onChange={() =>
                            setState((s) => ({
                              ...s,
                              sections: toggleSection(s.sections, section.key),
                            }))
                          }
                        />
                        {section.label}
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
            {sectionNote && <p className="text-xs text-muted-foreground">{sectionNote}</p>}
          </fieldset>

          {inOverview ? (
            // R5f rules 10 and 11. Overview saves nothing. This opens the
            // builder with the same choices, and the builder names it.
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                icon="Save"
                disabled={prompt !== null}
                onClick={() => onSaveAs?.(state)}
              >
                Save as report
              </Button>
            </div>
          ) : (
          <>
          <label className="block text-xs">
            <span className="mb-1 block font-semibold text-foreground">Name</span>
            <Input
              inputSize="sm"
              className="w-full sm:w-[20rem] xl:w-full"
              value={state.name}
              maxLength={MAX_REPORT_NAME}
              onChange={(e) =>
                setState((s) => ({ ...s, name: e.target.value, nameTouched: true }))
              }
            />
          </label>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              loading={saving}
              disabled={refusal !== null || prompt !== null}
              onClick={save}
            >
              {editing ? "Save changes" : "Save report"}
            </Button>
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            {/* §6.5 item 10. Absent, never disabled, without the
                server's can_delete (R5d). */}
            {deleteShown(editing) && (
              <Button
                className="ml-auto"
                variant="destructive"
                size="sm"
                icon="Trash2"
                onClick={() => setConfirmDelete(true)}
              >
                Delete report
              </Button>
            )}
          </div>
          {(refusal || otherSaveError || deleteError) && (
            <p className="text-xs text-destructive" role="alert">
              {deleteError ?? otherSaveError ?? refusal}
            </p>
          )}
          </>
          )}
        </div>

        <section
          className="min-w-0 border-t border-border pt-3 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0"
          aria-label="Preview"
        >
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <p className="text-xs text-muted-foreground">
              {inOverview
                ? "The server computes each number. Overview saves nothing."
                : "Preview. The server computes each number, and nothing is saved."}
            </p>
            {updating && (
              <span className="text-xs text-muted-foreground" role="status">
                Updating…
              </span>
            )}
          </div>
          {prompt ? (
            <PreviewPrompt icon={personPrompt ? "User" : "FolderKanban"} text={prompt} />
          ) : currentError && chipPreviewError ? (
            <p className="text-xs text-muted-foreground">
              Change the choice marked above to see the preview.
            </p>
          ) : currentError ? (
            <p className="flex items-center gap-2 text-xs text-destructive" role="alert">
              <span title={currentError}>The preview could not be drawn.</span>
              <Button variant="text" size="sm" onClick={() => setPreviewRound((n) => n + 1)}>
                Try again
              </Button>
            </p>
          ) : preview ? (
            // §6.5 item 9. During a change the last preview stays,
            // dimmed, so the page does not jump. A skeleton only on the first
            // load.
            <div className={updating ? "opacity-60" : undefined} aria-busy={updating}>
              <RenderedBody
                body={{
                  ...preview.body,
                  report: {
                    ...preview.body.report,
                    name: inOverview ? OVERVIEW_NAME : state.name.trim() || NEW_REPORT_NAME,
                  },
                }}
                headerLine={headerLine}
                hiddenHint={hiddenHint}
                lead={
                  !tableShown ? null : summary.data ? (
                    <SpaceSummary
                      summary={summary.data}
                      onOpen={(id) => onOpenNode?.(id)}
                    />
                  ) : summary.loading ? (
                    <SkeletonRows count={2} />
                  ) : null
                }
              />
            </div>
          ) : (
            <SkeletonRows count={3} />
          )}
        </section>
      </div>

      {editing && (
        <ConfirmDialog
          open={confirmDelete}
          {...deleteReportCopy(editing.name)}
          busy={deleting}
          onConfirm={remove}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

/** One template card. A coming-soon card is not a control. */
function TemplateCard({
  template,
  onStart,
}: {
  template: ReportTemplate;
  onStart: (template: ReportTemplate) => void;
}) {
  if (!template.available) {
    // §6.5 item 11. What it waits for is a tooltip, not a line.
    return (
      <div
        aria-disabled="true"
        title={template.waits_for ? `Waits for: ${template.waits_for}` : undefined}
        className="h-full rounded-lg border border-dashed border-border p-2 text-muted-foreground"
      >
        {/* No badge: the section title "Coming later" already says it. */}
        <span className="block min-w-0 truncate pr-px text-sm font-medium">{template.name}</span>
        <p className="mt-1 text-xs">{template.question}</p>
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onStart(template)}
      className="tech-transition h-full w-full rounded-lg border border-border p-2 text-left hover:bg-muted"
    >
      <span className="flex items-center gap-2">
        <Icon name="FileText" className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="min-w-0 truncate pr-px text-sm font-medium text-foreground">
          {template.name}
        </span>
      </span>
      <span className="mt-1 block text-xs text-muted-foreground">{template.question}</span>
    </button>
  );
}

/**
 * WS-27bn R2 — the Reports home (§6.1, as narrowed for R2).
 *
 * ⚠️ **No card renders on load.** A card names a report. A click selects it,
 * and the existing render runs then. A home screen that rendered each card
 * would run every report's SQL on each visit.
 *
 * ⚠️ **"Your reports" is the server's `mine`.** The browser does not compare
 * addresses. It filters the rows the server marked.
 *
 * §6.5 item 11. The live templates come first, in one grid. The
 * coming-soon ones fold under "Coming later (N)".
 *
 * WS-27bn R5f (§6.1). Overview sits above this, and the order under it is
 * the gallery, then "Your reports".
 */
export function ReportsHome({
  rows,
  templates,
  templatesError,
  cardLine,
  finishedCount,
  onOpen,
  onStart,
}: {
  rows: ReportRow[] | null;
  templates: ReportTemplate[] | undefined;
  templatesError: string | null;
  cardLine: (row: ReportRow) => string;
  /** Overview's finished count, when its preview has answered (R5f). */
  finishedCount?: number | null;
  onOpen: (id: string) => void;
  onStart: (template: ReportTemplate) => void;
}) {
  const mine = yourReports(rows ?? []);
  const live = (templates ?? []).filter((t) => t.available);
  const later = (templates ?? []).filter((t) => !t.available);

  return (
    <div className="space-y-4">
      <section aria-labelledby="reports-gallery">
        <h3 id="reports-gallery" className="mb-2 text-xs font-semibold text-foreground">
          Start from a question
        </h3>
        {templatesError && templates === undefined ? (
          <p className="text-xs text-destructive" role="alert">
            {templatesError}
          </p>
        ) : templates === undefined ? (
          <SkeletonRows count={3} />
        ) : (
          <div className="space-y-2">
            <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {live.map((t) => (
                <li key={t.key}>
                  <TemplateCard template={t} onStart={onStart} />
                </li>
              ))}
            </ul>
            {later.length > 0 && (
              <CollapsibleSection label={`Coming later (${later.length})`} defaultOpen={false}>
                <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {later.map((t) => (
                    <li key={t.key}>
                      <TemplateCard template={t} onStart={onStart} />
                    </li>
                  ))}
                </ul>
              </CollapsibleSection>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="reports-yours">
        <h3 id="reports-yours" className="mb-2 text-xs font-semibold text-foreground">
          Your reports
        </h3>
        {rows === null ? (
          <SkeletonRows count={2} />
        ) : mine.length === 0 ? (
          <p className="text-xs text-muted-foreground">{homeEmptyLine(finishedCount)}</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {mine.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => onOpen(r.id)}
                  className="tech-transition h-full w-full rounded-lg border border-border p-2 text-left hover:bg-muted"
                >
                  <span className="block truncate pr-px text-sm font-medium text-foreground">
                    {r.name}
                  </span>
                  <span className="mt-1 block truncate pr-px text-xs text-muted-foreground">
                    {cardLine(r)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * Home and Edit above a saved report.
 *
 * WS-27bn R5d (§9 Q13). **Edit shows only when the server's `can_edit` is
 * true**, so a reader who is not the author or an admin sees no Edit at all.
 * It is absent, never disabled, as the People Center renders a control the
 * reader may not use. The server still refuses the PATCH with 403.
 */
export function ReportActions({
  row,
  onHome,
  onEdit,
}: {
  row: ReportRow;
  onHome: () => void;
  onEdit: () => void;
}) {
  return (
    <div className="ml-auto flex gap-2">
      <Button variant="ghost" size="sm" icon="LayoutGrid" onClick={onHome}>
        Home
      </Button>
      {row.can_edit === true && (
        <Button variant="ghost" size="sm" icon="Pencil" onClick={onEdit}>
          Edit
        </Button>
      )}
    </div>
  );
}

/**
 * A render that failed, inside the pane (§6.5 item 14). The sentence,
 * one "Try again", and the way home or to Edit, so the reader is never stuck
 * on an error with no control.
 */
export function RenderFailed({
  error,
  row,
  onRetry,
  onHome,
  onEdit,
}: {
  error: string;
  row: ReportRow | null;
  onRetry: () => void;
  onHome: () => void;
  onEdit: () => void;
}) {
  return (
    // The sentence first, then what to do about it: Try again beside it,
    // and the way home or to Edit on the right (visual review, 2026-09-29).
    <div className="flex flex-wrap items-center gap-2">
      <p className="min-w-0 text-xs text-destructive" role="alert">
        {error}
      </p>
      <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
        Try again
      </Button>
      {row ? (
        <ReportActions row={row} onHome={onHome} onEdit={onEdit} />
      ) : (
        <Button className="ml-auto" variant="ghost" size="sm" icon="LayoutGrid" onClick={onHome}>
          Home
        </Button>
      )}
    </div>
  );
}

/** One list of the rail: a small label, then a row for each report. */
function RailList({
  title,
  rows,
  selected,
  author,
  scopeLabel,
  onOpen,
}: {
  title: string;
  rows: ReportRow[];
  selected: string | null;
  /** The scope in the chips' words: "Whole organization" or the project. */
  scopeLabel: (row: ReportRow) => string;
  /** The author's name, on a shared row. */
  author?: (row: ReportRow) => string;
  onOpen: (id: string) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="px-2 pb-0.5 pt-1 text-xs font-semibold text-muted-foreground">{title}</p>
      <ul className="space-y-0.5">
        {rows.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              onClick={() => onOpen(r.id)}
              className={`flex w-full items-start gap-1.5 rounded px-2 py-1 text-left text-xs hover:bg-muted ${
                selected === r.id ? "bg-muted font-medium" : ""
              }`}
            >
              <Icon name="FileText" className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block truncate pr-px">{r.name}</span>
                {/* The chips' words (§6.5 item 12): "Whole organization" or
                    the project's name, never "All" or "Node". A line of its
                    own, so a long project name does not cut the report's. */}
                <span
                  className="block truncate pr-px font-normal text-muted-foreground"
                  title={scopeLabel(r)}
                >
                  {scopeLabel(r)}
                </span>
                {author ? (
                  <span className="block truncate pr-px font-normal text-muted-foreground">
                    by {author(r)}
                  </span>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** What the right pane shows: a saved render, or the builder. */
type Pane =
  | { kind: "view" }
  | { kind: "new"; initial: BuilderState }
  | { kind: "edit"; row: ReportRow };

/**
 * The one Reports app (WS-27bn R5f, §9 Q14). Home is Overview, then the
 * gallery, then "Your reports". A saved report opens with the rail beside
 * it. The rail is absent on Overview (rule 16).
 *
 * ⚠️ The page passed the portfolio's `finished` roll-up here before R5f,
 * and the page no longer fetched it for Reports, so the Home line never had
 * a count. The count now comes from Overview's own preview.
 */
export default function ReportsView({
  onOpenNode,
}: {
  /** Open a row of Overview's space table in the Projects page. */
  onOpenNode?: (id: string) => void;
}) {
  const [rows, setRows] = useState<ReportRow[] | null>(null);
  /** The finished total of Overview's last preview, for the Home line. */
  const [overviewFinished, setOverviewFinished] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [body, setBody] = useState<RenderedReportBody | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pane, setPane] = useState<Pane>({ kind: "view" });
  /** Moves after a save, and on "Try again", so the render runs again. */
  const [renderRound, setRenderRound] = useState(0);

  // The scope chip lists the tree the caller can see. One read cache, the
  // same key the page uses, so this adds no request on a warm page.
  const tree = useCachedResource(projectsKey("tree"), () => projectsApi.tree());
  const roots = useMemo(() => tree.data?.rows ?? [], [tree.data]);
  const scopes = useMemo(() => scopeOptions(roots), [roots]);

  // WS-27bn R2. The catalogue is the server's, through the one read cache.
  const catalogue = useCachedResource(projectsKey("reports/templates"), () =>
    projectsApi.reportTemplates()
  );
  const templates = catalogue.data?.templates;

  // §6.5 items 5, 12 and 13. The names of subjects and authors,
  // from the key the builder reads, so this adds no request once it ran.
  const subjects = useCachedResource(projectsKey("reports/subjects"), () =>
    projectsApi.reportSubjects()
  );

  function scopeName(row: ReportRow): string {
    if (row.project_id === null) return "Whole organization";
    return scopes.find((s) => s.value === row.project_id)?.label ?? "Project";
  }

  function authorName(row: ReportRow): string {
    const who = (row.created_by ?? "").trim().toLowerCase();
    return subjects.data?.people.find((p) => p.email === who)?.name || row.created_by;
  }

  function cardLine(row: ReportRow): string {
    return reportCardLine(
      row,
      templateLabel(row, templates ?? []),
      subjectLabel(row.config.subject ?? null, subjects.data),
      scopeName(row)
    );
  }

  // WS-27bn R5b. A link from `reportLink` opens the builder filled in. The
  // keys are read ONCE, when the catalogue has arrived, and then removed
  // from the address, so a reload does not open the builder again. The page
  // already removed `app`.
  const router = useRouter();
  const searchParams = useSearchParams();
  const linkQuery = REPORT_LINK_KEYS.some((k) => searchParams.has(k))
    ? searchParams.toString()
    : "";
  useEffect(() => {
    // The tree must arrive too: the node is kept only when it is in it.
    if (!linkQuery || templates === undefined) return;
    if (tree.data === undefined && !tree.error) return;
    const params = new URLSearchParams(linkQuery);
    const intent = parseReportLink(params, templates, roots);
    // A link is consumed by setting state once, as the page's `?app=` does.
    /* eslint-disable react-hooks/set-state-in-effect */
    if (intent) setPane({ kind: "new", initial: builderStateFromLink(intent) });
    /* eslint-enable react-hooks/set-state-in-effect */
    for (const k of REPORT_LINK_KEYS) params.delete(k);
    const qs = params.toString();
    router.replace(qs ? `/projects?${qs}` : "/projects");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkQuery, templates, roots, tree.error]);

  /** Open the builder from a template. A coming-soon one opens nothing. */
  function start(template: ReportTemplate) {
    const initial = builderStateFromTemplate(template);
    if (initial === null) return;
    setPane({ kind: "new", initial });
  }

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
  }, [selected, renderRound]);

  function saved(row: ReportRow) {
    setRows((prev) => [row, ...(prev ?? []).filter((r) => r.id !== row.id)]);
    setPane({ kind: "view" });
    setSelected(row.id);
    setRenderRound((n) => n + 1);
  }

  function deleted(id: string) {
    setRows((prev) => (prev ?? []).filter((r) => r.id !== id));
    setPane({ kind: "view" });
    setSelected(null);
  }

  const selectedRow = rows?.find((r) => r.id === selected) ?? null;
  const building = pane.kind !== "view";
  // R5f rule 16. Overview is Home, and the rail is absent there. It shows
  // when a member opens a saved report.
  const railShown = !building && selected !== null;
  const { yours, shared } = railGroups(rows ?? []);
  const header =
    body && selectedRow
      ? reportHeaderLine({
          subject: subjectLabel(selectedRow.config.subject ?? null, subjects.data),
          scope: scopePhrase(
            selectedRow.project_id,
            selectedRow.config.include_subtree,
            scopes
          ),
          periodStart: body.period_start,
          periodEnd: body.period_end,
          periodFree: periodFree({
            sections: selectedRow.config.sections,
            subject: selectedRow.config.subject ?? null,
          }),
          asOf: asOfDay(body.sections.pulse?.today, new Date()),
        })
      : undefined;

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        {/* R5f visual review. The page header above already says "Reports ·
            Look now, or save to deliver", so this row adds no second
            subtitle. */}
        <h2 className="text-sm font-semibold">Reports</h2>
        <Button
          className="ml-auto"
          variant="secondary"
          size="sm"
          icon="Plus"
          disabled={pane.kind === "new"}
          onClick={() => setPane({ kind: "new", initial: newBuilderState() })}
        >
          New report
        </Button>
      </div>

      {/* §6.5 item 1. While the member builds a report the rail leaves, and
          the builder takes the width for its preview. R5f rule 16: Home is
          Overview, and the rail leaves there too. */}
      <div
        className={
          railShown ? "grid gap-3 lg:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]" : ""
        }
      >
        {railShown && (
          <aside className="rounded-lg border border-border bg-card p-2">
            {rows === null ? (
              <SkeletonRows count={3} />
            ) : (
              <div className="space-y-2">
                <RailList
                  title="Yours"
                  rows={yours}
                  selected={selected}
                  scopeLabel={scopeName}
                  onOpen={(id) => setSelected(id)}
                />
                <RailList
                  title="Shared with you"
                  rows={shared}
                  selected={selected}
                  author={authorName}
                  scopeLabel={scopeName}
                  onOpen={(id) => setSelected(id)}
                />
              </div>
            )}
          </aside>
        )}

        <div className="min-w-0 rounded-lg border border-border bg-card p-3">
          {pane.kind === "new" ? (
            <ReportBuilder
              key={`new:${pane.initial.template ?? "blank"}:${subjectValue(pane.initial.subject)}:${pane.initial.projectId ?? ""}`}
              initial={pane.initial}
              editing={null}
              roots={roots}
              templates={templates}
              onSaved={saved}
              onDeleted={deleted}
              onCancel={() => setPane({ kind: "view" })}
            />
          ) : pane.kind === "edit" ? (
            <ReportBuilder
              key={`edit:${pane.row.id}`}
              initial={builderStateFrom(pane.row)}
              editing={pane.row}
              roots={roots}
              templates={templates}
              onSaved={saved}
              onDeleted={deleted}
              onCancel={() => setPane({ kind: "view" })}
            />
          ) : !selected ? (
            // R5f (§6.1). Home is Overview, then the gallery, then "Your
            // reports". Overview writes no row. "Save as report" opens the
            // builder with its choices.
            <div className="space-y-4">
              <ReportBuilder
                key="reportsOverview"
                mode="reportsOverview"
                initial={overviewState()}
                editing={null}
                roots={roots}
                templates={templates}
                onSaved={saved}
                onDeleted={deleted}
                onCancel={() => undefined}
                onSaveAs={(state) =>
                  setPane({ kind: "new", initial: saveAsReportState(state) })
                }
                onPreview={(preview) =>
                  setOverviewFinished(preview.sections.finished?.total_completed ?? null)
                }
                onOpenNode={onOpenNode}
              />
              <div className="border-t border-border pt-4">
                <ReportsHome
                  rows={rows}
                  templates={templates}
                  templatesError={catalogue.error}
                  cardLine={cardLine}
                  finishedCount={overviewFinished}
                  onOpen={(id) => setSelected(id)}
                  onStart={start}
                />
              </div>
            </div>
          ) : error ? (
            <RenderFailed
              error={error}
              row={selectedRow}
              onRetry={() => setRenderRound((n) => n + 1)}
              onHome={() => setSelected(null)}
              onEdit={() => selectedRow && setPane({ kind: "edit", row: selectedRow })}
            />
          ) : body === null ? (
            <SkeletonRows count={4} />
          ) : (
            <div className="space-y-3">
              {/* WS-27bm S8: the report as a file, beside Edit. Rendered
                  again on the click, so the file carries the numbers of that
                  moment. WS-27bn R2: Home returns to the Reports home. */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <ReportFileButtons reportId={selected} />
                {selectedRow && (
                  <ReportActions
                    row={selectedRow}
                    onHome={() => setSelected(null)}
                    onEdit={() => setPane({ kind: "edit", row: selectedRow })}
                  />
                )}
              </div>
              <RenderedBody body={body} headerLine={header} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
