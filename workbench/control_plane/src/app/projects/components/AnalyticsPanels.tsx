"use client";

/**
 * WS-27bk §9.12.7 — the owner's analytics questions, rendered.
 *
 * Three endpoints existed and NOTHING drew them. `/analytics/stuck`,
 * `/analytics/load` and `/analytics/throughput` all shipped with tests and no
 * surface, and the header of the Analytics view (deleted in WS-27bn R5f)
 * recorded the gap it left: *"a
 * chart drawn from data we do not have would be an invented trend line. When
 * a time-series endpoint exists, that section slots in below the table."*
 * This is that section, plus the two panels beside it.
 *
 * ⚠️ **Nothing here computes a metric.** Every number arrives finished from
 * the server, and the only arithmetic below turns a count into a bar WIDTH.
 * The rule is `analytics.py`'s and it is not a preference: the task list is
 * paginated, so a total taken in the browser is a total of one page. It looks
 * right, and nothing on the way says otherwise.
 *
 * ⚠️ **No chart library.** Same reasoning the old Analytics view used for
 * dropping TanStack (its table is `SpaceSummary.tsx` since WS-27bn R5f): one screen of bars is not worth a dependency, and every charting
 * package brings its own palette — which is the second colour vocabulary
 * `AGENTS.md` rule 1 refuses. Bars are flex children with a percentage width,
 * so they inherit the one look for free.
 *
 * Hues come from the two seams and from nowhere else: `statusAccent` for
 * anything that means a task state, `accentForSlot` for the ageing bands,
 * which are categorical rather than stateful.
 *
 * ⚠️ Every number carries a `title`. That is the owner's ask of 2026-09-16
 * (*"what are these numbers here?"*) and `countTooltips.test.ts` fails the
 * build for a bare one.
 */
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";

import { ControlLink } from "@/components/ControlLink";
import Icon from "@/components/Icon";
import { PersonName } from "@/components/PersonName";
import Button from "@/components/ui/Button";
import { InfoTip } from "@/components/ui/InfoTip";
import { personDisplayName } from "@/lib/personName";

import {
  type AccentHue,
  accentForHue,
  statusAccent,
} from "@/lib/statusAccent";

import { PILL_HUE, PILL_LABEL } from "@/app/people/lib/dashboard";

import type {
  AgentRunsReport,
  CapacityReport,
  ConflictsReport,
  FinishedReport,
  HygieneReport,
  LoadReport,
  OutlookReport,
  PulseReport,
  RebalanceReport,
  StuckReport,
  ThroughputReport,
  WaitingReport,
  WhoDidTheWorkReport,
} from "../lib/api";
import { asList, bandCount, staleBands } from "../lib/analyticsRead";
import {
  capacityRows,
  committedBar,
  hoursLine,
  rowLabel,
  rowWarnings,
  skillLine,
  windowsLine,
} from "../lib/capacity";
import {
  KIND_LABEL,
  PANEL_ROWS,
  capNote,
  conflictRows,
  countsLine,
  rowPeople,
  severityHue,
  windowLine,
} from "../lib/conflicts";
import { effortDisplay, personEffort } from "../lib/effort";
import {
  HYGIENE_KINDS,
  OVERLAP_NOTE,
  TITLES_SHOWN,
  hygieneCount,
  hygieneRows,
  kindTitle,
  moreNote,
} from "../lib/hygiene";
import {
  type OutlookLine,
  type SlipRange,
  type Tone,
  capacityLine,
  forecastGap,
  headlineVerdict,
  isDrawableOutlook,
  peopleLine,
  slipRange,
  velocityLine,
} from "../lib/outlook";
import {
  ACTOR_SEGMENTS,
  WHO_DID_THE_WORK,
  actorAccent,
  hiddenMembersLine,
  memberAgentLine,
} from "../lib/kpiStrip";
import { FINISHED_HINT_LEAD, PANEL_HINTS, WHO_HINT_LEAD } from "../lib/panelHints";
import { CAPACITY_HR_HINT, CONFLICTS_HR_HINT } from "../lib/hrHints";
import { sectionName } from "../lib/reportBuilder";
import { usePersonNames } from "../lib/usePersonNames";
import { SECTION_CLEAR_LINES } from "../lib/sectionEmpty";
import {
  focusMore,
  focusShown,
  focusWhy,
  helpLine,
  emptyPanelLine,
  hiddenPeopleLine,
  loadBar,
  pulseName,
  pulseRows,
  statusMark,
} from "../lib/pulse";
import {
  AGENT_RUN_RULES,
  LONG_SIZE,
  PANEL_RUN_ROWS,
  RUN_OUTCOME_SEGMENTS,
  RUN_SIZES,
  longLines,
  outcomeAccent,
  peakDay,
  silentLines,
  successFigure,
  successTrend,
} from "../lib/agentRunsReport";
import {
  type WaitingKind,
  type WaitingLine,
  answerLine,
  listRows,
  waitingLists,
} from "../lib/waiting";
import {
  REBALANCE_HR_HINT,
  helpersLine,
  holderLine,
  pickupLine,
  rebalanceCapNote,
  rebalancePickups,
  rebalanceTasks,
} from "../lib/rebalance";

/**
 * The ageing bands, in the order the server sends them.
 *
 * Two labels each, and both are load-bearing. `short` is what fits a
 * four-column legend at desktop width — photographed 2026-09-16, the long
 * form rendered as "Under …", "7 to 14 …", "Over 3…", which is a legend
 * that labels nothing. `full` is what the tooltip says, so the meaning is
 * one hover away and never lost.
 *
 * ⚠️ **`hue` is ORDINAL, and the first cut of this got it wrong.** The
 * bands went through `accentForSlot`, the categorical ramp — which painted
 * "14 to 30 days" GREEN and "over 30 days" violet. Green means fine
 * everywhere else in this product, so the third-worst band read as the
 * healthy one. Ageing is a severity scale, not a set of unrelated
 * categories, and the ramp is only correct for the latter. These four rise
 * monotonically: neutral, noted, warning, wrong.
 */
const BANDS: { key: string; short: string; full: string; hue: AccentHue }[] = [
  { key: "under_7d", short: "< 7d", full: "Under 7 days", hue: "gray" },
  { key: "days_7_to_14", short: "7–14d", full: "7 to 14 days", hue: "blue" },
  { key: "days_14_to_30", short: "14–30d", full: "14 to 30 days", hue: "amber" },
  { key: "over_30d", short: "> 30d", full: "Over 30 days", hue: "red" },
];

/**
 * A window, as something a person reads at a glance.
 *
 * ⚠️ ISO dates are what the SERVER sends and what a report must quote, but
 * "2026-06-29 to 2026-09-20" broke after "2026-09-" in a narrow panel — a date
 * split across two lines. This shortens it and keeps the year once.
 *
 * Formatted from the ISO string by hand rather than through `new Date()`:
 * these are floating calendar dates, and `new Date("2026-09-20")` reads them
 * as midnight UTC and moves them a day west of Greenwich. The same trap
 * `TaskRow.due_on` carries.
 */
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/**
 * "2026-09-20" → "20 Sep 2026". Anything that is not a YYYY-MM-DD string
 * gives "" and never throws: a payload with no `period_start` threw here and
 * took the whole Analytics page down (S12 visual review, 2026-09-28).
 */
export function day(iso: unknown): string {
  const m = typeof iso === "string" ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null;
  if (!m || !MONTHS[Number(m[2]) - 1]) return "";
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

/** "29 Jun – 20 Sep 2026", or "this period" when either end is missing. */
export function period(from: unknown, to: unknown): string {
  const a = day(from);
  const b = day(to);
  if (!a || !b) return "this period";
  // One year, said once: "29 Jun – 20 Sep 2026".
  const ya = a.slice(a.lastIndexOf(" "));
  return ya === b.slice(b.lastIndexOf(" "))
    ? `${a.slice(0, a.lastIndexOf(" "))} – ${b}`
    : `${a} – ${b}`;
}

/** Hours, as something a person reads without converting it. */
function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "—";
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

/**
 * The line of a rebalance section with no task at risk and no pickup, when
 * somebody is idle. "Nobody is idle" would then be false, so the panel says
 * who is idle and why no pickup shows. `null` when nobody is idle.
 */
export function idleLine(idle: number | null | undefined): string | null {
  if (typeof idle !== "number" || idle <= 0) return null;
  return idle === 1
    ? "Nothing is at risk. 1 idle person has no task that fits."
    : `Nothing is at risk. ${idle} idle people have no task that fits.`;
}

/**
 * What a report adds to a panel's card (WS-27bn R5f round 1, §6.6 D).
 *
 * `RenderedBody` wraps each panel in a provider. The panel then shows the
 * section's icon beside its title, and a small table button in its header.
 * The table opens inside the same card. A panel outside a report, such as
 * one on `NodeDashboard`, gets no provider and draws as before.
 */
export interface PanelChrome {
  /** The section's icon, from `lib/sectionIcons.ts`. */
  icon?: string;
  /** The section as a table. Absent, and the header has no table button. */
  table?: React.ReactNode;
  /**
   * WS-27bn R5g round 1. The card fills the height of its cell, so two
   * cards in one row of the live Overview end at one line.
   */
  fill?: boolean;
}

export const PanelChromeContext = createContext<PanelChrome | null>(null);

function Panel({
  title,
  hint,
  hintTitle,
  children,
}: {
  title: string;
  /** What the panel MEANS. The owner asked for exactly this. */
  hint: string;
  /**
   * A second fact about the panel, as the hint's tooltip (`projects_reports.md`
   * §6.5 item 17). `pulse` puts its `help_note` here.
   */
  hintTitle?: string | null;
  children: React.ReactNode;
}) {
  // WS-27bn R2b. The title names the region, so a screen reader announces
  // "What we finished, region" and not an anonymous section. `useId` keeps
  // two panels of one kind on one page from sharing an id.
  const titleId = useId();
  const chrome = useContext(PanelChromeContext);
  const [tableOpen, setTableOpen] = useState(false);
  // R5f round 2 (item 13). React 19 writes `hidden` as a boolean, so the
  // value `until-found` is set on the element here. Find-in-page then reaches
  // a closed table, and the browser opens it with `beforematch`.
  const tableRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = tableRef.current;
    if (!el) return;
    if (tableOpen) {
      el.removeAttribute("hidden");
      return;
    }
    el.setAttribute("hidden", "until-found");
    const found = () => setTableOpen(true);
    el.addEventListener("beforematch", found);
    return () => el.removeEventListener("beforematch", found);
  }, [tableOpen]);
  return (
    <section
      className={`rounded-lg border border-border bg-card p-3 ${chrome?.fill ? "h-full" : ""}`}
      aria-labelledby={titleId}
    >
      <header className="mb-3 flex items-start gap-2">
        {chrome?.icon ? (
          <Icon name={chrome.icon} className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        ) : null}
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="text-xs font-semibold text-foreground">
            {title}
          </h3>
          <p
            className="mt-0.5 text-[11px] text-muted-foreground"
            title={hintTitle ?? undefined}
          >
            {hint}
          </p>
        </div>
        {chrome?.table ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon="Table2"
            aria-expanded={tableOpen}
            aria-label={tableOpen ? "Hide the table" : "Show as a table"}
            title={tableOpen ? "Hide the table" : "Show as a table"}
            onClick={() => setTableOpen((o) => !o)}
          />
        ) : null}
      </header>
      {children}
      {chrome?.table ? (
        // The server render writes `hidden=""`. The effect above turns it
        // into `until-found` in the browser. ⚠️ Keep this div BARE:
        // `until-found` does not set `display: none` (Tailwind's preflight
        // excludes it), so a border, margin or padding here still paints
        // while the table is closed. The box lives on the child.
        <div ref={tableRef} hidden={!tableOpen}>
          <div className="mt-3 border-t border-border pt-3">{chrome.table}</div>
        </div>
      ) : null}
    </section>
  );
}

/**
 * One figure in a tile, and never a blank. THE tile (WS-27bn R2b).
 *
 * The old Analytics view, `NodeDashboard` and the report's summary row each
 * held a copy until R2b. Since WS-27bn R5f that view is gone, and
 * `SpaceSummary` and `RenderedBody` draw its tiles with this one. This is the `NodeDashboard` copy, which already took a
 * `title`, and one more prop: `display`, for a figure that is words, such as
 * a median that reads "3d" or "not measured".
 *
 * ⚠️ `value` is typed as a number and is not guaranteed to be one. `api.call`
 * parses JSON and casts it, so a roll-up that omits a count arrives as
 * `undefined`, and `{undefined}` renders NOTHING. Measured 2026-09-03: two of
 * the five tiles were a heading over empty space. A tile with no figure reads
 * as a number that FAILED, not as a zero, so an unavailable figure is an
 * explicit dash that says so.
 */
export function Stat({
  label,
  value,
  display,
  tone,
  title,
  detail,
  help,
  className = "",
}: {
  label: string;
  value?: number;
  /** The figure as words. It wins over `value` when both are given. */
  display?: string;
  tone?: string;
  /** What this figure counts. The dash below still wins when it is absent. */
  title?: string;
  /**
   * WS-27bn RA-2. Short lines under the figure, such as the split of people
   * and agents. Each line carries the tile's `title`, so a hover anywhere on
   * the tile gives its rule.
   */
  detail?: readonly string[];
  /**
   * WS-27bn RA-2. The tile's rule behind an `InfoTip` beside the label, so a
   * keyboard and a touch reach it too. A `title` reaches a mouse only.
   */
  help?: string;
  className?: string;
}) {
  const known =
    typeof display === "string" ||
    (typeof value === "number" && Number.isFinite(value));
  return (
    <div className={`rounded-lg border border-border bg-card px-3 py-2 ${className}`}>
      {help ? (
        <div className="flex items-start justify-between gap-1">
          <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
          <InfoTip label={`How ${label} is counted`} title={label}>
            {help}
          </InfoTip>
        </div>
      ) : (
        <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      )}
      <p
        className={`text-lg font-semibold ${known ? (tone ?? "text-foreground") : "text-muted-foreground"}`}
        title={known ? title : `${label} did not come back from the server`}
      >
        {known ? (display ?? value) : "—"}
      </p>
      {known &&
        detail?.map((line) => (
          <p key={line} className="text-[11px] text-muted-foreground" title={title}>
            {line}
          </p>
        ))}
    </div>
  );
}

/**
 * One horizontal bar built from segments.
 *
 * ⚠️ A zero-width segment is DROPPED rather than rendered at 0%. A 0%-wide
 * div still draws its border in some engines, which puts a hairline of the
 * wrong colour on the bar and reads as a real value.
 *
 * WS-27bn R2b. An ABSENT segment is dropped too: a report body from an older
 * server may lack a bucket, and "undefined" is not a width. The bar carries
 * its figures as an accessible name, so a screen reader gets the values and
 * not only a picture. `emptyTitle` exists because "Nothing in this scope"
 * is wrong for a person with zero working hours and committed work.
 */
function Bar({
  segments,
  total,
  emptyTitle = "Nothing in this scope yet",
}: {
  segments: { key: string; value: number | undefined; dot: string; label: string }[];
  total: number;
  emptyTitle?: string;
}) {
  if (!(total > 0)) {
    return (
      <div
        className="h-2 w-full rounded-full bg-muted"
        role="img"
        aria-label={emptyTitle}
        title={emptyTitle}
      />
    );
  }
  const drawn = segments.filter(
    (s): s is typeof s & { value: number } =>
      typeof s.value === "number" && s.value > 0
  );
  return (
    <div
      className="flex h-2 w-full overflow-hidden rounded-full bg-muted"
      role="img"
      aria-label={
        drawn.length > 0
          ? drawn.map((s) => `${s.label}: ${s.value} of ${total}`).join(", ")
          : `None of ${total}`
      }
    >
      {drawn
        .map((s) => (
          <div
            key={s.key}
            className={s.dot}
            style={{ width: `${(s.value / total) * 100}%` }}
            title={`${s.label}: ${s.value} of ${total}`}
          />
        ))}
    </div>
  );
}

/** (a) Where is work stuck? */
export function StuckPanel({ data }: { data: StuckReport }) {
  // ⚠️ WS-27bn R2b. A report body from a server before R3a has no ageing
  // bands. Without `stale` the panel draws no band chart, no band legend,
  // and never "No open work in this scope". It was not sent the open work,
  // so it cannot say there is none. Since R3a a report sends the bands.
  const hasBands = data?.stale !== undefined && data?.stale !== null;
  // ⚠️ The server sends `stale` as a LIST of {band, n}. This read asked
  // `b.key in data.stale`, which on an array tests INDICES — always false —
  // so the histogram drew an empty bar and no legend at all. `staleBands`
  // normalises either shape, and `analyticsRead.test.ts` pins both.
  const sent = staleBands(data.stale);
  const bands = BANDS.filter((b) => sent.some((x) => x.key === b.key));
  const staleTotal = bands.reduce((n, b) => n + bandCount(sent, b.key), 0);
  const overdueAccent = statusAccent({ category: "cancelled" });

  return (
    <Panel
      title={sectionName("stuck")}
      hint={
        // WS-27bn R3a. A report sends the bands and no blocked list, so its
        // hint does not name what the panel cannot show.
        hasBands && typeof data.blocked_total === "number"
          ? "Open tasks by how long they have sat without a change, what is blocked, and what is past due."
          : hasBands
            ? PANEL_HINTS.stuck
            : "Open tasks past their due date, by project."
      }
    >
      {hasBands && (
      <>
      {/* ⚠️ A ONE-BAND BAR IS NOT A CHART. Photographed 2026-09-17: every
          open task sat in `< 7d`, so this drew a featureless grey slab that
          reads as a loading skeleton — the reader learns nothing and cannot
          tell the panel from a broken one. A proportional bar earns its space
          only when there is a proportion. */}
      {bands.filter((b) => bandCount(sent, b.key) > 0).length > 1 ? (
        <Bar
          total={staleTotal}
          segments={bands.map((b) => ({
            key: b.key,
            value: bandCount(sent, b.key),
            dot: accentForHue(b.hue).dot,
            label: b.full,
          }))}
        />
      ) : staleTotal > 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {(() => {
            const only = bands.find((b) => bandCount(sent, b.key) > 0);
            return only
              ? `All ${staleTotal} open ${staleTotal === 1 ? "task" : "tasks"} were touched ${only.full.toLowerCase()} ago.`
              : null;
          })()}
        </p>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          No open work in this scope.
        </p>
      )}
      {/* ⚠️ TWO columns at every width. Four fitted while this panel was
          one of three; the fourth panel narrowed them all, and each label
          then broke across two lines ("<  7d"). A legend that wraps
          mid-label is harder to read than one that takes a second row. */}
      <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        {bands.map((b) => (
          <li
            key={b.key}
            className="flex min-w-0 items-center gap-1.5 text-[11px]"
            title={`${bandCount(sent, b.key)} open tasks last changed ${b.full.toLowerCase()} ago`}
          >
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${accentForHue(b.hue).dot}`}
              aria-hidden
            />
            <span className="text-muted-foreground">{b.short}</span>
            <span className="ml-auto font-medium tabular-nums">
              {bandCount(sent, b.key)}
            </span>
          </li>
        ))}
      </ul>
      </>
      )}

      {!hasBands && !(Array.isArray(data.overdue) && data.overdue.length > 0) && (
        <p className="text-[11px] text-muted-foreground">
          Nothing is past its due date in this scope.
        </p>
      )}

      {(data.blocked_total ?? 0) > 0 && asList(data.blocked).length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <p className="mb-1 text-[11px] text-muted-foreground">
            {/* ⚠️ "N of M", never a bare N. The list is capped at 20, and a
                lone count would read as the whole of it. */}
            <span
              title={`${asList(data.blocked).length} shown of ${data.blocked_total} tasks blocked by work that is not finished`}
            >
              Blocked: {asList(data.blocked).length} of {data.blocked_total}
            </span>
          </p>
          <ul className="space-y-0.5">
            {asList<{id: string; title: string; task_number: number | null; due_at: string | null}>(data.blocked).slice(0, 5).map((t) => (
              <li
                key={t.id}
                className="truncate text-[11px]"
                title={`${t.title} — blocked by an unfinished task`}
              >
                {t.title}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ⚠️ `Array.isArray`, not a truthiness check. This field WAS a
          number, and a `.length` on one is undefined rather than an error —
          which is how the section went missing without anybody noticing. */}
      {Array.isArray(data.overdue) && data.overdue.length > 0 && (
        <div className={hasBands ? "mt-3 border-t border-border pt-2" : ""}>
          <p
            className="mb-1 text-[11px] text-muted-foreground"
            title={`${data.overdue_total} open tasks are past their due date, across ${data.overdue.length} project${data.overdue.length === 1 ? "" : "s"}`}
          >
            Overdue · {data.overdue_total}
          </p>
          <ul className="space-y-0.5">
            {data.overdue.slice(0, 5).map((p) => (
              <li
                key={p.project_id}
                className="flex items-center gap-2 text-[11px]"
              >
                <span className="truncate">{p.name}</span>
                <span
                  className={`ml-auto font-medium tabular-nums ${overdueAccent.text}`}
                  title={`${p.overdue} open tasks in ${p.name} are past their due date`}
                >
                  {p.overdue}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

/**
 * WS-27bn R5a. "This view hides N other people", under a panel whose rows
 * the server filtered for this reader. The server removes the rows. The
 * panel only prints the line that counts them, and nothing for an admin.
 */
function HiddenPeople({ n }: { n: number | null | undefined }) {
  const line = hiddenPeopleLine(n, "view");
  if (!line) return null;
  return (
    <p
      className="mt-2 text-[11px] text-muted-foreground"
      title="You see your own row, and the rows of the members of the teams you lead. An admin sees every row. The totals still count everybody."
    >
      {line}
    </p>
  );
}

/** (b) Who is overloaded? */
export function LoadPanel({ data }: { data: LoadReport }) {
  // ⚠️ `asList` at the boundary, not `Array.isArray` at each use site. Four
  // separate defects in this pane have been a typed-as-present field arriving
  // absent or as the wrong shape — `api.call` casts rather than validates, so
  // the interface is a claim about the server and not a check on it. Measured
  // 2026-09-17: `{people: null}` from this endpoint crashed the whole PAGE.
  const people = asList<LoadReport["people"][number]>(data?.people);
  // The payload carries addresses. The directory names them (owner,
  // 2026-10-10: names, not email IDs), and the address goes in the hover.
  const names = usePersonNames(people.map((row) => row.assignee));
  const overdue = accentForHue("red");
  const soon = accentForHue("amber");
  const later = accentForHue("gray");

  return (
    <Panel
      title={sectionName("load")}
      hint={PANEL_HINTS.load}
    >
      {people.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {emptyPanelLine(data?.hidden_people, SECTION_CLEAR_LINES.load)}
        </p>
      ) : (
        <>
          <ul className="space-y-2">
            {people.map((row) => (
              <li key={row.assignee ?? "__unassigned"}>
                <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                  {row.assignee ? (
                    // The name, never the address (owner, 2026-10-10). The
                    // address is in the hover, with the full name.
                    <PersonName
                      email={row.assignee}
                      name={row.name || names.get(row.assignee.toLowerCase())}
                      className="min-w-0 pr-px"
                    />
                  ) : (
                    <span
                      // ⚠️ `pr-px` is not a nicety. Photographed 2026-09-16,
                      // "Unassigned" rendered as "Unassignea": an italic glyph
                      // leans past its own advance width, and `truncate`'s
                      // overflow clip cut the final letter at EVERY width.
                      className="min-w-0 truncate pr-px italic text-muted-foreground"
                      title="Open work with nobody assigned. On a real board this is usually the largest bar."
                    >
                      Unassigned
                    </span>
                  )}
                  {/* ⚠️ Hours ONLY when this plate is sized. `personEffort`
                      answers null otherwise — nine unsized tasks is not zero
                      hours of work, and "0h" beside real figures says it is. */}
                  {(() => {
                    const eff = personEffort(row);
                    if (!eff) return null;
                    return (
                      <span
                        className="ml-auto shrink-0 tabular-nums text-muted-foreground"
                        title={
                          eff.cover.complete
                            ? `${eff.label} estimated across all ${row.open_tasks} open tasks. An estimate — this product records no hours worked.`
                            : `${eff.label} estimated across ${eff.cover.estimated} of ${row.open_tasks} open tasks. The other ${eff.cover.missing} carry no estimate, so the real figure is higher.`
                        }
                      >
                        {eff.label}
                        {!eff.cover.complete && (
                          <span className="opacity-60">*</span>
                        )}
                      </span>
                    );
                  })()}
                  <span
                    className={`${personEffort(row) ? "" : "ml-auto "}shrink-0 font-medium tabular-nums`}
                    title={
                      // WS-27bn R2b: a bucket the server did not send is
                      // left out, never printed as "undefined".
                      [
                        `${row.open_tasks} open tasks: ${row.overdue} overdue`,
                        ...(typeof row.due_next_7d === "number"
                          ? [`${row.due_next_7d} due in the next 7 days`]
                          : []),
                        ...(typeof row.later === "number"
                          ? [`${row.later} later or undated`]
                          : []),
                      ].join(", ")
                    }
                  >
                    {row.open_tasks}
                  </span>
                </div>
                <div className="mt-1">
                  <Bar
                    total={row.open_tasks}
                    segments={[
                      {
                        key: "overdue",
                        value: row.overdue,
                        dot: overdue.dot,
                        label: "Overdue",
                      },
                      {
                        key: "soon",
                        value: row.due_next_7d,
                        dot: soon.dot,
                        label: "Due in the next 7 days",
                      },
                      {
                        key: "later",
                        value: row.later,
                        dot: later.dot,
                        label: "Later, or with no due date",
                      },
                    ]}
                  />
                </div>
              </li>
            ))}
          </ul>
          <p
            className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground"
            // ⚠️ The rows deliberately sum to MORE than this. A task with two
            // assignees is on both plates and counts for both, so the total is
            // counted over tasks. Saying so is cheaper than a support question.
            title="Counted over tasks. A task assigned to two people appears in both of their bars, so the bars add up to more than this number."
          >
            {data.total_tasks} open{" "}
            {data.total_tasks === 1 ? "task" : "tasks"}
            {/* WS-27bn R2b: a report does not send `people_total`. */}
            {typeof data.people_total === "number" &&
              ` across ${data.people_total} ${data.people_total === 1 ? "person" : "people"}`}
          </p>
          <EffortLine data={data} />
        </>
      )}
      <HiddenPeople n={data?.hidden_people} />
    </Panel>
  );
}

/**
 * WS-27bm S7a — who has the hours for the open work, beside Load.
 *
 * ⚠️ **Draws the route and counts nothing** (`projects_ai_chat.md` §10.3
 * item 8). Every figure is the server's, and `lib/capacity.ts` holds only the
 * words. The pill is the People dashboard's pill, in the People dashboard's
 * hues (`PILL_HUE`), so one pill never wears two colours in two apps.
 *
 * ⚠️ **The hours are HR tier.** Without `admin:members:read` the server sends
 * the task half only, and the panel says so in one line instead of drawing
 * empty hours that would read as "free".
 */
export function CapacityPanel({ data }: { data: CapacityReport }) {
  const rows = capacityRows(data);
  const horizonDays = data?.windows?.horizon?.days ?? data?.horizon_days ?? 14;

  return (
    <Panel
      title={sectionName("capacity")}
      hint={PANEL_HINTS.capacity}
    >
      {rows.length <= 1 && (rows[0]?.open_tasks ?? 0) === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {emptyPanelLine(data?.hidden_people, SECTION_CLEAR_LINES.load)}
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => {
            const line = hoursLine(row, horizonDays);
            const committed = committedBar(row, horizonDays);
            const warnings = rowWarnings(row, data?.windows?.horizon?.starts_on);
            const skills = skillLine(row);
            const pill = row.pill;
            const accent = pill ? accentForHue(PILL_HUE[pill]) : null;
            return (
              <li key={row.assignee ?? "__unassigned"}>
                <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                  {row.assignee && row.kind === "person" ? (
                    // The name, never the address (owner, 2026-10-10).
                    <PersonName email={row.assignee} name={row.name} className="min-w-0 pr-px" />
                  ) : (
                    <span
                      // `pr-px`: an italic glyph leans past its advance width,
                      // and `truncate` clips it. The Load panel learned this.
                      className="min-w-0 truncate pr-px italic text-muted-foreground"
                      title="Open work with nobody assigned. Nobody's hours cover it."
                    >
                      {rowLabel(row)}
                    </span>
                  )}
                  {pill && accent && (
                    <span
                      className="flex shrink-0 items-center gap-1 text-muted-foreground"
                      title={row.pill_reason ?? PILL_LABEL[pill]}
                    >
                      <span
                        className={`size-1.5 rounded-full ${accent.dot}`}
                        aria-hidden
                      />
                      {PILL_LABEL[pill]}
                    </span>
                  )}
                  {line && (
                    <span
                      className="ml-auto shrink-0 tabular-nums text-muted-foreground"
                      title={line.title}
                    >
                      {line.text}
                    </span>
                  )}
                  <span
                    className={`${line ? "" : "ml-auto "}shrink-0 font-medium tabular-nums`}
                    title={`${row.open_tasks} open tasks in this scope, ${row.overdue} overdue. The same count the Load panel shows.`}
                  >
                    {row.open_tasks}
                  </span>
                </div>
                {/* WS-27bn R2b. Committed hours of working hours in the
                    horizon, with the HR half and `hours_basis` only. The
                    text states both figures, because a bar clips above
                    100 percent. `committedBar` decides, and counts nothing. */}
                {committed && (
                  <div className="mt-1 flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <Bar
                        total={committed.of}
                        emptyTitle={committed.emptyTitle}
                        segments={[
                          {
                            key: "committed",
                            value: committed.value,
                            dot: accentForHue(committed.hue).dot,
                            label: "Committed hours",
                          },
                        ]}
                      />
                    </div>
                    <span
                      className="shrink-0 text-[10px] tabular-nums text-muted-foreground"
                      title={committed.title}
                    >
                      {committed.text}
                    </span>
                  </div>
                )}
                {(skills || warnings.length > 0) && (
                  <p
                    className="mt-0.5 truncate text-[10px] text-muted-foreground"
                    title={[...warnings, ...(skills ? [`Skills: ${skills}`] : [])].join(" · ")}
                  >
                    {[...warnings, ...(skills ? [skills] : [])].join(" · ")}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {/* The windows name what the HOURS measured, so they print only
          beside hours. Without the grant there are none to explain. */}
      {data?.hr_visible === false ? (
        <p className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground">
          {CAPACITY_HR_HINT}
        </p>
      ) : (
        <p
          className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground"
          title="The pill compares this Monday-to-Sunday week with the contracted week. Spare hours and at-risk tasks read the horizon."
        >
          {windowsLine(data)}
        </p>
      )}
      <HiddenPeople n={data?.hidden_people} />
    </Panel>
  );
}

/**
 * WS-27bm S7c — where the plan interferes with itself, beside Capacity.
 *
 * ⚠️ **Draws the route and counts nothing** (`projects_ai_chat.md` §10.5
 * item 13). Every row, count and date is the server's, and `lib/conflicts.ts`
 * holds only the words and the hues. Severity wears the ONE status
 * vocabulary: `high` is the destructive token, `medium` the warning token.
 *
 * ⚠️ **Four kinds are HR tier.** Without `admin:members:read` the server sends
 * the three task kinds only, and the panel says so in one line instead of
 * implying that nobody is overcommitted.
 */
export function ConflictsPanel({ data }: { data: ConflictsReport }) {
  const drawn = conflictRows(data).slice(0, PANEL_ROWS);
  const counts = countsLine(data);
  const span = windowLine(data);
  const cap = capNote(data, drawn.length);

  return (
    <Panel
      title={sectionName("conflicts")}
      hint={PANEL_HINTS.conflicts}
    >
      {drawn.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {emptyPanelLine(
            data?.hidden_people,
            // R5f round 2 (item 2). Without the grant the server holds four
            // kinds back, so "The plan lines up" would be a claim about them.
            data?.hr_visible === false
              ? "No conflicts in the kinds you can see."
              : SECTION_CLEAR_LINES.conflicts,
          )}
        </p>
      ) : (
        <>
          {counts && (
            <p
              className="mb-2 text-[11px] text-muted-foreground"
              title="Counted by the server over every row, before any cap."
            >
              {counts}
            </p>
          )}
          <ul className="space-y-2">
            {drawn.map((row, i) => {
              const accent = accentForHue(severityHue(row));
              const who = rowPeople(row);
              return (
                <li key={`${row.kind}:${asList<string>(row.task_ids).join(",")}:${i}`}>
                  <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                    <span
                      className={`size-1.5 shrink-0 rounded-full ${accent.dot}`}
                      aria-hidden
                    />
                    <span
                      // The DOT carries the severity, and the label stays in
                      // the foreground. Warning-token text on a light card
                      // read too faint in the visual review (2026-09-24).
                      className="shrink-0 font-medium text-foreground"
                      title={
                        row.severity === "high"
                          ? "High: a due date is at stake."
                          : "Medium: the plan disagrees with itself."
                      }
                    >
                      {KIND_LABEL[row.kind]}
                    </span>
                    {who && (
                      <span
                        // `pr-px`: the Load panel's italic-clip lesson.
                        className="ml-auto min-w-0 truncate pr-px text-muted-foreground"
                        title={who}
                      >
                        {who}
                      </span>
                    )}
                  </div>
                  <p
                    className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground"
                    title={row.sentence}
                  >
                    {row.sentence}
                  </p>
                </li>
              );
            })}
          </ul>
          {cap && (
            <p className="mt-2 text-[10px] text-muted-foreground">{cap}</p>
          )}
        </>
      )}
      {data?.hr_visible === false ? (
        <p className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground">
          {CONFLICTS_HR_HINT}
        </p>
      ) : (
        span &&
        asList(data?.rows).length > 0 && (
          <p
            className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground"
            title="The window bounds absences, leaving dates, overcommitment and parallel work. A dependency is wrong whenever it falls."
          >
            {span}
          </p>
        )
      )}
      <HiddenPeople n={data?.hidden_people} />
    </Panel>
  );
}

/**
 * WS-27bn R3b — who could help whom, in a report.
 *
 * ⚠️ **Draws the route and counts nothing** (`projects_reports.md` §8 R3b).
 * Every task, helper and count is the rebalance route's, and `lib/rebalance.ts`
 * holds only the words. An at-risk task and an idle person wear the People
 * dashboard's pill hues (`PILL_HUE`), each with its words beside it.
 *
 * ⚠️ **HR tier.** Without `admin:members:read` the server sends neither list,
 * and the panel says so in one line instead of drawing "nobody at risk".
 *
 * ⚠️ **Read only, and a report panel only.** No Assign, no Dismiss and no
 * task link (slice R4b). The Analytics app does not mount it yet.
 */
export function RebalancePanel({ data }: { data: RebalanceReport }) {
  const tasks = rebalanceTasks(data);
  const pickups = rebalancePickups(data);
  const cap = rebalanceCapNote(data, tasks.length, pickups.length);
  const risk = accentForHue(PILL_HUE.at_risk);
  const idle = accentForHue(PILL_HUE.idle);
  const hidden = data?.hr_visible === false;

  return (
    <Panel
      title={sectionName("rebalance")}
      hint={PANEL_HINTS.rebalance}
    >
      {hidden ? (
        <p className="text-[11px] text-muted-foreground">{REBALANCE_HR_HINT}</p>
      ) : tasks.length === 0 && pickups.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {idleLine(data?.idle_total) ?? SECTION_CLEAR_LINES.rebalance}
        </p>
      ) : (
        <>
          {typeof data?.at_risk_total === "number" &&
            typeof data?.idle_total === "number" && (
              <p
                className="mb-2 text-[11px] text-muted-foreground"
                title="Counted by the server over every task and person, before any cap."
              >
                {data.at_risk_total} at risk · {data.idle_total} idle
              </p>
            )}
          {tasks.length > 0 && (
            <ul className="space-y-2">
              {tasks.map((task) => {
                const helpers = helpersLine(task);
                return (
                  <li key={task.task_id}>
                    <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                      <span
                        className={`size-1.5 shrink-0 rounded-full ${risk.dot}`}
                        aria-hidden
                      />
                      <span
                        // `pr-px`: the Load panel's italic-clip lesson.
                        className="min-w-0 truncate pr-px font-medium text-foreground"
                        title={task.title}
                      >
                        {task.title}
                      </span>
                      <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                        {PILL_LABEL.at_risk}
                      </span>
                    </div>
                    <p
                      className="mt-0.5 truncate text-[10px] text-muted-foreground"
                      title={holderLine(task)}
                    >
                      {holderLine(task)}
                    </p>
                    <p
                      className="truncate text-[10px] text-muted-foreground"
                      title={
                        helpers
                          ? `Up to three people whose skills fit this task: ${helpers}`
                          : "Nobody in scope fits this task."
                      }
                    >
                      {helpers ? `Could help: ${helpers}` : "Nobody fits this task."}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
          {pickups.length > 0 && (
            <ul className={`space-y-1.5 ${tasks.length ? "mt-3 border-t border-border pt-2" : ""}`}>
              {pickups.map((person) => (
                <li key={person.email}>
                  <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                    <span
                      className={`size-1.5 shrink-0 rounded-full ${idle.dot}`}
                      aria-hidden
                    />
                    <PersonName
                      email={person.email}
                      name={person.name}
                      className="min-w-0 pr-px font-medium text-foreground"
                    />
                    <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                      {PILL_LABEL.idle}
                    </span>
                  </div>
                  <p
                    className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground"
                    title={`Work in scope that fits ${person.name}'s skills: ${pickupLine(person)}`}
                  >
                    Could take: {pickupLine(person)}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {cap && <p className="mt-2 text-[10px] text-muted-foreground">{cap}</p>}
        </>
      )}
    </Panel>
  );
}

/**
 * WS-27bn R3c — which open tasks lack the data every other report needs.
 *
 * ⚠️ **Draws the server's counts and counts nothing** (`projects_reports.md`
 * §8 R3c). Each kind is one bar, "n of open total", with the private `Bar`.
 * Up to five titles follow it, then "…and M more". `lib/hygiene.ts` holds
 * the words.
 *
 * ⚠️ **A task counts in each kind that it breaks**, so the bars do not add up
 * to the open total, and the panel says so under them.
 *
 * ⚠️ **A report panel only.** The Analytics app does not mount it yet: the
 * `/analytics/hygiene` route is a later slice.
 */
export function HygienePanel({ data }: { data: HygieneReport }) {
  const open = data?.open_total;
  const gap = accentForHue("amber");

  return (
    <Panel
      title={sectionName("hygiene")}
      hint={PANEL_HINTS.hygiene}
    >
      {typeof open !== "number" || open <= 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No open tasks in this scope.
        </p>
      ) : (
        <>
          <ul className="space-y-3">
            {HYGIENE_KINDS.map(({ kind, label, title }) => {
              const n = hygieneCount(data, kind);
              const rows = hygieneRows(data, kind).slice(0, TITLES_SHOWN);
              const more = moreNote(n, rows.length);
              const meaning = kindTitle(title, data);
              return (
                <li key={kind}>
                  <div className="mb-1 flex items-baseline gap-2 text-[11px]">
                    <span className="font-medium text-foreground" title={meaning}>
                      {label}
                    </span>
                    <span
                      className="ml-auto tabular-nums text-muted-foreground"
                      title={`${n ?? "No count"} of ${open} open tasks: ${meaning}`}
                    >
                      {typeof n === "number" ? `${n} of ${open}` : "—"}
                    </span>
                  </div>
                  <Bar
                    segments={[{ key: kind, value: n, dot: gap.dot, label }]}
                    total={open}
                  />
                  {rows.length > 0 && (
                    <ul className="mt-1 space-y-0.5">
                      {rows.map((r) => (
                        <li
                          key={r.id}
                          // `pr-px`: the Load panel's italic-clip lesson.
                          className="truncate pr-px text-[10px] text-muted-foreground"
                          title={`${r.title} · ${r.project_name}`}
                        >
                          {r.title}
                        </li>
                      ))}
                    </ul>
                  )}
                  {more && (
                    <p
                      className="mt-0.5 text-[10px] text-muted-foreground"
                      title="The table under this panel names up to twenty tasks of each kind."
                    >
                      {more}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground">
            {OVERLAP_NOTE}
          </p>
        </>
      )}
    </Panel>
  );
}

/**
 * WS-27bn RA-3 — what a plain click on a report row does with a task id.
 *
 * `ReportsView` provides it from the page, which opens the task as a deep
 * link does. A row is a real link to `/projects?task=<id>` either way
 * (`ControlLink`), so a modified click opens a tab. With no provider, as in
 * a static render, a plain click follows the link.
 */
export const OpenTaskContext = createContext<((taskId: string) => void) | null>(null);

/** The deep link of a task: the shape the board and the bell read. */
function taskHref(taskId: string): string {
  return `/projects?task=${encodeURIComponent(taskId)}`;
}

/** The dot of each waiting list: a tone, through the one hue vocabulary. */
const WAITING_HUE: Readonly<Record<WaitingKind, AccentHue>> = {
  questions: "amber",
  reviews: "blue",
  handoffs: "red",
};

/** One row of the waiting panel: the task as a link, then who and how long. */
function WaitingTaskRow({
  line,
  open,
}: {
  line: WaitingLine;
  open: ((taskId: string) => void) | null;
}) {
  const href = taskHref(line.id);
  return (
    <li>
      <ControlLink
        href={href}
        onActivate={() => (open ? open(line.id) : window.location.assign(href))}
        className="-mx-1 block rounded px-1 py-0.5 hover:bg-muted"
        title={`Open ${line.label}`}
      >
        <span className="block truncate pr-px text-[11px] text-foreground">{line.label}</span>
        {/* A phone wraps the detail, so "why it waits" never clips. */}
        <span className="block pr-px text-[10px] text-muted-foreground sm:truncate">
          {line.detail}
        </span>
      </ControlLink>
    </li>
  );
}

/**
 * WS-27bn RA-3 — "Waiting on people", the coordination panel (§6.8 C).
 *
 * ⚠️ **Draws the server's rows and decides nothing.** The rows, the totals,
 * the answer time and the summary sentence are `waiting_body`'s.
 * `lib/waiting.ts` holds the words. The summary comes first (§6.8 E5).
 *
 * ⚠️ **No rank of people (E1).** The rows keep the server's order, the
 * longest wait first. **Each row names the agent and its member (E2).**
 *
 * Each row opens its task. The Overview hides the panel when the scope has
 * no agent run (`overviewShown`).
 */
export function WaitingPanel({ data }: { data: WaitingReport }) {
  const open = useContext(OpenTaskContext);
  const answer = answerLine(data);
  const lists = waitingLists(data, (email) => personDisplayName(null, email));

  return (
    <Panel title={sectionName("waiting")} hint={PANEL_HINTS.waiting}>
      <p className="text-xs text-foreground">{data.summary}</p>
      {answer && (
        <p
          className="mt-1 text-[11px] text-muted-foreground"
          title="From an agent's question to the answer of its member, over the questions of this period. A question with no answer is left out of both figures and counted."
        >
          {answer}
        </p>
      )}
      {lists
        .filter((l) => l.total > 0)
        .map((l) => {
          const { shown, more } = listRows(l.rows, l.total);
          return (
            <div key={l.kind} className="mt-3 border-t border-border pt-2">
              <p className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${accentForHue(WAITING_HUE[l.kind]).dot}`}
                  aria-hidden
                />
                <span>{l.heading}</span>
                <span
                  className="ml-auto font-medium tabular-nums text-foreground"
                  title={`${l.total} in total, the longest wait first`}
                >
                  {l.total}
                </span>
              </p>
              <ul className="space-y-0.5">
                {shown.map((r) => (
                  <WaitingTaskRow key={r.key} line={r} open={open} />
                ))}
              </ul>
              {more > 0 && (
                <p
                  className="mt-0.5 text-[10px] text-muted-foreground"
                  title="The table under this panel names up to twenty of each list."
                >
                  …and {more} more
                </p>
              )}
            </div>
          );
        })}
    </Panel>
  );
}

/**
 * WS-27bn RA-4 — one heading of the "Agent runs" panel, with the rule of its
 * figure behind an `InfoTip`, so a keyboard and a touch reach it too.
 */
function RunsHeading({
  label,
  help,
  dot,
  count,
  countTitle,
}: {
  label: string;
  /** The rule, and the words of its button: "How the run sizes are counted". */
  help?: { label: string; text: string };
  dot?: string;
  count?: number;
  countTitle?: string;
}) {
  return (
    <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
      {dot && <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden />}
      <span>{label}</span>
      {help && (
        <InfoTip label={help.label} title={label}>
          {help.text}
        </InfoTip>
      )}
      {typeof count === "number" && (
        <span className="ml-auto font-medium tabular-nums text-foreground" title={countTitle}>
          {count}
        </span>
      )}
    </div>
  );
}

/**
 * WS-27bn RA-4 — "Agent runs" (`projects_reports.md` §6.8 B): "can I trust
 * agents with this work?"
 *
 * ⚠️ **Draws the server's figures and decides nothing.** The outcomes, the
 * days, the success rate, the silent runs, the sizes, the flag and the
 * summary sentence are `agent_runs_body`'s. `lib/agentRunsReport.ts` holds
 * the words and the rule of each figure. The summary comes first (§6.8 E5).
 * The chart is the Throughput chart's style, with each day stacked by
 * outcome.
 *
 * ⚠️ **No rank of people (E1), and each row names the agent and its member
 * (E2).** No text calls 100% the goal (E4). The run log stays one click
 * away, on the task.
 *
 * Each row opens its task. The Overview hides the panel when the scope has
 * no agent run (`overviewShown`).
 */
export function AgentRunsPanel({ data }: { data: AgentRunsReport }) {
  const open = useContext(OpenTaskContext);
  const name = (email: string) => personDisplayName(null, email);
  const series = asList<AgentRunsReport["series"][number]>(data?.series);
  const peak = peakDay(series);
  const trend = successTrend(data);
  const silent = listRows(silentLines(data, name), data.silent_total, PANEL_RUN_ROWS);
  const long = longLines(data, name);
  const sized = Math.max(1, ...RUN_SIZES.map((sz) => data.sizes?.[sz.key] ?? 0));
  const flag = accentForHue("amber");

  return (
    <Panel title={sectionName("agent_runs")} hint={PANEL_HINTS.agent_runs}>
      <p className="text-xs text-foreground">{data.summary}</p>

      <dl className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <dt className="flex items-center gap-1 text-muted-foreground">
            <span>Success rate</span>
            <InfoTip label="How the success rate is counted" title="Success rate">
              {AGENT_RUN_RULES.success(data.days)}
            </InfoTip>
          </dt>
          <dd
            className="font-semibold tabular-nums text-foreground"
            title={`${data.success.completed} of ${data.success.rated} runs that completed, failed or were abandoned`}
          >
            {successFigure(data)}
          </dd>
          {trend && <dd className="text-[10px] text-muted-foreground">{trend}</dd>}
        </div>
        <div>
          <dt className="text-muted-foreground">Ended</dt>
          <dd
            className="font-semibold tabular-nums text-foreground"
            title={`Agent runs that ended in the last ${data.days} days`}
          >
            {data.ended_total}
          </dd>
          <dd className="text-[10px] text-muted-foreground">in {data.days} days</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1 text-muted-foreground">
            <span>Silent now</span>
            <InfoTip label="How a silent run is counted" title="Silent now">
              {AGENT_RUN_RULES.silent(data.silent_after_minutes)}
            </InfoTip>
          </dt>
          <dd
            className="font-semibold tabular-nums text-foreground"
            title={`Open runs with no agent activity for ${data.silent_after_minutes} minutes or more`}
          >
            {data.silent_total}
          </dd>
        </div>
      </dl>

      {/* The outcomes of each day, stacked. ⚠️ A chart with no axis is a
          picture, so the scale and the two ends are named, as Throughput
          names them. */}
      <div className="mt-3 border-t border-border pt-2">
        <RunsHeading
          label="Run outcomes"
          help={{ label: "How the run outcomes are counted", text: AGENT_RUN_RULES.outcomes(data.days) }}
        />
        <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
          <span title={`Busiest day: ${peak} runs ended`}>{peak}</span>
          <span>runs ended per day</span>
        </div>
        <div className="flex h-16 items-end gap-0.5">
          {series.map((d) => (
            <div
              key={d.day}
              className="flex h-full flex-1 flex-col justify-end"
              title={
                `${weekLabel(d.day)}: ${d.total} ended` +
                RUN_OUTCOME_SEGMENTS.filter((seg) => d[seg.key] > 0)
                  .map((seg) => `, ${d[seg.key]} ${seg.label.toLowerCase()}`)
                  .join("")
              }
            >
              {d.total === 0 ? (
                <div className="h-[3%] w-full rounded-sm bg-muted" />
              ) : (
                // Completed sits on the axis, and the other outcomes above it.
                <div
                  className="flex w-full flex-col overflow-hidden rounded-sm"
                  style={{ height: `${Math.max(8, (d.total / peak) * 100)}%` }}
                >
                  {[...RUN_OUTCOME_SEGMENTS].reverse().map((seg) =>
                    d[seg.key] > 0 ? (
                      <div
                        key={seg.key}
                        className={outcomeAccent(seg.key).dot}
                        style={{ flexGrow: d[seg.key] }}
                      />
                    ) : null
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-1 flex items-baseline justify-between text-[11px] text-muted-foreground">
          <span>{weekLabel(series[0]?.day)}</span>
          <span>today</span>
        </div>
        {/* The legend carries the meaning, never the hue alone. */}
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          {RUN_OUTCOME_SEGMENTS.map((seg) => (
            <li key={seg.key} className="inline-flex items-center gap-1.5" title={seg.title}>
              <span className={`h-2 w-2 shrink-0 rounded-full ${outcomeAccent(seg.key).dot}`} aria-hidden />
              {seg.label}
              <span className="font-medium tabular-nums text-foreground">
                {data.outcomes?.[seg.key] ?? 0}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {data.silent_total > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          {/* The rule of a silent run is the InfoTip of the figure above. */}
          <RunsHeading
            label="Silent now"
            dot={flag.dot}
            count={data.silent_total}
            countTitle={`${data.silent_total} in total, the longest silence first`}
          />
          <ul className="space-y-0.5">
            {silent.shown.map((r) => (
              <WaitingTaskRow key={r.key} line={r} open={open} />
            ))}
          </ul>
          {silent.more > 0 && (
            <p
              className="mt-0.5 text-[10px] text-muted-foreground"
              title="The table under this panel names up to twenty silent runs."
            >
              …and {silent.more} more
            </p>
          )}
        </div>
      )}

      {data.sized_total > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <RunsHeading
            label="Run sizes"
            help={{
              label: "How the run sizes are counted",
              text: AGENT_RUN_RULES.sizes(data.days, data.long_after_hours),
            }}
            count={data.sized_total}
            countTitle={`${data.sized_total} runs completed in the last ${data.days} days`}
          />
          <ul className="space-y-1">
            {RUN_SIZES.map((sz) => {
              const n = data.sizes?.[sz.key] ?? 0;
              const isLong = sz.key === LONG_SIZE;
              return (
                <li key={sz.key} className="flex items-center gap-2 text-[11px]">
                  <span className="w-24 shrink-0 text-muted-foreground">{sz.label}</span>
                  <span className="h-2 min-w-0 flex-1 rounded-sm bg-muted" aria-hidden>
                    <span
                      // A size is a count, not a control: the quiet tone, so
                      // a changed accent never repaints the data.
                      className={`block h-full rounded-sm ${isLong ? flag.dot : "bg-muted-foreground/60"}`}
                      style={{ width: `${n === 0 ? 0 : Math.max(4, (n / sized) * 100)}%` }}
                    />
                  </span>
                  <span className="w-6 shrink-0 text-right font-medium tabular-nums text-foreground">
                    {n}
                  </span>
                </li>
              );
            })}
          </ul>
          {(data.sizes?.unmeasured ?? 0) > 0 && (
            <p className="mt-1 text-[10px] text-muted-foreground">
              {data.sizes.unmeasured} completed with no agent time, so they have no size.
            </p>
          )}
          {data.long_line && (
            <p className="mt-2 flex items-start gap-1.5 text-[11px] text-foreground">
              <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${flag.dot}`} aria-hidden />
              <span>{data.long_line}</span>
            </p>
          )}
          {long.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {long.slice(0, PANEL_RUN_ROWS).map((r) => (
                <WaitingTaskRow key={r.key} line={r} open={open} />
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}

/**
 * WS-27bn R3d — one card for each person, read today.
 *
 * ⚠️ **Draws the server's card and decides nothing** (`projects_reports.md`
 * §8 R3d). The status, the reasons and every count are `pulse_body`'s.
 * `lib/pulse.ts` holds the words. The load bar uses the private `Bar`.
 *
 * ⚠️ **The HR half is decided per row, by key presence.** A card with no
 * `status` draws no status and falls back to overdue of open tasks. A
 * person on leave draws "On leave", never the pill, so "Idle" cannot say
 * that somebody who is away has room.
 *
 * ⚠️ **The server removes the cards this reader may not see** (§7.1, WS-27bn
 * R5a). The panel only prints the line that counts them.
 *
 * ⚠️ **A report panel only.** The Analytics app does not mount it: there is
 * no `/analytics/pulse` route yet.
 */
export function PulsePanel({
  data,
  hiddenHint,
}: {
  data: PulseReport;
  /**
   * `projects_reports.md` §6.5 item 16. What the reader can do about the hidden
   * cards. The report builder passes it. The words stay `hiddenPeopleLine`'s.
   */
  hiddenHint?: string | null;
}) {
  const rows = pulseRows(data);
  const hidden = hiddenPeopleLine(data?.hidden_people, "view", hiddenHint);
  const overdue = accentForHue("red");
  const help = accentForHue("red");

  return (
    <Panel
      title={sectionName("pulse")}
      hint={PANEL_HINTS.pulse}
      // §6.5 item 17. The note sits in the hint's tooltip, not as a
      // line of its own under the cards.
      hintTitle={data?.help_note}
    >
      {rows.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {emptyPanelLine(data?.hidden_people, SECTION_CLEAR_LINES.pulse)}
        </p>
      ) : (
        // `@container`: the grid follows the panel's own width, not the
        // window's. A report column can be narrow on a wide screen.
        <div className="@container">
          <ul className="grid gap-2 @md:grid-cols-2">
            {rows.map((row) => {
              const mark = statusMark(row);
              const accent = mark ? accentForHue(mark.hue) : null;
              const bar = loadBar(row);
              const focus = focusShown(row);
              const more = focusMore(row, focus.length);
              const needs = helpLine(row);
              const waiting = row.waiting_count;
              return (
                <li
                  key={row.assignee}
                  className="min-w-0 rounded-md border border-border p-2"
                >
                  <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                    <span
                      className="min-w-0 truncate pr-px font-medium text-foreground"
                      title={row.assignee}
                    >
                      {pulseName(row)}
                    </span>
                    {mark && accent && (
                      <span
                        className="ml-auto flex shrink-0 items-center gap-1 text-muted-foreground"
                        title={mark.title}
                      >
                        <span
                          className={`size-1.5 rounded-full ${accent.dot}`}
                          aria-hidden
                        />
                        {mark.label}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <Bar
                        total={bar.of}
                        emptyTitle={bar.emptyTitle}
                        segments={[
                          {
                            key: bar.kind,
                            value: bar.value,
                            dot:
                              bar.kind === "hours" && accent
                                ? accent.dot
                                : overdue.dot,
                            label:
                              bar.kind === "hours" ? "Committed hours" : "Overdue",
                          },
                        ]}
                      />
                    </div>
                    <span
                      className="shrink-0 text-[10px] tabular-nums text-muted-foreground"
                      title={bar.title}
                    >
                      {bar.text}
                    </span>
                  </div>
                  {focus.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5">
                      {focus.map((task) => (
                        <li
                          key={task.id}
                          className="flex min-w-0 items-baseline gap-1 text-[10px] text-muted-foreground"
                          title={`${task.title}${task.project_name ? ` · ${task.project_name}` : ""}: ${focusWhy(task)}`}
                        >
                          <span className="min-w-0 truncate pr-px text-foreground">
                            {task.title}
                          </span>
                          <span className="shrink-0">· {focusWhy(task)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {more && (
                    <p
                      className="mt-0.5 text-[10px] text-muted-foreground"
                      title={`${row.focus_total} focus tasks: in progress, or due today or tomorrow. The table under this panel names up to five.`}
                    >
                      {more}
                    </p>
                  )}
                  {typeof waiting === "number" && waiting > 0 && (
                    <p
                      className="mt-0.5 text-[10px] text-muted-foreground"
                      title="Only you see this. It comes from your own notes in My work."
                    >
                      {waiting} waiting past the date
                    </p>
                  )}
                  {needs && (
                    <p
                      className="mt-1 text-[10px] font-medium text-foreground"
                      title={data?.help_note ?? needs}
                    >
                      <span
                        className={`mr-1 inline-block size-1.5 rounded-full align-middle ${help.dot}`}
                        aria-hidden
                      />
                      {needs}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {hidden && (
        <div className="mt-3 space-y-0.5 border-t border-border pt-2 text-[11px] text-muted-foreground">
          <p title="You see your own card, and the cards of the members of the teams you lead. An admin sees every card.">
            {hidden}
          </p>
        </div>
      )}
    </Panel>
  );
}

/**
 * Work left and work done, in ESTIMATED hours.
 *
 * ⚠️ **Estimated, never logged.** Owner decision 2026-09-17, taken knowing
 * this product tracks no hours at all. "Done" is the estimate of what
 * reached `done`. Every string here says so, and none may say "logged",
 * "actual" or "tracked".
 *
 * ⚠️ **The asterisk carries the whole claim.** A remaining total drawn from
 * a tenth of the tasks looks identical to a complete one, and a reader plans
 * against it either way. `effort.ts` refuses to hand back a figure with no
 * coverage at all, and this marks every figure that is partial.
 */
function EffortLine({ data }: { data: LoadReport }) {
  const eff = effortDisplay(data.effort);
  if (!eff) return null;

  if (eff.kind === "none") {
    // An invitation, not "0h". Measured 2026-09-17: the demo tree carried
    // zero estimates across 37 tasks, which is where every project starts.
    return (
      <p
        className="mt-1 text-[11px] text-muted-foreground"
        title="Set an estimate on a task to see effort here. This product records no hours worked, so these figures are always plans."
      >
        {eff.unsized > 0
          ? `No estimates yet — ${eff.unsized} open ${
              eff.unsized === 1 ? "task carries" : "tasks carry"
            } no size.`
          : "No estimates yet."}
      </p>
    );
  }

  return (
    <p className="mt-1 text-[11px] text-muted-foreground">
      <span
        title={`Estimated effort remaining, across ${eff.left.estimated} of ${eff.left.total} open tasks. A plan, not hours worked — this product records none.`}
      >
        <strong className="tabular-nums text-foreground">
          {eff.leftLabel}
        </strong>
        {!eff.left.complete && <span className="opacity-60">*</span>} left
      </span>
      {eff.spent.total > 0 && (
        <span
          title={`Estimated effort of the ${eff.spent.total} finished tasks, of which ${eff.spent.estimated} carry an estimate. Cancelled work is excluded.`}
        >
          {" · "}
          <span className="tabular-nums">{eff.spentLabel}</span>
          {!eff.spent.complete && <span className="opacity-60">*</span>} done
        </span>
      )}
      {eff.donePct !== null && (
        <span className="tabular-nums"> · {eff.donePct}% of the estimate</span>
      )}
      {(!eff.left.complete || !eff.spent.complete) && (
        <span
          className="ml-1 opacity-70"
          title={`* Partial. ${eff.left.missing} open and ${eff.spent.missing} finished tasks carry no estimate, so the real figures are higher.`}
        >
          * partial
        </span>
      )}
    </p>
  );
}

/**
 * "7 Sep" — one week bucket, for a chart axis.
 *
 * ⚠️ Parsed by hand, never through `new Date()`. These are floating calendar
 * dates and `new Date("2026-09-07")` is midnight UTC — a day west of
 * Greenwich, which names the wrong week on the reader's screen. The third
 * place in this app to carry that note, after `ReportsView.periodLabel` and
 * `outlook.shortDate`.
 */
function weekLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const [, m, d] = iso.slice(0, 10).split("-");
  const month = MONTHS_SHORT[Number(m) - 1];
  return month ? `${Number(d)} ${month}` : "";
}

const MONTHS_SHORT = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");


/** (c) Are we getting faster? */
export function ThroughputPanel({ data }: { data: ThroughputReport }) {
  const done = statusAccent({ category: "done" });
  const dropped = statusAccent({ category: "cancelled" });
  const series = asList<ThroughputReport["series"][number]>(data?.series);
  const peak = Math.max(1, ...series.map((w) => w.completed));
  const summary = data?.summary ?? ({} as ThroughputReport["summary"]);

  return (
    <Panel
      title={sectionName("throughput")}
      hint={PANEL_HINTS.throughput}
    >
      {/* WS-27bn RA-2. The sentence of the split comes first (§6.8 E5). The
          server writes it. */}
      {data.actor_summary && (
        <p className="mb-2 text-xs text-foreground">{data.actor_summary}</p>
      )}
      {/* ⚠️ A CHART WITH NO AXIS IS A PICTURE. Photographed 2026-09-17: six
          green bars with no label anywhere, so a reader could see that the
          last one was tallest and not which week it was, nor how many. The
          scale and the ends of the axis are drawn; the middle is left to the
          hover text, because six labels at this width would collide. */}
      <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span title={`Peak week: ${peak} finished`}>{peak}</span>
        <span>finished per week</span>
      </div>
      <div className="flex h-24 items-end gap-1">
        {series.map((w, i) => {
          const last = i === series.length - 1;
          const partial = last && data.current_week_partial;
          return (
            <div
              key={w.week_start}
              className="flex h-full flex-1 flex-col justify-end"
              title={
                `Week of ${w.week_start}: ${w.completed} finished` +
                (w.cancelled ? `, ${w.cancelled} cancelled` : "") +
                // WS-27bn R2b: a report's week carries no median at all, and
                // an absent figure is said as nothing, not as "NaNd".
                (typeof w.median_hours === "number"
                  ? `, median ${duration(w.median_hours)}`
                  : w.median_hours === null
                    ? ", no measurable cycle time"
                    : "") +
                (partial ? " — this week is not over yet" : "")
              }
            >
              <div
                // ⚠️ The current week is DASHED, never dropped and never
                // solid. Dropping it loses this week's work; drawing it solid
                // makes every Monday look like a collapse.
                className={`w-full rounded-sm ${done.dot} ${partial ? "opacity-50" : ""}`}
                style={{
                  height: `${Math.max(w.completed > 0 ? 6 : 2, (w.completed / peak) * 100)}%`,
                }}
              />
            </div>
          );
        })}
      </div>
      {/* The two ends of the axis, named. `weekLabel` parses by hand — these
          are floating calendar dates, and `new Date("2026-09-07")` is
          midnight UTC, a day west of Greenwich. */}
      <div className="mt-1 flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span>{weekLabel(series[0]?.week_start)}</span>
        <span>
          {data.current_week_partial
            ? "this week, so far"
            : weekLabel(series[series.length - 1]?.week_start)}
        </span>
      </div>

      {/* WS-27bn R2b. Each cell shows only when the server sent its
          figure. A report's summary has no `completed`, because its
          Finished section already says it. */}
      <dl className="mt-3 grid grid-cols-3 gap-2 border-t border-border pt-2 text-[11px]">
        {typeof summary.completed === "number" && (
        <div>
          <dt className="text-muted-foreground">Finished</dt>
          <dd
            className={`font-semibold tabular-nums ${done.text}`}
            title={`${summary.completed} tasks reached a done status in the last ${data.weeks} weeks`}
          >
            {summary.completed}
          </dd>
        </div>
        )}
        {summary.median_hours !== undefined && (
        <div>
          <dt className="text-muted-foreground">Median</dt>
          <dd
            className="font-semibold tabular-nums"
            title={
              summary.median_hours === null
                ? "No task in this period recorded both a start and a finish, so there is no cycle time to report."
                : `Half of the ${summary.measured} measured tasks took less than this, from first moved to In progress until done.`
            }
          >
            {duration(summary.median_hours)}
          </dd>
        </div>
        )}
        {summary.p90_hours !== undefined && (
        <div>
          <dt className="text-muted-foreground">Slowest 10%</dt>
          <dd
            className="font-semibold tabular-nums"
            title={
              summary.p90_hours === null
                ? "No measurable cycle time in this period."
                : `One task in ten took longer than this. Reported beside the median because cycle time is heavily skewed, and a mean would hide it.`
            }
          >
            {duration(summary.p90_hours)}
          </dd>
        </div>
        )}
      </dl>

      {/* WS-27bn RA-2. The median and the slowest 10% of people and of
          agents. Agents holds agent-finished and agent-assisted tasks. */}
      {data.actor_cycle && <CycleSplit cycle={data.actor_cycle} />}

      {((summary.no_start ?? 0) > 0 || (summary.cancelled ?? 0) > 0) && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {(summary.no_start ?? 0) > 0 && (
            // ⚠️ Said out loud, because it is the median's denominator. A
            // reader who does not know these were excluded reads the median
            // as covering everything.
            <span
              title={`${summary.no_start} finished tasks never passed through In progress, so they have no measurable cycle time. They are left out of the median rather than counted as zero.`}
            >
              {summary.no_start} without a recorded start
            </span>
          )}
          {(summary.no_start ?? 0) > 0 && (summary.cancelled ?? 0) > 0 && " · "}
          {(summary.cancelled ?? 0) > 0 && (
            <span
              className={dropped.text}
              // Cancellations are never throughput. Shown so they are not
              // invisible — "we cancelled twelve" is the finding on some boards.
              title={`${summary.cancelled} tasks were cancelled in this period. Cancellations are never counted as finished work.`}
            >
              {summary.cancelled} cancelled
            </span>
          )}
        </p>
      )}
    </Panel>
  );
}

/**
 * WS-27bn RA-2 — the cycle time of people and of agents, under the
 * Throughput figures. Each figure is the server's (`actor_cycle`).
 */
function CycleSplit({ cycle }: { cycle: NonNullable<ThroughputReport["actor_cycle"]> }) {
  const rows = [
    {
      key: "people",
      label: "People",
      group: cycle.people,
      rule: "Tasks that no agent worked before they finished.",
    },
    {
      key: "agents",
      label: "Agents",
      group: cycle.agents,
      rule: "Tasks that an agent worked before they finished, also when a person finished them.",
    },
  ];
  return (
    <table className="mt-2 w-full border-t border-border text-[11px]">
      <caption className="sr-only">Cycle time of people and of agents</caption>
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="pt-2 font-normal">Who</th>
          <th className="pt-2 text-right font-normal">Median</th>
          <th className="pt-2 text-right font-normal">Slowest 10%</th>
          <th className="pt-2 text-right font-normal">Measured</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <th scope="row" className="py-0.5 text-left font-medium text-foreground" title={r.rule}>
              {r.label}
            </th>
            <td
              className="py-0.5 text-right font-semibold tabular-nums"
              title={
                r.group.median_hours === null
                  ? `No task of ${r.label.toLowerCase()} in this period has a measured cycle time.`
                  : `Half of the ${r.group.measured} measured tasks of ${r.label.toLowerCase()} took less than this.`
              }
            >
              {duration(r.group.median_hours)}
            </td>
            <td
              className="py-0.5 text-right tabular-nums"
              title={`One task in ten of ${r.label.toLowerCase()} took longer than this.`}
            >
              {duration(r.group.p90_hours)}
            </td>
            <td
              className="py-0.5 text-right tabular-nums text-muted-foreground"
              title={
                `${r.group.measured} of ${r.group.completed} finished tasks recorded a start` +
                (r.group.assisted ? `. A person finished ${r.group.assisted} of them.` : ".")
              }
            >
              {r.group.measured} of {r.group.completed}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * WS-27bn RA-2 — "Who did the work" (`projects_reports.md` §6.8 B). It
 * replaces "What we finished" in the Overview when the scope is one project
 * (`whoDidTheWorkShown`).
 *
 * ⚠️ **Draws the server's figures and decides nothing.** The weeks, the
 * split, the sentence and the member rows are `analytics_agents.py`'s. The
 * sentence comes first (§6.8 E5). The chart is the Throughput chart's
 * style, with each week stacked by label.
 *
 * ⚠️ **An agent never hides its person (E2), and no rank (E1).** Each agent
 * sits under the member it works for. The server sorts the members by name.
 */
export function WhoDidTheWorkPanel({ data }: { data: WhoDidTheWorkReport }) {
  const series = asList<WhoDidTheWorkReport["actor_series"][number]>(data?.actor_series);
  const peak = Math.max(1, ...series.map((w) => w.completed));
  const members = asList<WhoDidTheWorkReport["agent_members"][number]>(data?.agent_members);
  const names = usePersonNames(members.map((m) => m.member));
  const hidden = hiddenMembersLine(data?.hidden_people);

  return (
    <Panel
      title={WHO_DID_THE_WORK}
      hint={`${WHO_HINT_LEAD}, ${period(data?.period_start, data?.period_end)}.`}
    >
      <p className="text-xs text-foreground">{data.actor_summary}</p>

      <div className="mt-2 flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span title={`Peak week: ${peak} finished`}>{peak}</span>
        <span>finished per week</span>
      </div>
      <div className="flex h-24 items-end gap-1">
        {series.map((w, i) => {
          const partial = i === series.length - 1 && data.current_week_partial;
          return (
            <div
              key={w.week_start}
              className={`flex h-full flex-1 flex-col justify-end ${partial ? "opacity-50" : ""}`}
              title={
                `Week of ${w.week_start}: ${w.completed} finished, ${w.people} by people,` +
                ` ${w.agents} by agents` +
                (w.agent_assisted ? ` (a person finished ${w.agent_assisted} of them)` : "") +
                (partial ? " — this week is not over yet" : "")
              }
            >
              {w.completed === 0 ? (
                <div className="h-[2%] w-full rounded-sm bg-muted" />
              ) : (
                // The people sit on the axis, and the agents above them.
                <div
                  className="flex w-full flex-col overflow-hidden rounded-sm"
                  style={{ height: `${Math.max(6, (w.completed / peak) * 100)}%` }}
                >
                  {[...ACTOR_SEGMENTS].reverse().map((seg) =>
                    w[seg.key] > 0 ? (
                      <div
                        key={seg.key}
                        className={actorAccent(seg.key).dot}
                        style={{ flexGrow: w[seg.key] }}
                      />
                    ) : null
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span>{weekLabel(series[0]?.week_start)}</span>
        <span>
          {data.current_week_partial
            ? "this week, so far"
            : weekLabel(series[series.length - 1]?.week_start)}
        </span>
      </div>

      {/* The legend carries the meaning, never the hue alone. */}
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {ACTOR_SEGMENTS.map((seg) => (
          <li key={seg.key} className="inline-flex items-center gap-1.5" title={seg.title}>
            <span className={`h-2 w-2 shrink-0 rounded-full ${actorAccent(seg.key).dot}`} aria-hidden />
            {seg.label}
            <span
              className="font-medium tabular-nums text-foreground"
              title={`${data.actor_split[seg.key]} in this period. ${seg.title}`}
            >
              {data.actor_split[seg.key]}
            </span>
          </li>
        ))}
      </ul>

      {members.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <p className="mb-1 text-[11px] text-muted-foreground">Agent work, by the member it works for</p>
          <ul className="space-y-1">
            {members.map((m) => (
              <li key={m.member ?? "__in_app"} className="text-[11px]">
                <div className="flex min-w-0 items-baseline gap-2">
                  {m.member ? (
                    <PersonName
                      email={m.member}
                      name={m.member_name || names.get(m.member.toLowerCase())}
                      className="min-w-0 pr-px"
                    />
                  ) : (
                    <span
                      className="min-w-0 truncate pr-px text-muted-foreground"
                      title="In-app agents work for no member."
                    >
                      In-app agents
                    </span>
                  )}
                  <span
                    className="ml-auto shrink-0 font-medium tabular-nums"
                    title={`${m.total} tasks of this period that these agents worked`}
                  >
                    {m.total}
                  </span>
                </div>
                <p className="truncate pr-px text-[10px] text-muted-foreground">
                  {m.agents.map(memberAgentLine).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {hidden && <p className="mt-2 text-[10px] text-muted-foreground">{hidden}</p>}
    </Panel>
  );
}

/** (d) What did we finish? */
export function FinishedPanel({ data }: { data: FinishedReport }) {
  const done = statusAccent({ category: "done" });
  const dropped = statusAccent({ category: "cancelled" });
  const projects = asList<FinishedReport["projects"][number]>(data?.projects);
  const peak = Math.max(1, ...projects.map((p) => p.completed));

  return (
    <Panel
      title={sectionName("finished")}
      hint={`${FINISHED_HINT_LEAD}, ${period(data?.period_start, data?.period_end)}.`}
    >
      {projects.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          Nothing finished in this period.
        </p>
      ) : (
        <>
          <ul className="space-y-1.5">
            {projects.slice(0, 8).map((p) => (
              <li key={p.project_id}>
                <div className="flex items-baseline gap-2 text-[11px]">
                  <span className="min-w-0 truncate pr-px">{p.name}</span>
                  <span
                    className={`ml-auto font-medium tabular-nums ${done.text}`}
                    title={
                      `${p.completed} finished in ${p.name}` +
                      (p.cancelled ? `, ${p.cancelled} cancelled` : "") +
                      // WS-27bn R2b: absent is not null. `undefined !== null`
                      // printed "median NaNd" for a report body.
                      (typeof p.median_hours === "number"
                        ? `, median ${duration(p.median_hours)}`
                        : p.median_hours === null
                          ? ", no measurable cycle time"
                          : "")
                    }
                  >
                    {p.completed}
                  </span>
                  {p.cancelled > 0 && (
                    <span
                      className={`tabular-nums ${dropped.text}`}
                      // ⚠️ Beside the completions, never added to them. A
                      // team that cancelled nine did not finish nine.
                      title={`${p.cancelled} cancelled in ${p.name}. Cancellations are never counted as finished work.`}
                    >
                      −{p.cancelled}
                    </span>
                  )}
                </div>
                <div className="mt-1">
                  <Bar
                    total={peak}
                    segments={[
                      {
                        key: "done",
                        value: p.completed,
                        dot: done.dot,
                        label: "Finished",
                      },
                    ]}
                  />
                </div>
              </li>
            ))}
          </ul>
          <p
            className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground"
            title={
              `${data.total_completed} tasks finished between ` +
              `${data.period_start} and ${data.period_end}` +
              (data.total_cancelled
                ? `, and ${data.total_cancelled} were cancelled`
                : "")
            }
          >
            {data.total_completed} finished
            {projects.length > 8 && ` across ${projects.length} projects`}
            {data.total_cancelled > 0 && ` · ${data.total_cancelled} cancelled`}
          </p>
        </>
      )}
    </Panel>
  );
}

/**
 * Will this land, and when — the executive read (wave 7).
 *
 * ⚠️ **Four sentences, not four numbers.** Owner ask 2026-09-17 was for an
 * estimated completion date and *"other important information that might be
 * needed by an executive team, CEO, or project/product manager"*. The server
 * refuses to forecast far more often than it forecasts, and each refusal is
 * a finding — "scope is growing faster than delivery" is worth more than any
 * date this endpoint could print. So every state renders as a sentence.
 * `outlook.ts` holds the wording and `outlook.test.ts` pins it.
 *
 * ⚠️ **Nothing here is a logged hour.** This product records none.
 */
const TONE: Record<Tone, string> = {
  // Through `statusAccent`, never a raw palette class (AGENTS.md rule 5).
  good: statusAccent({ category: "done" }).text,
  bad: statusAccent({ category: "cancelled" }).text,
  warn: accentForHue("amber").text,
  quiet: "text-muted-foreground",
};

/** The filled segment of the slip bar, in the verdict's own tone. */
const TONE_DOT: Record<Tone, string> = {
  good: statusAccent({ category: "done" }).dot,
  bad: statusAccent({ category: "cancelled" }).dot,
  warn: accentForHue("amber").dot,
  quiet: "bg-muted-foreground/60",
};

/**
 * WS-27bn R3a — the plan against the forecast, as one range.
 *
 * The track runs from today to the later date. The segment between the plan
 * and the forecast is the slip, in the verdict's tone. `slipRange` places
 * both ends, and the text below says both dates and the slip, because a bar
 * alone does not say which end is which.
 */
function SlipBar({ range }: { range: SlipRange }) {
  return (
    <div className="mb-3">
      <div
        className="relative h-2 w-full rounded-full bg-muted"
        role="img"
        aria-label={range.title}
        title={range.title}
      >
        <div
          className={`absolute inset-y-0 rounded-full ${TONE_DOT[range.tone]}`}
          style={{ left: `${range.start}%`, width: `${range.width}%` }}
        />
        <span
          className="absolute -top-0.5 h-3 w-0.5 -translate-x-1/2 rounded-full bg-foreground"
          style={{ left: `${range.plan}%` }}
          aria-hidden
        />
        <span
          className={`absolute -top-0.5 size-3 -translate-x-1/2 rounded-full border-2 border-card ${TONE_DOT[range.tone]}`}
          style={{ left: `${range.forecast}%` }}
          aria-hidden
        />
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="h-3 w-0.5 rounded-full bg-foreground" aria-hidden />
          Plan {range.planLabel}
          {range.planPassed && " (passed)"}
        </span>
        <span className="flex items-center gap-1">
          <span
            className={`size-2 rounded-full ${TONE_DOT[range.tone]}`}
            aria-hidden
          />
          Forecast {range.forecastLabel}
        </span>
        <span className={`ml-auto font-medium ${TONE[range.tone]}`}>
          {range.slip}
        </span>
      </div>
    </div>
  );
}

function Verdict({ label, line }: { label: string; line: OutlookLine }) {
  return (
    <div className="min-w-0">
      {/* ⚠️ Sentence case, not tracked-out capitals. Rule 10 of the
          visual-review catalogue names an ALL-CAPS eyebrow as one of the
          commonest tells of generated design, and this panel had four. */}
      <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      <p className={`text-sm font-semibold ${TONE[line.tone]}`}>
        {line.headline}
      </p>
      {/* ⚠️ The detail is NOT a tooltip. A refusal that only explains itself
          on hover is a refusal most readers never understand — and hover
          does not exist on a phone. */}
      <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
        {line.detail}
      </p>
    </div>
  );
}

export function OutlookPanel({ data }: { data: OutlookReport }) {
  // ⚠️ Nothing, rather than a crash. A response this panel cannot read
  // took the whole PAGE down on 2026-09-17 — not the panel, the page.
  // `isDrawableOutlook` carries the four times this has happened here.
  if (!isDrawableOutlook(data)) return null;
  const verdict = headlineVerdict(data);
  // WS-27bn R3a. `null` when either date is absent, so the verdicts with no
  // forecast date draw no bar. The headline above already says why.
  const range = slipRange(data);
  const gap = forecastGap(data);
  const velocity = velocityLine(data.velocity);
  // WS-27bm S11. A reader without the HR grant is told leave is not counted.
  const capacity = capacityLine(data.capacity, data.people?.absences_applied);
  const people = peopleLine(data);

  return (
    <Panel
      title={sectionName("outlook")}
      hint={PANEL_HINTS.outlook}
    >
      {/* ⚠️ THE ANSWER FIRST, and at a size nothing else on the page reaches.
          Photographed 2026-09-17: six equal-weight facts and no verdict, with
          "79 days late" — the most important number on the portfolio view —
          set in the third quadrant at the same size as "3 people". BLUF, on
          a screen: the conclusion, then the evidence for whoever checks it. */}
      <div className="mb-3">
        <p className={`text-2xl font-semibold leading-tight ${TONE[verdict.tone]}`}>
          {verdict.headline}
        </p>
        <p className="mt-1 max-w-2xl text-xs leading-snug text-muted-foreground">
          {verdict.detail}
        </p>
      </div>

      {range && <SlipBar range={range} />}

      {/* ⚠️ The two forecasts disagreed by five months on screen and nothing
          said so. Both were calm coloured dates in the same size, and the
          reader was left to subtract them and decide which to believe. */}
      {gap ? (
        <div
          className={`mb-3 rounded-md border border-border bg-muted/40 px-2.5 py-2`}
        >
          <p className={`text-xs font-medium ${TONE[gap.tone]}`}>
            The two forecasts are {gap.headline}
          </p>
          <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
            {gap.detail}
          </p>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-x-4 gap-y-2.5 border-t border-border pt-2.5 sm:grid-cols-3">
        <Verdict label="At the current rate" line={velocity} />
        <Verdict label="If the plan holds" line={capacity} />
        <Verdict label="Who is carrying it" line={people} />
      </div>
    </Panel>
  );
}
