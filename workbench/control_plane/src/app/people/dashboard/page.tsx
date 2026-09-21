"use client";

/**
 * People Center · the people-management dashboard (WS-28j1).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.7 · **D-PC-14**.
 *
 * Owner-directed 2026-08-13: *"the person looking at this dashboard should have
 * all the intelligence and needs to be able to actually make those
 * decisions."* So every figure a decision needs sits on the row — projects,
 * open tasks, the next deadline, committed against contracted hours, the
 * unestimated count, last activity — and the pill states its own reason rather
 * than asking anyone to take it on trust.
 *
 * ⚠️ **A measurement surface, not a performance one.** Every number here is
 * trivially gamed and trivially misread. Rows are ordered by the urgency of the
 * WORK, never scored; there is no leaderboard and no per-person trend line
 * presented as an evaluation. Ranking tasks by risk is the product; ranking
 * people by output is not (D-PC-14, and `test_people_dashboard.py` greps this
 * file for the words that would mean it had become one).
 *
 * ⚠️ **Nothing here writes.** Expanding a row reads `/people/{id}/work`; the
 * pre-filled assign action the suggester ends in is WS-28j3's, and it is a
 * human's click either way (D-PC-13).
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import PageHeader from "@/components/PageHeader";
import { accentForHue } from "@/lib/statusAccent";

import { Avatar } from "../components/Avatar";
import { type WorkRow, peopleApi } from "../lib/api";
import { PAGE_FRAME } from "../lib/frame";
import {
  type SuggestionsResponse,
  describeCandidate,
  describePickup,
} from "../lib/suggestions";
import {
  type DashboardResponse,
  type DashboardRow,
  PILL_HUE,
  PILL_LABEL,
  type Pill,
  type Rollup,
  capacityBar,
  describeActivity,
  describeDeadline,
  describeRollup,
  describeScope,
  describeSpread,
  groupByDepartment,
  hours,
  NO_DEPARTMENT,
  pillTotals,
  sortRows,
} from "../lib/dashboard";

export default function WorkloadDashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Pill | null>(null);
  const [department, setDepartment] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [sug, setSug] = useState<SuggestionsResponse | null>(null);
  const [assigned, setAssigned] = useState<string | null>(null);

  // The read, inline with a `live` guard rather than a `useCallback` the effect
  // then calls. Both shapes exist in this tree; only this one satisfies
  // `react-hooks/set-state-in-effect`, which cannot see through the callback to
  // tell that every setState there already follows an await. `loading` starts
  // true, so nothing is set before the first one.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await peopleApi.dashboard();
        if (!live) return;
        setData(res);
        setError(null);
      } catch (err) {
        if (!live) return;
        // The gateway refuses with the sentence naming the grant. Shown
        // verbatim — a page that says "forbidden" teaches nobody what to ask
        // for.
        setError((err as Error).message);
        setData(null);
      }
      if (live) setLoading(false);
    })();
    return () => {
      live = false;
    };
  }, []);

  // WS-28j3 — fetched after the board, not with it: the board is the page
  // and must not wait on the suggester's extra queries.
  useEffect(() => {
    if (!data) return;
    let live = true;
    (async () => {
      try {
        const res = await peopleApi.suggestions();
        if (live) setSug(res);
      } catch {
        if (live) setSug(null); // the board stands on its own
      }
    })();
    return () => {
      live = false;
    };
  }, [data]);

  /**
   * §5.7.4's "pre-filled assign action a human confirms" — the confirm IS the
   * human act, and the write is the Projects app's ordinary assignees PUT.
   */
  async function assignHelper(taskId: string, title: string, helper: string) {
    if (!window.confirm(`Assign ${helper} to “${title}”?`)) return;
    try {
      await peopleApi.assignHelper(taskId, helper);
      setAssigned(`${helper} assigned to “${title}”.`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const rows = useMemo(() => sortRows(data?.rows ?? []), [data]);
  const totals = useMemo(() => pillTotals(rows), [rows]);
  const shown = useMemo(
    () =>
      rows.filter(
        (r) =>
          (!filter || r.pill === filter) &&
          // The rollup's own grouping key, so clicking a department row and
          // reading its section can never select different people.
          (!department ||
            (r.department?.trim() || NO_DEPARTMENT) === department)
      ),
    [rows, filter, department]
  );
  const groups = useMemo(() => groupByDepartment(shown), [shown]);
  const scope = data ? describeScope(data) : null;

  return (
    <main className={PAGE_FRAME}>
      <PageHeader
        title="Workload"
        subtitle="What everybody is holding, what is due, and where the week does not fit. Every figure is about tasks and dates."
        actions={
          <>
          <Link href="/people/quality">
            <Button variant="secondary" size="sm" icon="ShieldCheck">
              Data quality
            </Button>
          </Link>
          <Link href="/people/schedule">
            <Button variant="secondary" size="sm" icon="CalendarDays">
              Working week
            </Button>
          </Link>
          </>
        }
      />

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {scope && (
        <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <Icon name="Info" className="mt-0.5 size-3 shrink-0" />
          {scope}
        </p>
      )}

      {data && (
        <section
          className="flex flex-wrap gap-1.5"
          aria-label="Filter by signal"
        >
          {(Object.keys(PILL_LABEL) as Pill[]).map((pill) => {
            const accent = accentForHue(PILL_HUE[pill]);
            const on = filter === pill;
            return (
              <button
                key={pill}
                type="button"
                onClick={() => setFilter(on ? null : pill)}
                aria-pressed={on}
                className={`cc-control flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] ${
                  on
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted/40"
                }`}
              >
                <span className={`size-1.5 rounded-full ${accent.dot}`} />
                {PILL_LABEL[pill]}
                <span className="font-medium text-foreground">
                  {totals[pill]}
                </span>
              </button>
            );
          })}
          {(filter || department) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setFilter(null);
                setDepartment(null);
              }}
            >
              Clear
            </Button>
          )}
        </section>
      )}

      {data && data.departments.length > 0 && (
        <RollupPanel
          org={data.org}
          departments={data.departments}
          filter={department}
          onFilter={setDepartment}
        />
      )}

      {/* ⚠️ The FIELDS are guarded, not just the object. A response that
          arrives without `at_risk` — a version skew, a degraded answer —
          used to throw here and blank the whole Workload page, not just
          this section. `harness.ts` records that class of crash as its
          eighth trap, and this is one of the components it means. */}
      {sug && ((sug.at_risk?.length ?? 0) > 0 || (sug.pickups?.length ?? 0) > 0) && (
        <section className="rounded-xl border border-border">
          <div className="border-b border-border p-3">
            <h2 className="text-xs font-medium text-foreground">
              Rebalancing suggestions
            </h2>
            <p className="text-[11px] text-muted-foreground">
              Ranked by matched skill × spare hours × availability — every
              number shown, nothing assigned without your confirm.
              {sug.truncated ? " List trimmed; the worst cases are first." : ""}
            </p>
          </div>
          {assigned && (
            <p className="border-b border-border p-3 text-[11px] text-muted-foreground">
              {assigned}
            </p>
          )}
          {sug.at_risk.map((item) => (
            <div key={item.task_id} className="border-b border-border p-3 last:border-0">
              <p className="text-xs text-foreground">
                {item.title}
                {item.project_name ? (
                  <span className="text-muted-foreground"> · {item.project_name}</span>
                ) : null}
                <span className="text-muted-foreground">
                  {" "}— {item.holder.name} is short{" "}
                  {item.shortfall_hours ? `${item.shortfall_hours}h` : "time"}
                  {item.due_on ? ` before ${item.due_on}` : ""}
                </span>
              </p>
              {item.candidates.length === 0 ? (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Nobody free has a matching skill on record — which may mean
                  the skill exists and nobody wrote it down.
                </p>
              ) : (
                <ul className="mt-1 flex flex-col gap-1">
                  {item.candidates.map((c) => (
                    <li
                      key={c.email}
                      className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground"
                    >
                      <span className="text-foreground">{c.name}</span>
                      {describeCandidate(c)}
                      <Button
                        size="sm"
                        variant="secondary"
                        icon="UserPlus"
                        onClick={() =>
                          void assignHelper(item.task_id, item.title, c.email)
                        }
                      >
                        Assign
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          {sug.pickups.map((pickup) => (
            <div
              key={pickup.email}
              className="border-b border-border p-3 last:border-0"
            >
              <p className="text-xs text-foreground">
                {pickup.name}
                <span className="text-muted-foreground">
                  {" "}is idle — could pick up:
                </span>
              </p>
              <ul className="mt-1 flex flex-col gap-1">
                {pickup.tasks.map((task) => (
                  <li
                    key={`${pickup.email}-${task.task_id}`}
                    className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground"
                  >
                    <span className="text-foreground">{task.title}</span>
                    {describePickup(task)}
                    <Button
                      size="sm"
                      variant="secondary"
                      icon="UserPlus"
                      onClick={() =>
                        void assignHelper(task.task_id, task.title, pickup.email)
                      }
                    >
                      Assign
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}

      {loading && (
        <p className="text-xs text-muted-foreground">Reading the roster…</p>
      )}

      {data && !loading && shown.length === 0 && (
        <p className="text-xs text-muted-foreground">
          {filter
            ? `Nobody${department ? ` in ${department}` : ""} is ${PILL_LABEL[
                filter
              ].toLowerCase()} right now.`
            : department
              ? `Nobody is in ${department}.`
              : "No people yet. Add somebody in the directory."}
        </p>
      )}

      {groups.map((group) => (
        <section key={group.department} className="flex flex-col gap-1.5">
          <h2 className="text-[11px] font-medium text-muted-foreground">
            {group.department}
            <span className="ml-1.5 font-normal">{group.rows.length}</span>
          </h2>
          {group.rows.map((row) => (
            <PersonCard
              key={row.person_id ?? row.name}
              row={row}
              open={openId === (row.person_id ?? row.name)}
              onToggle={() =>
                setOpenId((id) =>
                  id === (row.person_id ?? row.name)
                    ? null
                    : (row.person_id ?? row.name)
                )
              }
            />
          ))}
        </section>
      ))}
    </main>
  );
}

/**
 * The department rollup (WS-28j2, §5.7.3).
 *
 * ⚠️ Every figure here is the **server's**, computed from the same rows the
 * table below renders. Nothing on this panel is summed in the browser — a
 * rollup that recomputes a number the app already shows is how two numbers
 * start disagreeing where nobody can see them (§5.9).
 *
 * Ordered by strain, because a rollup nobody can act on is a table. That is an
 * ordering of *work*: the strip beside each row is a count of tasks and hours,
 * and there is no score anywhere on it (D-PC-14).
 */
function RollupPanel({
  org,
  departments,
  filter,
  onFilter,
}: {
  org: Rollup;
  departments: Rollup[];
  filter: string | null;
  onFilter: (department: string | null) => void;
}) {
  return (
    <section className="rounded-xl border border-border">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border p-3">
        <h2 className="text-xs font-medium text-foreground">
          Everyone
          <span className="ml-1.5 font-normal text-muted-foreground">
            {org.headcount} across {org.departments ?? departments.length}{" "}
            departments
            {org.agents ? ` · ${org.agents} agents, not counted here` : ""}
          </span>
        </h2>
        <p className="text-[11px] text-muted-foreground">
          {describeRollup(org)}
        </p>
      </div>

      <ul>
        {departments.map((group) => {
          const on = filter === group.department;
          const spread = describeSpread(group);
          return (
            <li
              key={group.department}
              className="border-b border-border/60 last:border-0"
            >
              <button
                type="button"
                onClick={() => onFilter(on ? null : group.department)}
                aria-pressed={on}
                className={`cc-control flex w-full flex-col items-start gap-0.5 p-3 text-left ${
                  on ? "bg-primary/10" : "hover:bg-muted/40"
                }`}
              >
                <span className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={`text-xs font-medium ${
                      on ? "text-primary" : "text-foreground"
                    }`}
                  >
                    {group.department}
                  </span>
                  {(Object.keys(PILL_LABEL) as Pill[])
                    .filter((pill) => group.pills[pill] > 0)
                    .map((pill) => (
                      <span
                        key={pill}
                        className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                          accentForHue(PILL_HUE[pill]).chip
                        }`}
                      >
                        {group.pills[pill]} {PILL_LABEL[pill].toLowerCase()}
                      </span>
                    ))}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {describeRollup(group)}
                </span>
                {spread && (
                  <span className="text-[11px] text-muted-foreground">
                    {spread}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function PillBadge({ pill }: { pill: Pill }) {
  const accent = accentForHue(PILL_HUE[pill]);
  return (
    <span
      className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${accent.chip}`}
    >
      {PILL_LABEL[pill]}
    </span>
  );
}

function PersonCard({
  row,
  open,
  onToggle,
}: {
  row: DashboardRow;
  open: boolean;
  onToggle: () => void;
}) {
  const bar = capacityBar(row);
  return (
    <article className="rounded-xl border border-border">
      <div className="flex flex-wrap items-center gap-3 p-3">
        <Avatar name={row.name} avatar={row.avatar} className="size-8 text-xs" />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {row.person_id ? (
              <Link
                href={`/people?person=${row.person_id}`}
                className="truncate text-xs font-medium text-foreground hover:underline"
              >
                {row.name}
              </Link>
            ) : (
              <span className="truncate text-xs font-medium text-foreground">
                {row.name}
              </span>
            )}
            {row.kind === "agent" && (
              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                Agent
              </span>
            )}
            {/* An assignee with no directory row — somebody who left, or an
                address nobody added. Invisible everywhere else in the product. */}
            {row.kind === "person" && !row.person_id && (
              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                Not in the directory
              </span>
            )}
            {row.pill && <PillBadge pill={row.pill} />}
            {row.away && (
              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {row.away.kind === "partial" ? "Part-day" : "Away"} until{" "}
                {row.away.until}
              </span>
            )}
          </div>
          {/* The pill's own reason, always beside it. A pill without its reason
              is a verdict, and this surface does not issue verdicts. */}
          {row.reason && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {row.reason}
            </p>
          )}
          {row.note && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {row.note}
            </p>
          )}
        </div>

        <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
          <Figure label="Open" value={String(row.open_tasks)} />
          <Figure
            label="Projects"
            value={
              row.projects_total > 0
                ? row.projects
                    .map((p) => p.name)
                    .join(", ")
                    .concat(
                      row.projects_total > row.projects.length
                        ? ` +${row.projects_total - row.projects.length}`
                        : ""
                    )
                : "—"
            }
          />
          <Figure
            label="Next"
            value={describeDeadline(row.next_due_at)}
          />
          <Figure
            label="Spare"
            value={row.hours_basis ? hours(row.spare_hours_this_week) : "—"}
          />
          <Figure label="Activity" value={describeActivity(row.last_activity_at)} />
        </dl>

        <Button
          variant="ghost"
          size="sm"
          icon={open ? "ChevronUp" : "ChevronDown"}
          onClick={onToggle}
          aria-expanded={open}
        >
          {open ? "Hide" : "Tasks"}
        </Button>
      </div>

      <div className="px-3 pb-3">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          {!bar.unknown && (
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${bar.percent}%` }}
            />
          )}
        </div>
        <p className="mt-1 text-[10px] text-muted-foreground">{bar.label}</p>
      </div>

      {row.at_risk.length > 0 && (
        <ul className="border-t border-border px-3 py-2 text-[11px]">
          {row.at_risk.map((task) => (
            <li key={task.task_id} className="text-muted-foreground">
              <span className="text-foreground">{task.title}</span>
              {task.project_name ? ` · ${task.project_name}` : ""} · due{" "}
              {task.due_on} · needs {hours(task.needed_hours)}, has{" "}
              {hours(task.available_hours)}
            </li>
          ))}
        </ul>
      )}

      {open && <TaskList row={row} />}
    </article>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[10px] text-muted-foreground">{label}</dt>
      <dd className="max-w-[16rem] truncate text-foreground">{value}</dd>
    </div>
  );
}

/**
 * The expanded row (§5.7.1) — read through `/people/{id}/work`, which already
 * scopes a person's open tasks by the VIEWER's grants. A second task read here
 * would be a second scoping rule to keep in step with the first.
 *
 * Sorted by urgency rather than by project: the question being asked is "what
 * is at risk", not "what belongs where".
 */
function TaskList({ row }: { row: DashboardRow }) {
  const [tasks, setTasks] = useState<WorkRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // Nothing to fetch, and nothing to set: the render below answers this case
    // from `row.person_id` directly, before it ever consults `tasks`.
    if (!row.person_id) return;
    (async () => {
      try {
        const res = await peopleApi.work(row.person_id as string);
        if (live) setTasks(res.rows);
      } catch (err) {
        if (live) setError((err as Error).message);
      }
    })();
    return () => {
      live = false;
    };
  }, [row.person_id]);

  if (error) {
    return (
      <p className="border-t border-border px-3 py-2 text-[11px] text-destructive">
        {error}
      </p>
    );
  }
  if (!row.person_id) {
    return (
      <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        No directory record, so there is no person page to open. Add them in the
        directory to see their tasks here.
      </p>
    );
  }
  if (!tasks) {
    return (
      <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        Reading their tasks…
      </p>
    );
  }
  if (tasks.length === 0) {
    return (
      <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        Nothing open in the projects you can see.
      </p>
    );
  }
  return (
    <table className="w-full border-t border-border text-[11px]">
      <tbody>
        {tasks.map((task) => (
          <tr key={task.id} className="border-b border-border/60 last:border-0">
            <td className="px-3 py-1.5 text-foreground">{task.title}</td>
            <td className="px-3 py-1.5 text-muted-foreground">
              {task.project_name ?? "—"}
            </td>
            <td className="px-3 py-1.5 text-muted-foreground">
              {task.status_name}
            </td>
            <td className="px-3 py-1.5 text-right text-muted-foreground">
              {describeDeadline(task.due_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
