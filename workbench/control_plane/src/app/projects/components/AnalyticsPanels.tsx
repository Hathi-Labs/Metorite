"use client";

/**
 * WS-27bk §9.12.7 — the owner's analytics questions, rendered.
 *
 * Three endpoints existed and NOTHING drew them. `/analytics/stuck`,
 * `/analytics/load` and `/analytics/throughput` all shipped with tests and no
 * surface, and `AnalyticsView`'s own header recorded the gap it left: *"a
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
 * ⚠️ **No chart library.** Same reasoning `AnalyticsView` used for dropping
 * TanStack: one screen of bars is not worth a dependency, and every charting
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
import {
  type AccentHue,
  accentForHue,
  statusAccent,
} from "@/lib/statusAccent";

import type {
  FinishedReport,
  LoadReport,
  OutlookReport,
  StuckReport,
  ThroughputReport,
} from "../lib/api";
import { asList, bandCount, staleBands } from "../lib/analyticsRead";
import { effortDisplay, personEffort } from "../lib/effort";
import {
  type OutlookLine,
  type Tone,
  capacityLine,
  peopleLine,
  slipLine,
  velocityLine,
} from "../lib/outlook";

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

function day(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

function period(from: string, to: string): string {
  const a = day(from);
  const b = day(to);
  // One year, said once: "29 Jun – 20 Sep 2026".
  const ya = a.slice(a.lastIndexOf(" "));
  return ya === b.slice(b.lastIndexOf(" "))
    ? `${a.slice(0, a.lastIndexOf(" "))} – ${b}`
    : `${a} – ${b}`;
}

/** Hours, as something a person reads without converting it. */
function duration(hours: number | null): string {
  if (hours === null) return "—";
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

function Panel({
  title,
  hint,
  children,
}: {
  title: string;
  /** What the panel MEANS. The owner asked for exactly this. */
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-card p-3">
      <header className="mb-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>
      </header>
      {children}
    </section>
  );
}

/**
 * One horizontal bar built from segments.
 *
 * ⚠️ A zero-width segment is DROPPED rather than rendered at 0%. A 0%-wide
 * div still draws its border in some engines, which puts a hairline of the
 * wrong colour on the bar and reads as a real value.
 */
function Bar({
  segments,
  total,
}: {
  segments: { key: string; value: number; dot: string; label: string }[];
  total: number;
}) {
  if (total <= 0) {
    return (
      <div
        className="h-2 w-full rounded-full bg-muted"
        title="Nothing in this scope yet"
      />
    );
  }
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
      {segments
        .filter((s) => s.value > 0)
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
      title="Where work is stuck"
      hint="Open tasks by how long they have sat without a change, what is blocked, and what is past due."
    >
      <Bar
        total={staleTotal}
        segments={bands.map((b) => ({
          key: b.key,
          value: bandCount(sent, b.key),
          dot: accentForHue(b.hue).dot,
          label: b.full,
        }))}
      />
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

      {data.blocked_total > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <p className="mb-1 text-[11px] text-muted-foreground">
            {/* ⚠️ "N of M", never a bare N. The list is capped at 20, and a
                lone count would read as the whole of it. */}
            <span
              title={`${data.blocked.length} shown of ${data.blocked_total} tasks blocked by work that is not finished`}
            >
              Blocked: {data.blocked.length} of {data.blocked_total}
            </span>
          </p>
          <ul className="space-y-0.5">
            {data.blocked.slice(0, 5).map((t) => (
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
        <div className="mt-3 border-t border-border pt-2">
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

/** (b) Who is overloaded? */
export function LoadPanel({ data }: { data: LoadReport }) {
  const overdue = accentForHue("red");
  const soon = accentForHue("amber");
  const later = accentForHue("gray");

  return (
    <Panel
      title="Who is overloaded"
      hint="Open tasks per person, split by when they are due. Unassigned is a bar, not a gap."
    >
      {data.people.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No open work in this scope.
        </p>
      ) : (
        <>
          <ul className="space-y-2">
            {data.people.map((row) => (
              <li key={row.assignee ?? "__unassigned"}>
                <div className="flex min-w-0 items-baseline gap-2 text-[11px]">
                  <span
                    // ⚠️ `pr-px` is not a nicety. Photographed 2026-09-16,
                    // "Unassigned" rendered as "Unassignea": an italic glyph
                    // leans past its own advance width, and `truncate`'s
                    // overflow clip cut the final letter at EVERY width.
                    className={`min-w-0 truncate pr-px ${row.assignee ? "" : "italic text-muted-foreground"}`}
                    title={
                      row.assignee ??
                      "Open work with nobody assigned. On a real board this is usually the largest bar."
                    }
                  >
                    {row.assignee ?? "Unassigned"}
                  </span>
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
                    title={`${row.open_tasks} open tasks: ${row.overdue} overdue, ${row.due_next_7d} due in the next 7 days, ${row.later} later or undated`}
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
            {data.total_tasks === 1 ? "task" : "tasks"} across{" "}
            {data.people_total}{" "}
            {data.people_total === 1 ? "person" : "people"}
          </p>
          <EffortLine data={data} />
        </>
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

/** (c) Are we getting faster? */
export function ThroughputPanel({ data }: { data: ThroughputReport }) {
  const done = statusAccent({ category: "done" });
  const dropped = statusAccent({ category: "cancelled" });
  const peak = Math.max(1, ...data.series.map((w) => w.completed));
  const { summary } = data;

  return (
    <Panel
      title="Are we getting faster"
      hint="Tasks finished each week, and how long they took from first started to done."
    >
      <div className="flex h-24 items-end gap-1">
        {data.series.map((w, i) => {
          const last = i === data.series.length - 1;
          const partial = last && data.current_week_partial;
          return (
            <div
              key={w.week_start}
              className="flex h-full flex-1 flex-col justify-end"
              title={
                `Week of ${w.week_start}: ${w.completed} finished` +
                (w.cancelled ? `, ${w.cancelled} cancelled` : "") +
                (w.median_hours !== null
                  ? `, median ${duration(w.median_hours)}`
                  : ", no measurable cycle time") +
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

      <dl className="mt-3 grid grid-cols-3 gap-2 border-t border-border pt-2 text-[11px]">
        <div>
          <dt className="text-muted-foreground">Finished</dt>
          <dd
            className={`font-semibold tabular-nums ${done.text}`}
            title={`${summary.completed} tasks reached a done status in the last ${data.weeks} weeks`}
          >
            {summary.completed}
          </dd>
        </div>
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
      </dl>

      {(summary.no_start > 0 || summary.cancelled > 0) && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {summary.no_start > 0 && (
            // ⚠️ Said out loud, because it is the median's denominator. A
            // reader who does not know these were excluded reads the median
            // as covering everything.
            <span
              title={`${summary.no_start} finished tasks never passed through In progress, so they have no measurable cycle time. They are left out of the median rather than counted as zero.`}
            >
              {summary.no_start} without a recorded start
            </span>
          )}
          {summary.no_start > 0 && summary.cancelled > 0 && " · "}
          {summary.cancelled > 0 && (
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

/** (d) What did we finish? */
export function FinishedPanel({ data }: { data: FinishedReport }) {
  const done = statusAccent({ category: "done" });
  const dropped = statusAccent({ category: "cancelled" });
  const peak = Math.max(1, ...data.projects.map((p) => p.completed));

  return (
    <Panel
      title="What we finished"
      hint={`Completed by project, ${period(data.period_start, data.period_end)}.`}
    >
      {data.projects.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          Nothing finished in this period.
        </p>
      ) : (
        <>
          <ul className="space-y-1.5">
            {data.projects.slice(0, 8).map((p) => (
              <li key={p.project_id}>
                <div className="flex items-baseline gap-2 text-[11px]">
                  <span className="min-w-0 truncate pr-px">{p.name}</span>
                  <span
                    className={`ml-auto font-medium tabular-nums ${done.text}`}
                    title={
                      `${p.completed} finished in ${p.name}` +
                      (p.cancelled ? `, ${p.cancelled} cancelled` : "") +
                      (p.median_hours !== null
                        ? `, median ${duration(p.median_hours)}`
                        : ", no measurable cycle time")
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
            {data.projects.length > 8 &&
              ` across ${data.projects.length} projects`}
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

function Verdict({ label, line }: { label: string; line: OutlookLine }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
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
  const velocity = velocityLine(data.velocity);
  const capacity = capacityLine(data.capacity);
  const slip = slipLine(data);
  const people = peopleLine(data);

  return (
    <Panel
      title="Will this land"
      hint="Forecast from what the team actually did, against what the plan would need. Estimated — this product records no hours worked."
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Verdict label="At the current rate" line={velocity} />
        <Verdict label="If the plan holds" line={capacity} />
        {slip ? <Verdict label="Against the plan" line={slip} /> : null}
        <Verdict label="Who is carrying it" line={people} />
      </div>
      {/* ⚠️ The two forecasts answer DIFFERENT questions, and a reader who
          thinks they are two attempts at one question will read their
          disagreement as a bug. Said once, under both. */}
      <p className="mt-3 border-t border-border pt-2 text-[11px] text-muted-foreground">
        The first reads the team&apos;s actual rate, and subtracts the rate work
        arrives. The second divides remaining estimated effort by the hours the
        assigned people have. A gap between them is the plan meeting reality.
      </p>
    </Panel>
  );
}
