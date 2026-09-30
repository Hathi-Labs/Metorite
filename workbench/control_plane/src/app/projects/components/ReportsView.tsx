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
import { useEffect, useId, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { LayoutBoundary } from "@/components/LayoutBoundary";
import Badge from "@/components/ui/Badge";
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
  linkStep,
  REPORT_SECTIONS,
  reportsPane,
  sectionName,
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
  BODY_ORDER,
  type LiveTimerEnv,
  type PreviewState,
  bodyDimmed,
  browserStorage,
  browserTimerEnv,
  createPreviewController,
  gridSpans,
  overviewFilterCount,
  overviewSummary,
  previewRequest,
  readFiltersOpen,
  startLive,
  startTicker,
  updatedLine,
  writeFiltersOpen,
} from "../lib/overviewLive";
import { allClear, clearLine, sectionIsClear } from "../lib/sectionEmpty";
import { sectionIcon } from "../lib/sectionIcons";
import {
  CapacityPanel,
  ConflictsPanel,
  FinishedPanel,
  HygienePanel,
  LoadPanel,
  OutlookPanel,
  PanelChromeContext,
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
 * A section's table (WS-27bn R2b). Since R5f round 1 (§6.6 D item 5) it
 * opens from the table button in the header of the section's card, and it
 * sits inside that card.
 *
 * ⚠️ `title` is the section's name in the report's own words, which the
 * chat's report card borrows (`skill_projects/views.py`
 * `REPORT_CARD_SECTIONS`, fenced by `test_projects_agent.py`). It labels the
 * table for a screen reader.
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
    // The count is in the label (H-186 item 2). A visible "N rows" line
    // was noise inside the card (the R5f round 1 visual review).
    <div
      role="group"
      aria-label={`${title}, as a table${typeof count === "number" ? `, ${count} ${count === 1 ? "row" : "rows"}` : ""}`}
    >
      {children}
    </div>
  );
}

/**
 * A section in a report: its panel, with the section's icon and its table
 * in the card header (§6.6 D). `PanelChromeContext` carries both into the
 * panel, so each panel stays the only child of its `LayoutBoundary`.
 */
function SectionFrame({
  sectionKey,
  table,
  fill,
  children,
}: {
  sectionKey: string;
  table?: React.ReactNode;
  /** R5g round 1. The card fills the height of its grid cell. */
  fill?: boolean;
  children: React.ReactNode;
}) {
  return (
    <PanelChromeContext.Provider value={{ icon: sectionIcon(sectionKey), table, fill }}>
      {children}
    </PanelChromeContext.Provider>
  );
}

/** One clear section: a check mark, its title and one friendly line (§6.6 D item 3). */
export function ClearRow({ sectionKey, title }: { sectionKey: string; title: string }) {
  const done = statusAccent({ category: "done" });
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2">
      <Icon name="CircleCheckBig" className={`h-4 w-4 shrink-0 ${done.text}`} />
      <span className="min-w-0 text-xs">
        <span className="font-medium text-foreground">{title}</span>
        <span className="text-muted-foreground"> · {clearLine(sectionKey)}</span>
      </span>
    </div>
  );
}

/**
 * The one card of a report whose sections are all clear (§6.6 D item 3). The
 * owner's case: Team pulse on a quiet day looked like three error boxes.
 */
export function AllClear({ keys, scope }: { keys: string[]; scope: string }) {
  const done = statusAccent({ category: "done" });
  const where = scope.startsWith("In ") ? ` ${scope.charAt(0).toLowerCase()}${scope.slice(1)}` : scope ? ` in the ${scope.toLowerCase()}` : "";
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Icon name="CircleCheckBig" className={`h-5 w-5 shrink-0 ${done.text}`} />
        All clear
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {`Nothing needs attention${where} right now.`}
      </p>
      <ul className="mt-3 space-y-1.5">
        {keys.map((key) => (
          <li key={key} className="flex items-start gap-2 text-xs">
            <Icon name="Check" className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${done.text}`} />
            {/* R5f round 2 (item 11). The name over the line on a phone, so
                a two-word name does not break in two. */}
            <span className="flex min-w-0 flex-col sm:flex-row sm:gap-2">
              <span className="font-medium whitespace-nowrap text-foreground">
                {sectionName(key)}
              </span>
              <span className="text-muted-foreground">{clearLine(key)}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The meta line under a report's title, with a small icon for each part
 * (§6.6 D item 2). `reportHeaderLine` stays the one source of the words, and
 * this only splits them on " · ".
 */
export function HeaderMeta({ line }: { line: string }) {
  const parts = line.split(" · ").filter(Boolean);
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {parts.map((part, i) => {
        const icon = part.startsWith("About ")
          ? "User"
          : i === parts.length - 1
            ? "CalendarDays"
            : "FolderKanban";
        return (
          <span key={`${i}:${part}`} className="inline-flex items-center gap-1">
            <Icon name={icon} className="h-3.5 w-3.5 shrink-0" />
            {part}
          </span>
        );
      })}
    </p>
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
  showTitle = true,
  layout = "column",
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
  /**
   * §6.6 D. The builder names the report in its header row, so its preview
   * shows the meta line only. A saved report shows its name.
   */
  showTitle?: boolean;
  /**
   * WS-27bn R5g (§6.7). `grid` is the live Overview: the panels flow in two
   * columns at `xl`, and a wide panel spans both. The toolbar says the
   * choices, so the header line goes. A saved report and the builder keep
   * `column`, one readable measure.
   */
  layout?: "column" | "grid";
}) {
  const done = statusAccent({ category: "done" });
  const late = statusAccent({ category: "cancelled" });
  const { sections } = body;
  const grid = layout === "grid";
  // R5g rule 1. Which panels take the full row. `gridSpans` leaves no row
  // with one panel alone.
  const drawn = BODY_ORDER.filter((k) => (sections as Record<string, unknown>)[k]);
  // R5g round 1. A clear row takes a full row. Stretched beside a chart, a
  // one-line row reads as an empty card.
  const clearKeys = new Set(
    drawn.filter((k) => sectionIsClear(k, (sections as Record<string, never>)[k]))
  );
  const wide = grid ? gridSpans(drawn, clearKeys) : new Set<string>();
  /** One cell of the Overview grid. In a column the section draws bare. */
  // R5g round 1. The cells of one row stretch to one height, and each
  // panel card fills its cell (`fill`), so a short card leaves no gap.
  const cell = (key: string, node: React.ReactNode) =>
    grid ? (
      <div key={key} className={wide.has(key) ? "min-w-0 xl:col-span-2" : "min-w-0"}>
        {node}
      </div>
    ) : (
      node
    );
  const tiles = reportTiles(sections);
  const tone = { done: done.text, late: late.text };
  // §6.6 D item 3. Every chosen section is clear: one calm card, and no row
  // for each section.
  const clear = allClear(sections);
  const line =
    headerLine ??
    `${body.report.scope === "portfolio" ? "Whole organization" : "This project"} · ${periodLabel(body.period_start, body.period_end)}`;

  return (
    <div className="space-y-3">
      {!grid && (
        <header className="space-y-1">
          {showTitle && (
            <h3 className="text-base font-semibold text-foreground">{body.report.name}</h3>
          )}
          <HeaderMeta line={line} />
        </header>
      )}

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

      {/* §6.6 E item 2. The strip and the space table take the full width. */}
      {lead}

      {clear.clear ? (
        <AllClear keys={clear.keys} scope={line.split(" · ").find((p) => !p.startsWith("About ")) ?? ""} />
      ) : (
      // ⚠️ A readable MEASURE, not the panel's full width. Photographed
      // 2026-09-17: at 1440 the rows ran the whole pane, so "Mobile App" sat
      // about 1200px from its own number and the eye could not join them. A
      // report body is a document, and a document has a column width.
      // R5g: the live Overview is a dashboard, so its panels flow in a grid,
      // and each cell is narrow enough to read.
      <div
        className={
          grid ? "grid grid-cols-1 gap-3 xl:grid-cols-2" : "max-w-3xl space-y-3"
        }
      >

      {sections.finished &&
        cell(
          "finished",
          sectionIsClear("finished", sections.finished) ? (
          <ClearRow sectionKey="finished" title="What we finished" />
        ) : (
          <SectionFrame
            sectionKey="finished"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="finished work">
              <FinishedPanel data={finishedPanelData(sections.finished, body)} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {sections.throughput &&
        cell(
          "throughput",
          sectionIsClear("throughput", sections.throughput) ? (
          <ClearRow sectionKey="throughput" title="How long it took" />
        ) : (
          <SectionFrame
            sectionKey="throughput"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="throughput">
              <ThroughputPanel
                data={throughputPanelData(sections.throughput, body)}
              />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {/* WS-27bn R3a. Opt-in. The outlook route's own body, for this scope.
          The table says each figure the panel draws, in words. */}
      {sections.outlook &&
        cell(
          "outlook",
          sectionIsClear("outlook", sections.outlook) ? (
          <ClearRow sectionKey="outlook" title="Forecast" />
        ) : (
          <SectionFrame
            sectionKey="outlook"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="forecast">
              <OutlookPanel data={outlookPanelData(sections.outlook)} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {sections.stuck &&
        cell(
          "stuck",
          sectionIsClear("stuck", sections.stuck) ? (
          <ClearRow sectionKey="stuck" title="Stuck work" />
        ) : (
          <SectionFrame
            sectionKey="stuck"
            fill={grid}
            table={
              <Table title="Stuck work" count={sections.stuck.overdue.length}>
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
            }
          >
            <LayoutBoundary layout="stuck work">
              <StuckPanel data={stuckPanelData(sections.stuck, body)} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {sections.load &&
        cell(
          "load",
          sectionIsClear("load", sections.load) ? (
          <ClearRow sectionKey="load" title="Open work" />
        ) : (
          <SectionFrame
            sectionKey="load"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="load">
              <LoadPanel data={loadPanelData(sections.load, body)} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {/* WS-27bm S7a. Opt-in: only a report that asked for `capacity` has it.
          The rows and the hours are the capacity route's, verbatim. */}
      {sections.capacity &&
        cell(
          "capacity",
          sectionIsClear("capacity", sections.capacity) ? (
          <ClearRow sectionKey="capacity" title="Who has the hours" />
        ) : (
          <SectionFrame
            sectionKey="capacity"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="capacity">
              <CapacityPanel data={capacityPanelData(sections.capacity, body)} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {/* WS-27bn R3d. Opt-in. `pulse_body`, read today and not over the
          period. The server removed the cards this reader may not see, and
          the table names each card and each focus task it sent. */}
      {sections.pulse &&
        cell(
          "pulse",
          sectionIsClear("pulse", sections.pulse) ? (
          <ClearRow sectionKey="pulse" title="Team pulse" />
        ) : (
          <SectionFrame
            sectionKey="pulse"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="team pulse">
              <PulsePanel data={pulsePanelData(sections.pulse)} hiddenHint={hiddenHint} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {/* WS-27bn R3c. Opt-in. `hygiene_body`, read now and not over the
          period. The table names each task the server sent, kind by kind. */}
      {sections.hygiene &&
        cell(
          "hygiene",
          sectionIsClear("hygiene", sections.hygiene) ? (
          <ClearRow sectionKey="hygiene" title="Data hygiene" />
        ) : (
          <SectionFrame
            sectionKey="hygiene"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="data hygiene">
              <HygienePanel data={hygienePanelData(sections.hygiene)} />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {/* WS-27bm S7c. Opt-in: only a report that asked for `conflicts` has
          it. The rows and the sentences are the conflicts route's, verbatim. */}
      {sections.conflicts &&
        cell(
          "conflicts",
          sectionIsClear("conflicts", sections.conflicts) ? (
          <ClearRow sectionKey="conflicts" title="Where the plan conflicts" />
        ) : (
          <SectionFrame
            sectionKey="conflicts"
            fill={grid}
            table={
              <Table title="Where the plan conflicts" count={sections.conflicts.rows.length}>
                <p
                  className="mb-1 text-xs text-muted-foreground"
                  title={`Counted by the server over every row. The plan is read ${sections.conflicts.horizon_days} days ahead.`}
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
            }
          >
            <LayoutBoundary layout="conflicts">
              <ConflictsPanel
                data={conflictsPanelData(sections.conflicts, body)}
              />
            </LayoutBoundary>
          </SectionFrame>
        ))}

      {/* WS-27bn R3b. Opt-in, and read only. The rebalance route's own body.
          Without the HR grant it has no lists, and the table says why. */}
      {sections.rebalance &&
        cell(
          "rebalance",
          sectionIsClear("rebalance", sections.rebalance) ? (
          <ClearRow sectionKey="rebalance" title="Who could help" />
        ) : (
          <SectionFrame
            sectionKey="rebalance"
            fill={grid}
            table={
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
            }
          >
            <LayoutBoundary layout="who could help">
              <RebalancePanel data={rebalancePanelData(sections.rebalance)} />
            </LayoutBoundary>
          {/* H-186 item 2. The table lists the idle people too, so they count. */}
          </SectionFrame>
        ))}
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
const CHIP_WIDTH = "w-full";

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
          label="Who is it about?"
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
  onNewReport,
  initialPreview = null,
  onDraft,
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
  /**
   * R5f. Each preview the server answers, with its key, for Home to keep.
   * R5g adds `at`, the time of the answer, so "Updated …" survives a return.
   */
  onPreview?: (body: PreviewReportBody, key: string, at: number) => void;
  /** R5f. Open a row of the space table in the Projects page. */
  onOpenNode?: (id: string) => void;
  /** R5f round 1 (§6.6 E). "New report" in the Overview toolbar. */
  onNewReport?: () => void;
  /**
   * R5f round 1, rule 6. The last preview Home kept. When its key equals
   * the key of the choices, the builder asks the server for nothing.
   */
  initialPreview?: { key: string; body: PreviewReportBody; at?: number } | null;
  /** R5f round 1, rule 6. Each change of the choices, for Home to keep. */
  onDraft?: (draft: BuilderState) => void;
}) {
  const inOverview = mode === "reportsOverview";
  const [draft, setState] = useState<BuilderState>(initial);
  /** R5g round 1. The key of the choices now, for the guard of a late answer. */
  const keyRef = useRef("");
  // A ref, so a new callback on each render of the parent does not ask the
  // server again. It is written in an effect, never during render.
  const onPreviewRef = useRef(onPreview);
  /**
   * R5g round 1. The one owner of the preview requests: the first preview, a
   * change and a refresh (`createPreviewController`). Its guard drops each
   * late answer, and its state is what the pane draws.
   */
  const [ctl] = useState(() =>
    createPreviewController<PreviewReportBody>({
      initial: initialPreview,
      fetch: ({ project_id, config }) => projectsApi.previewReport({ project_id, name: "", config }),
      now: () => Date.now(),
      errorMessage: (e) => message(e, "The preview could not be drawn."),
    })
  );
  const [live, setLive] = useState<PreviewState<PreviewReportBody>>(() => ctl.state);
  // The ports read refs, so they connect in an effect. It runs before the
  // effect that sends the first request.
  useEffect(() => {
    ctl.bind({
      currentKey: () => keyRef.current,
      onChange: setLive,
      onAnswer: (body, key, at) => onPreviewRef.current?.(body, key, at),
    });
    // No answer lands after unmount.
    return () => ctl.dispose();
  }, [ctl]);
  const preview = live.preview;
  const previewError = live.error;
  /**
   * R5g rule 3. The Overview Filters. Closed on the server and at the first
   * render, on every screen. An effect then reads what the member left.
   */
  const [filtersOpen, setFiltersOpen] = useState(false);
  const controlsId = useId();
  const filtersButtonId = useId();
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
  const previewShown = useRef(initialPreview !== null);
  const onDraftRef = useRef(onDraft);
  useEffect(() => {
    onPreviewRef.current = onPreview;
    onDraftRef.current = onDraft;
  });
  useEffect(() => {
    onDraftRef.current?.(draft);
  }, [draft]);
  // The name is not part of the key: the header shows the typed name, so a
  // keystroke in the name field asks the server for nothing.
  const previewKey = JSON.stringify({
    project_id: state.projectId,
    config: configFor(state),
    blocked: prompt !== null,
    round: previewRound,
  });
  useEffect(() => {
    keyRef.current = previewKey;
  });

  useEffect(() => {
    const { blocked } = previewRequest(previewKey);
    // A template about one person with no person yet, or about one project
    // with no project yet: the server would answer 422, so the preview asks
    // for nothing and says what to do.
    // R5f round 1, rule 6. The preview on screen already answers this key.
    if (!ctl.needed(previewKey, blocked)) return;
    const timer = setTimeout(
      () => {
        previewShown.current = true;
        ctl.request(previewKey);
      },
      previewShown.current ? PREVIEW_DELAY_MS : 0
    );
    return () => clearTimeout(timer);
  }, [previewKey, ctl]);

  // R5g rule 4. Overview is live (`startLive`). A timer refreshes every 5
  // minutes while the tab is visible, and a hidden tab stops it. A return to
  // the tab, and a mount, refresh an answer over a minute old.
  useEffect(() => {
    if (!inOverview) return;
    return startLive(browserTimerEnv(), ctl);
  }, [inOverview, ctl]);

  // R5g rule 3. Read what the member left once, after the first render, so
  // the server and the first render agree: Filters closed.
  useEffect(() => {
    if (!inOverview) return;
    // A stored preference is read once, as `panelMode` reads its own.
    /* eslint-disable-next-line react-hooks/set-state-in-effect */
    setFiltersOpen(readFiltersOpen(browserStorage));
  }, [inOverview]);

  function toggleFilters() {
    const next = !filtersOpen;
    setFiltersOpen(next);
    writeFiltersOpen(browserStorage, next);
  }

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
  // R5g round 1. Only other choices dim the body. A refresh keeps the key.
  const updating = !prompt && bodyDimmed(live, previewKey);
  const sectionNote = subjectSectionNote(state);
  // A new project-only report asks for a project. An edit keeps its scope.
  const onlyProjects = projectOnly(template) && !isEdit;

  // The locked chip of "My day": "You" for its author, the author's name for
  // an admin who edits it (§6.6 B item 1).
  const selfEmail = self?.kind === "person" ? self.email : null;
  const lockedLabel =
    state.subject?.kind === "person" && state.subject.email !== selfEmail
      ? (subjectLabel(state.subject, answer) ?? "Its author")
      : "You";

  let subjectChip: React.ReactNode = null;
  if (chipShown) {
    if (chipStatus === "failed") {
      subjectChip = <SubjectChipFailed onRetry={subjects.refresh} />;
    } else if (chipStatus === "loading" || !answer) {
      subjectChip = (
        <ChipSlot>
          <Skeleton className="h-8 w-full" />
        </ChipSlot>
      );
    } else if (needs === "self") {
      subjectChip = (
        <ChipSlot note={subjectChipNote(needs, answer, state.subject, isEdit)}>
          <p className="flex h-8 w-full items-center gap-2 rounded-lg border border-border bg-muted/40 px-2.5 text-xs text-foreground">
            <Icon name="Lock" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            {lockedLabel}
          </p>
        </ChipSlot>
      );
    } else {
      subjectChip = (
        <ChipSlot
          error={subjectError}
          note={subjectChipNote(needs, answer, state.subject, isEdit)}
        >
          <SelectButton
            label="Who is it about?"
            prompt={needs === "person" ? "Choose a person" : undefined}
            widthClass={CHIP_WIDTH}
            value={subjectValue(state.subject)}
            defaultValue={needs === "person" ? "" : subjectValue(null)}
            options={people}
            filterAbove={8}
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
  const saveDisabled = refusal !== null || prompt !== null;

  const actions = (
    <>
      {/* §6.6 A item 3. Absent, never disabled, without the server's
          can_delete (R5d). Quiet, and apart from Save. */}
      {deleteShown(editing) && (
        <Button
          variant="text"
          size="sm"
          icon="Trash2"
          onClick={() => setConfirmDelete(true)}
        >
          Delete report
        </Button>
      )}
      <Button variant="ghost" size="sm" onClick={onCancel}>
        Cancel
      </Button>
      <Button
        variant="primary"
        size="sm"
        loading={saving}
        disabled={saveDisabled}
        title={refusal ?? prompt ?? undefined}
        onClick={save}
      >
        {editing ? "Save changes" : "Save report"}
      </Button>
    </>
  );

  // ⚠️ R5g rule 3. ONE set of controls. The builder lays them out as three
  // steps, and the Overview Filters lay them out in one row. The state, the
  // handlers and the rules are the same, so the two cannot drift.
  const workControl = (
    <>
      <ChipSlot error={scopeError}>
        <SelectButton
          label="Which work?"
          prompt={onlyProjects ? "Choose a project" : undefined}
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
      {isEdit && (
        <p className="text-xs text-muted-foreground">
          A saved report keeps its scope. Start a new report for another one.
        </p>
      )}
      {state.projectId !== null && (
        <label className="flex items-center gap-2 text-xs text-foreground">
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
    </>
  );

  const timeControl = noPeriod ? (
    // §6.6 B item 3. Every chosen section reads the state now, so a
    // period chip would change nothing.
    <p
      className="inline-flex h-8 items-center gap-1.5 rounded-full bg-muted px-3 text-xs text-muted-foreground"
      title="Each section you chose reads the state now. The period does not change this report."
    >
      <Icon name="Clock" className="h-3.5 w-3.5" />
      {AS_OF_TODAY}
    </p>
  ) : (
    <SelectButton
      label="What time?"
      widthClass={CHIP_WIDTH}
      value={periodKey(state) ?? SAVED_PERIOD}
      defaultValue="last_week"
      options={periodOptions(state)}
      onChange={(value) => setState((s) => withPeriod(s, value))}
    />
  );

  /** The section toggles of one group: tiles in the builder, chips in Overview. */
  const sectionToggles = (
    sections: { key: string; label: string }[],
    variant: "tile" | "chip"
  ) =>
    sections.map((section) => {
      const blocked = sectionBlockedBySubject(state, section.key);
      const on = !blocked && state.sections.includes(section.key);
      const last =
        on &&
        state.sections.filter((k) => !sectionBlockedBySubject(state, k)).length === 1;
      return (
        <SectionTile
          key={section.key}
          sectionKey={section.key}
          label={section.label}
          on={on}
          blocked={blocked}
          last={last}
          variant={variant}
          onToggle={() =>
            setState((s) => ({
              ...s,
              sections: toggleSection(s.sections, section.key),
            }))
          }
        />
      );
    });

  const controls = (
    <div className="space-y-3 rounded-xl border border-border bg-card p-3">
      <p className="text-sm font-semibold text-foreground">Build your report</p>
      {chipShown && (
        <Step n={1} label="Who is it about?">
          {subjectChip}
        </Step>
      )}
      <Step n={chipShown ? 2 : 1} label="Which work?">
        {workControl}
      </Step>
      <Step n={chipShown ? 3 : 2} label="What time?">
        {timeControl}
      </Step>

      <div className="border-t border-border pt-3">
      {/* `min-w-0`: a fieldset keeps a min-content width by default, and a
          truncated tile then pushes the card wider than its column. */}
      <fieldset className="min-w-0 space-y-2">
        <legend className="mb-1 text-sm font-semibold text-foreground">
          What to include
        </legend>
        {/* §6.6 C. Tiles in three groups, each in SECTIONS order. The
            tooltip is the panel's own sentence. */}
        {sectionGroups().map((group) => (
          <div key={group.label} className="space-y-1">
            <p className="text-xs text-muted-foreground">{group.label}</p>
            <div className="grid min-w-0 grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-1">
              {sectionToggles(group.sections, "tile")}
            </div>
          </div>
        ))}
        {sectionNote && <p className="text-xs text-muted-foreground">{sectionNote}</p>}
      </fieldset>
      </div>
      {(otherSaveError || deleteError) && (
        <p className="text-xs text-destructive" role="alert">
          {deleteError ?? otherSaveError}
        </p>
      )}
    </div>
  );

  const previewBody = prompt ? (
    <PreviewPrompt icon={personPrompt ? "User" : "FolderKanban"} text={prompt} />
  ) : currentError && chipPreviewError ? (
    <p className="text-xs text-muted-foreground">
      {inOverview
        ? "Open Filters and change the marked choice to see Overview."
        : "Change the choice marked on the left to see the preview."}
    </p>
  ) : currentError ? (
    <p className="flex items-center gap-2 text-xs text-destructive" role="alert">
      <span title={currentError}>The preview could not be drawn.</span>
      <Button variant="text" size="sm" onClick={() => setPreviewRound((n) => n + 1)}>
        Try again
      </Button>
    </p>
  ) : preview ? (
    // §6.5 item 9. During a change the last preview stays, dimmed,
    // so the page does not jump. A skeleton only on the first load.
    // R5g rule 4. A refresh does not dim it: only the spinner moves.
    <div
      className={`transition-opacity ${updating ? "opacity-60" : ""}`}
      aria-busy={updating}
    >
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
        // §6.6 D. The header row above names the report, so the
        // preview does not say the name a second time.
        showTitle={false}
        layout={inOverview ? "grid" : "column"}
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
  );

  if (inOverview) {
    // R5g (§6.7). The live Overview, full page: one slim toolbar, Filters in
    // place under it, and the panels across the page.
    const filterCount = overviewFilterCount(state);
    const periodText = noPeriod
      ? AS_OF_TODAY
      : (periodOptions(state).find((o) => o.value === (periodKey(state) ?? SAVED_PERIOD))
          ?.label ?? "");
    const summaryLine = overviewSummary({
      subject: subjectLabel(state.subject, answer),
      scope: scopePhrase(state.projectId, state.includeSubtree, scopes),
      period: periodText,
      updated: null,
    });
    return (
      <div className="space-y-3">
        <OverviewHeader
          summary={summaryLine}
          updated={
            live.updatedAt !== null || live.refreshFailed ? (
              <UpdatedAgo updatedAt={live.updatedAt} failed={live.refreshFailed} />
            ) : null
          }
          summaryTitle={
            preview ? periodLabel(preview.body.period_start, preview.body.period_end) : undefined
          }
          filtersOpen={filtersOpen}
          filtersId={controlsId}
          filtersButtonId={filtersButtonId}
          filterCount={filterCount}
          refreshing={live.refreshing}
          saveDisabled={prompt !== null}
          onToggleFilters={toggleFilters}
          onRefresh={() => ctl.refresh()}
          onSaveAs={() => onSaveAs?.(state)}
          onNewReport={onNewReport}
        />
        <OverviewFilters
          id={controlsId}
          labelledBy={filtersButtonId}
          open={filtersOpen}
          who={chipShown ? subjectChip : null}
          work={workControl}
          time={timeControl}
          sections={
            <fieldset className="min-w-0 space-y-2">
              <legend className="mb-1 text-xs font-medium text-muted-foreground">
                What to include
              </legend>
              {sectionGroups().map((group) => (
                <div key={group.label} className="flex flex-wrap items-center gap-1.5">
                  <span className="w-full text-xs text-muted-foreground sm:w-36">
                    {group.label}
                  </span>
                  {sectionToggles(group.sections, "chip")}
                </div>
              ))}
              {sectionNote && <p className="text-xs text-muted-foreground">{sectionNote}</p>}
            </fieldset>
          }
          resetShown={filterCount > 0}
          onReset={() => setState(overviewState())}
        />
        <section className="min-w-0" aria-label={OVERVIEW_NAME} aria-busy={live.refreshing || updating}>
          {previewBody}
        </section>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <BuilderHeader
        onBack={onCancel}
        title={
          <TitleField
            value={state.name}
            onChange={(name) => setState((s) => ({ ...s, name, nameTouched: true }))}
          />
        }
        actions={actions}
      />
      {(state.template || editing) && (
        <div className="flex flex-wrap items-center gap-2">
          {state.template && template && (
            <Badge tone="neutral" icon="FileText">
              From template: {template.name}
            </Badge>
          )}
          {editing && (
            <Badge tone="neutral" icon="Pencil">
              Editing a saved report
            </Badge>
          )}
        </div>
      )}
      <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)] xl:items-start">
        <div className="xl:sticky xl:top-0">{controls}</div>

        <section
          className="min-w-0 rounded-xl bg-muted/40 p-3 sm:p-4"
          aria-label="Preview"
        >
          <div className="mb-3 flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              {updating ? (
                <Icon name="Loader2" className="h-3 w-3 animate-spin" />
              ) : (
                <span
                  aria-hidden
                  className={`inline-block size-1.5 rounded-full ${statusAccent({ category: "done" }).dot}`}
                />
              )}
              <span role="status">{updating ? "Updating" : "Live preview"}</span>
            </span>
          </div>
          {previewBody}
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

/** One numbered step of "Build your report" (§6.6 B), a label over a control. */
function Step({
  n,
  label,
  children,
}: {
  n: number;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-2 text-xs font-medium text-foreground">
        <span
          aria-hidden
          className="inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary"
        >
          {n}
        </span>
        {label}
      </p>
      {children}
    </div>
  );
}

/**
 * The builder's one header row (§6.6 A): back, the title, and the actions.
 * The title is a heading in Overview and an input in the builder.
 */
export function BuilderHeader({
  onBack,
  title,
  actions,
}: {
  onBack?: () => void;
  title: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {onBack && (
        <Button variant="ghost" size="sm" icon="ArrowLeft" onClick={onBack}>
          Reports
        </Button>
      )}
      <div className="min-w-[12rem] flex-1">{title}</div>
      <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}

/**
 * The report name as an inline title (§6.6 A item 2). It is a labelled input
 * that looks like a heading, with a pencil on hover and on focus. The border
 * shows only on hover and on focus.
 */
export function TitleField({
  value,
  onChange,
}: {
  value: string;
  onChange: (name: string) => void;
}) {
  return (
    <label className="group flex min-w-0 items-center gap-1.5 [&_input]:border-transparent [&_input]:bg-transparent [&_input:hover]:border-border [&_input:focus]:border-primary/50">
      <span className="sr-only">Report name</span>
      <Input
        inputSize="lg"
        className="min-w-0 flex-1 font-semibold"
        value={value}
        maxLength={MAX_REPORT_NAME}
        onChange={(e) => onChange(e.target.value)}
      />
      <Icon
        name="Pencil"
        className="reveal-on-hover h-3.5 w-3.5 shrink-0 text-muted-foreground transition-opacity group-focus-within:opacity-100"
      />
    </label>
  );
}

/**
 * One section tile of "What to include" (§6.6 C, R5f round 2 item 7). One
 * line: the section's icon, its one name and a check. The short line is the
 * tooltip, and a screen reader reads it through `aria-describedby`. A tile
 * that a subject turns off is disabled and says why. The last chosen tile
 * stays on, because a report needs one section: it is `aria-disabled`, it
 * says why, and its click does nothing (item 10).
 */
export function SectionTile({
  sectionKey,
  label,
  on,
  blocked,
  last,
  onToggle,
  variant = "tile",
}: {
  sectionKey: string;
  label: string;
  on: boolean;
  blocked: boolean;
  last: boolean;
  onToggle: () => void;
  /**
   * WS-27bn R5g rule 3. `chip` is the small toggle of the Overview Filters.
   * It keeps every rule of the tile: `aria-pressed`, the blocked reason, and
   * the last chosen section `aria-disabled` with its reason.
   */
  variant?: "tile" | "chip";
}) {
  const describedBy = useId();
  const line = blocked
    ? "Off for a person or team."
    : last
      ? "A report needs at least one section."
      : PANEL_HINTS[sectionKey];
  if (variant === "chip") {
    return (
      <span className="inline-flex">
        <Button
          variant="secondary"
          size="none"
          radius="keep"
          layout="inline-flex items-center gap-1.5"
          className="rounded-full px-2.5 py-1 text-xs font-medium transition-colors"
          selected={on}
          disabled={blocked}
          aria-disabled={last ? "true" : undefined}
          aria-describedby={describedBy}
          title={line}
          onClick={last ? undefined : onToggle}
        >
          {/* A chosen chip swaps its icon for a check, so the width holds. */}
          <Icon
            name={on ? "Check" : sectionIcon(sectionKey)}
            className={`h-3.5 w-3.5 shrink-0 ${on ? "text-primary" : "text-muted-foreground"}`}
          />
          <span className="whitespace-nowrap">{label}</span>
        </Button>
        <span id={describedBy} className="sr-only">
          {line}
        </span>
      </span>
    );
  }
  // The hint span is a SIBLING of the button, not a child. A visually hidden
  // child still joins the accessible name, so a screen reader would read the
  // hint twice: once in the name and once as the description.
  return (
    <div className="min-w-0">
    <Button
      variant="secondary"
      size="none"
      layout="flex w-full items-center gap-2 text-left"
      className="min-w-0 rounded-lg px-2.5 py-1.5 transition-colors"
      selected={on}
      disabled={blocked}
      aria-disabled={last ? "true" : undefined}
      aria-describedby={describedBy}
      title={line}
      onClick={last ? undefined : onToggle}
    >
      <Icon
        name={sectionIcon(sectionKey)}
        className={`h-4 w-4 shrink-0 ${on ? "text-primary" : "text-muted-foreground"}`}
      />
      <span
        className={`min-w-0 flex-1 truncate pr-px text-xs font-medium ${on ? "text-foreground" : "text-foreground/80"}`}
      >
        {label}
      </span>
      <Icon
        name="Check"
        className={`h-3.5 w-3.5 shrink-0 text-primary ${on ? "" : "invisible"}`}
      />
    </Button>
    <span id={describedBy} className="sr-only">
      {line}
    </span>
    </div>
  );
}

/**
 * The one slim toolbar of the live Overview (WS-27bn R5g rule 2, §6.7). The
 * summary of the choices on the left. Filters, Refresh and "Save as report"
 * on the right. No menu and no dialog.
 */
export function OverviewHeader({
  summary,
  updated,
  summaryTitle,
  filtersOpen,
  filtersId,
  filtersButtonId,
  filterCount,
  refreshing,
  saveDisabled,
  onToggleFilters,
  onRefresh,
  onSaveAs,
  onNewReport,
}: {
  summary: string;
  /**
   * R5g round 1. "Updated …", as its own small component (`UpdatedAgo`).
   * Its 30-second clock then renders only this text, and not the body.
   */
  updated?: React.ReactNode;
  /** The dates of the period, as a tooltip on the summary. */
  summaryTitle?: string;
  filtersOpen: boolean;
  filtersId: string;
  filtersButtonId: string;
  /** How many choices differ from the Overview defaults. */
  filterCount: number;
  refreshing: boolean;
  saveDisabled: boolean;
  onToggleFilters: () => void;
  onRefresh: () => void;
  onSaveAs: () => void;
  onNewReport?: () => void;
}) {
  const changed = `${filterCount} ${filterCount === 1 ? "filter" : "filters"} changed`;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h3 className="text-base font-semibold text-foreground">{OVERVIEW_NAME}</h3>
        <p className="min-w-0 text-xs text-muted-foreground" title={summaryTitle}>
          {summary}
          {updated ? <> · {updated}</> : null}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          id={filtersButtonId}
          variant="secondary"
          size="sm"
          icon="SlidersHorizontal"
          aria-expanded={filtersOpen}
          aria-controls={filtersId}
          className={filtersOpen ? "border-primary/40 text-foreground" : ""}
          onClick={onToggleFilters}
        >
          Filters
          {filterCount > 0 && (
            <Badge tone="primary" className="ml-0.5 tabular-nums">
              <span aria-hidden>{filterCount}</span>
              <span className="sr-only">{changed}</span>
            </Badge>
          )}
        </Button>
        {/* R5g round 1. Never disabled while it loads, so it keeps the
            focus. The spinner and aria-busy say it works, and the
            controller ignores a second click. */}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh"
          title="Refresh"
          aria-busy={refreshing}
          onClick={onRefresh}
        >
          <Icon
            name={refreshing ? "Loader2" : "RefreshCw"}
            size={14}
            className={refreshing ? "animate-spin" : undefined}
          />
        </Button>
        <Button variant="secondary" size="sm" icon="Save" disabled={saveDisabled} onClick={onSaveAs}>
          Save as report
        </Button>
        {onNewReport && (
          <Button variant="primary" size="sm" icon="Plus" onClick={onNewReport}>
            New report
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * The Overview Filters, open in place under the toolbar (WS-27bn R5g rule 3).
 * The three choices sit in one row that wraps, and the sections are chips
 * under it. The builder's own controls fill it, so the logic is not forked.
 * It stays in the page when closed, as `hidden`, so the button's
 * `aria-controls` names a real element.
 */
export function OverviewFilters({
  id,
  labelledBy,
  open,
  who,
  work,
  time,
  sections,
  resetShown,
  onReset,
}: {
  id: string;
  labelledBy: string;
  open: boolean;
  who: React.ReactNode;
  work: React.ReactNode;
  time: React.ReactNode;
  sections: React.ReactNode;
  resetShown: boolean;
  onReset: () => void;
}) {
  return (
    <div
      id={id}
      role="region"
      aria-labelledby={labelledBy}
      hidden={!open}
      className="space-y-3 rounded-xl border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        {who && <FilterField label="Who is it about?">{who}</FilterField>}
        <FilterField label="Which work?">{work}</FilterField>
        <FilterField label="What time?">{time}</FilterField>
        {resetShown && (
          <div className="ml-auto self-end">
            <Button variant="text" size="sm" icon="RotateCcw" onClick={onReset}>
              Reset
            </Button>
          </div>
        )}
      </div>
      <div className="border-t border-border pt-3">{sections}</div>
    </div>
  );
}

/**
 * "Updated 2 min ago" (R5g round 1, item 5). It holds its own 30-second
 * clock, which stops while the tab is hidden, so only this text renders
 * again and not the panels.
 */
export function UpdatedAgo({
  updatedAt,
  failed,
  env = browserTimerEnv,
}: {
  updatedAt: number | null;
  failed: boolean;
  env?: () => LiveTimerEnv;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const e = env();
    const tick = () => setNow(e.now());
    return startTicker(e, { intervalMs: 30_000, onTick: tick, onShow: tick });
  }, [env]);
  // A new answer can be newer than the clock. Its age is then zero.
  const text = failed ? "Refresh failed" : updatedLine(updatedAt, Math.max(now, updatedAt ?? 0));
  return text ? <span>{text}</span> : null;
}

/** One labelled choice of the Overview Filters. */
function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="w-full space-y-1 sm:w-56">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {children}
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
  /**
   * R5f round 1, rule 6. Overview's choices and its last preview, kept here.
   * A member who opens the builder or a saved report and comes back finds
   * Overview as it was, and Overview asks the server nothing new.
   */
  const [overviewDraft, setOverviewDraft] = useState<BuilderState>(() => overviewState());
  const [overviewPreview, setOverviewPreview] = useState<{
    key: string;
    body: PreviewReportBody;
    /** R5g. When the server answered, for "Updated …" after a return. */
    at: number;
  } | null>(null);

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
    if (!linkQuery) return;
    // The tree must arrive too: the node is kept only when it is in it. A
    // failed catalogue read drops the link, so the pane never waits for ever.
    const step = linkStep({
      hasTemplates: templates !== undefined,
      catalogueFailed: Boolean(catalogue.error),
      hasTree: tree.data !== undefined,
      treeFailed: Boolean(tree.error),
    });
    if (step === "wait") return;
    const params = new URLSearchParams(linkQuery);
    const intent =
      step === "read" && templates !== undefined ? parseReportLink(params, templates, roots) : null;
    // A link is consumed by setting state once, as the page's `?app=` does.
    /* eslint-disable react-hooks/set-state-in-effect */
    if (intent) setPane({ kind: "new", initial: builderStateFromLink(intent) });
    /* eslint-enable react-hooks/set-state-in-effect */
    for (const k of REPORT_LINK_KEYS) params.delete(k);
    const qs = params.toString();
    router.replace(qs ? `/projects?${qs}` : "/projects");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkQuery, templates, roots, tree.error, catalogue.error]);

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
  // R5f round 1, rule 7. A builder link decides the pane before Overview
  // mounts, so Overview sends no preview for it.
  const shown = reportsPane({ building, selected, linkPending: linkQuery !== "" });
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
    // §6.6 E item 1. No heading here: the page header names the app.
    <div className="flex-1 overflow-y-auto p-4">
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

        <div className="min-w-0">
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
          ) : shown === "wait-for-link" ? (
            <SkeletonRows count={4} />
          ) : shown === "overview" ? (
            // R5f (§6.1). Home is Overview, then the gallery, then "Your
            // reports". Overview writes no row. "Save as report" opens the
            // builder with its choices.
            <div className="space-y-6">
              <ReportBuilder
                key="reportsOverview"
                mode="reportsOverview"
                initial={overviewDraft}
                initialPreview={overviewPreview}
                onDraft={setOverviewDraft}
                editing={null}
                roots={roots}
                templates={templates}
                onSaved={saved}
                onDeleted={deleted}
                onCancel={() => undefined}
                onSaveAs={(state) =>
                  setPane({ kind: "new", initial: saveAsReportState(state) })
                }
                onNewReport={() => setPane({ kind: "new", initial: newBuilderState() })}
                onPreview={(preview, key, at) => {
                  setOverviewPreview({ key, body: preview, at });
                  setOverviewFinished(preview.sections.finished?.total_completed ?? null);
                }}
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
              {/* §6.6 D item 1. A soft surface, so the report reads like a page. */}
              <div className="rounded-xl bg-muted/40 p-3 sm:p-4">
                <RenderedBody body={body} headerLine={header} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
