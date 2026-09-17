/**
 * Estimated effort, and the coverage that decides whether it may be shown.
 *
 * ⚠️ **THERE IS NO TIME TRACKING IN THIS PRODUCT.** `pm_tasks` carries
 * `estimate_mins` and no actual. `pm_task_personal.actual_start/end` is the
 * Calendar's per-member timeboxing — private, sparse, and not a project
 * effort log. So every figure here is a PLAN, and nothing in this file may
 * render the words "logged", "actual" or "tracked".
 *
 * Owner decision, 2026-09-17: work spent and work left are **estimated
 * effort in hours**, taken with that constraint stated.
 *
 * ⚠️ **The rule this file exists to enforce: an hours figure never appears
 * without its coverage.** A sum over 3 of 30 estimated tasks is not the
 * remaining work, it is a tenth of it, and it looks identical on screen. A
 * reader who sees "40h left" on a project with 27 unsized tasks will plan
 * against a number that is wrong by an order of magnitude and has no way to
 * tell. Measured 2026-09-17: the live scratch tree carried **zero** estimates
 * across 37 tasks, which is the state every project starts in.
 *
 * So `effortDisplay` returns `kind: "none"` when nothing is sized, and the
 * panel prints an invitation rather than `0h`. Zero hours and zero estimates
 * are different facts, and only one of them means "no work left".
 */
import type { EffortReport, LoadRow } from "./api";

/** Minutes as a person reads them. Never a bare decimal hour. */
export function hours(mins: number | null | undefined): string {
  if (mins === null || mins === undefined || !Number.isFinite(mins)) return "—";
  if (mins <= 0) return "0h";
  if (mins < 60) return `${Math.round(mins)}m`;
  const h = mins / 60;
  // Under a day, a half hour matters. Past that it is noise on a plan.
  if (h < 10) return `${Math.round(h * 2) / 2}h`;
  if (h < 80) return `${Math.round(h)}h`;
  // ⚠️ Days, at 8 hours — NOT 24. A person-day of work is a working day, and
  // dividing by 24 would report a fortnight of effort as "five days".
  return `${Math.round(h / 8)}d`;
}

export type Coverage = {
  /** Tasks carrying an estimate. */
  estimated: number;
  /** Tasks in scope. */
  total: number;
  /** 0–100, rounded. `0` when nothing is sized, `100` when all of it is. */
  pct: number;
  /** Every task in scope is sized, so the sum is the whole answer. */
  complete: boolean;
  /** How many carry no estimate. The number a reader can act on. */
  missing: number;
};

export function coverage(estimated: number, total: number): Coverage {
  const e = Number.isFinite(estimated) ? Math.max(0, estimated) : 0;
  const t = Number.isFinite(total) ? Math.max(0, total) : 0;
  return {
    estimated: e,
    total: t,
    pct: t > 0 ? Math.round((e / t) * 100) : 0,
    // ⚠️ An EMPTY scope is complete, not incomplete. A project with no open
    // tasks has nothing unsized, and calling that 0% coverage would put a
    // warning on a finished project.
    complete: e >= t,
    missing: Math.max(0, t - e),
  };
}

export type EffortDisplay =
  | {
      /** Nothing in scope carries an estimate. Show an invitation, not `0h`. */
      kind: "none";
      /** Tasks that could be sized and are not. */
      unsized: number;
    }
  | {
      kind: "some";
      leftLabel: string;
      spentLabel: string;
      left: Coverage;
      spent: Coverage;
      /**
       * Share of the estimated total already done, 0–100 — or `null` when
       * the two halves do not share enough coverage to compare.
       *
       * ⚠️ Comparing a well-estimated backlog against a barely-estimated
       * history reports progress that is an artefact of who filled in forms.
       */
      donePct: number | null;
    };

/** How much coverage each half needs before the two may be compared. */
const COMPARABLE_PCT = 50;

export function effortDisplay(
  effort: EffortReport | null | undefined
): EffortDisplay | null {
  // Absent means the server did not say, which a client must not render as
  // any number at all. `null` draws nothing.
  if (!effort || typeof effort.left_mins !== "number") return null;

  const left = coverage(effort.left_estimated ?? 0, effort.left_tasks ?? 0);
  const spent = coverage(effort.spent_estimated ?? 0, effort.spent_tasks ?? 0);

  if (left.estimated === 0 && spent.estimated === 0) {
    return { kind: "none", unsized: left.total };
  }

  const total = (effort.left_mins ?? 0) + (effort.spent_mins ?? 0);
  const comparable =
    total > 0 &&
    (left.total === 0 || left.pct >= COMPARABLE_PCT) &&
    (spent.total === 0 || spent.pct >= COMPARABLE_PCT);

  return {
    kind: "some",
    leftLabel: hours(effort.left_mins),
    spentLabel: hours(effort.spent_mins),
    left,
    spent,
    donePct: comparable
      ? Math.round(((effort.spent_mins ?? 0) / total) * 100)
      : null,
  };
}

/**
 * One person's plate, in hours, or `null` when none of it is sized.
 *
 * ⚠️ Returns `null` rather than `"0h"` for an unsized plate. Somebody
 * carrying nine unsized tasks is not carrying zero hours of work, and a
 * column of `0h` beside real figures says the opposite.
 */
export function personEffort(
  row: LoadRow
): { label: string; cover: Coverage } | null {
  const estimated = row.estimated ?? 0;
  if (estimated <= 0) return null;
  return {
    label: hours(row.est_mins ?? 0),
    cover: coverage(estimated, row.open_tasks ?? 0),
  };
}
