/**
 * Turning a forecast into a sentence, including the ones that refuse.
 *
 * ⚠️ **A refusal is the product here, not an error state.** The server
 * answers `not_converging`, `no_history`, `no_estimates`, `no_capacity` and
 * `nothing_left` far more often than it answers with a date, and each of
 * those is a finding a manager can act on. Rendering them as a dash would
 * throw away the whole point — *"scope is growing faster than delivery"* is
 * more useful than any date this endpoint could print.
 *
 * ⚠️ **Nothing here is a logged hour.** This product records none. Owner
 * direction 2026-09-17: derive the temporal shape from the activity spine,
 * `estimate_mins` and `due_at`. Every string below says "estimated" where it
 * means estimated, and none says "logged", "actual" or "tracked".
 */
import type { CapacityForecast, OutlookReport, VelocityForecast } from "./api";

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/**
 * "12 Mar 2027".
 *
 * ⚠️ Parsed by hand, never through `new Date()`. These are floating calendar
 * dates, and `new Date("2027-03-12")` is midnight UTC — a day west of
 * Greenwich, which names the wrong day on the reader's screen. The same
 * trap `ReportsView.periodLabel` already carries a note about.
 */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  const month = MONTHS[Number(m) - 1];
  if (!month) return "—";
  return `${Number(d)} ${month} ${y}`;
}

export type Tone = "good" | "warn" | "bad" | "quiet";

export type OutlookLine = {
  /** The headline, four words or so. */
  headline: string;
  /** Why, in one sentence. Never empty — a refusal must explain itself. */
  detail: string;
  tone: Tone;
};

/**
 * The velocity forecast as a sentence.
 *
 * ⚠️ `not_converging` is `bad` and everything else that lacks a date is
 * `quiet`. The difference matters: "we cannot tell yet" and "this will never
 * finish at the current rate" must not wear the same colour.
 */
export function velocityLine(v: VelocityForecast): OutlookLine {
  const rates = `Finishing ${v.finished_per_week}/wk · adding ${v.created_per_week}/wk, over ${v.weeks_sampled} weeks.`;
  switch (v.verdict) {
    case "converging":
      return {
        headline: shortDate(v.finish_date),
        detail: `${v.remaining_tasks} tasks left, clearing at ${v.net_per_week}/wk net. ${rates}`,
        tone: "good",
      };
    case "not_converging":
      // ⚠️ The two rates ARE the finding. A refusal with no numbers reads as
      // a broken feature; with them it reads as the problem it is.
      return {
        headline: "Not converging",
        detail: `Work is arriving at least as fast as it is finished, so no completion date exists at this rate. ${rates}`,
        tone: "bad",
      };
    case "no_history":
      return {
        headline: "Too early to say",
        detail: `Fewer than three tasks finished in the last ${v.weeks_sampled} weeks. A rate needs more than that.`,
        tone: "quiet",
      };
    case "nothing_left":
      return {
        headline: "Nothing open",
        detail: "Every task in this scope is closed.",
        tone: "good",
      };
    default:
      return {
        headline: "—",
        detail: "This deployment did not return a forecast.",
        tone: "quiet",
      };
  }
}

/** The capacity forecast as a sentence. */
export function capacityLine(c: CapacityForecast): OutlookLine {
  const cover = Math.round((c.estimate_coverage ?? 0) * 100);
  switch (c.verdict) {
    case "ok":
      return {
        headline: shortDate(c.finish_date),
        detail:
          `${c.hours_left}h of estimated work left against ${c.hours_per_week}h a week` +
          (cover < 100
            ? `, sized over ${cover}% of open tasks — so the real figure is higher.`
            : ", across every open task."),
        tone: cover < 50 ? "warn" : "good",
      };
    case "no_estimates":
      return {
        headline: "Nothing sized",
        // Zero hours and nothing sized render identically as "0h", and only
        // one of them means the project is finished.
        detail:
          "No open task carries an estimate, so remaining effort cannot be" +
          " computed. Set estimates to see a date here.",
        tone: "quiet",
      };
    case "no_capacity":
      return {
        headline: "No capacity on file",
        // ⚠️ Assuming forty hours would put a confident date on a number the
        // product never asked anybody for.
        detail:
          "Nobody holding open work has stated weekly hours, so this cannot" +
          " be divided into a date.",
        tone: "quiet",
      };
    case "nothing_left":
      return {
        headline: "Nothing open",
        detail: "No open work to estimate.",
        tone: "good",
      };
    default:
      return { headline: "—", detail: "Not returned.", tone: "quiet" };
  }
}

/**
 * The plan, and how far the forecast has drifted from it.
 *
 * Returns `null` when there is nothing honest to say — no plan, or no
 * forecast to compare it against.
 */
export function slipLine(o: OutlookReport): OutlookLine | null {
  const { plan } = o;
  if (!plan || !plan.planned_finish) return null;
  const covered = plan.tasks > 0 ? Math.round((plan.dated / plan.tasks) * 100) : 0;
  const caveat =
    plan.dated < plan.tasks
      ? ` Based on the ${plan.dated} of ${plan.tasks} open tasks that carry a due date (${covered}%).`
      : "";

  if (plan.slip_days === null || plan.slip_days === undefined) {
    return {
      headline: shortDate(plan.planned_finish),
      detail: `The last due date on open work.${caveat}`,
      tone: "quiet",
    };
  }
  if (plan.slip_days > 0) {
    return {
      headline: `${plan.slip_days} days late`,
      detail: `Planned ${shortDate(plan.planned_finish)}, forecast ${shortDate(o.velocity.finish_date)}.${caveat}`,
      tone: plan.slip_days > 14 ? "bad" : "warn",
    };
  }
  if (plan.slip_days < 0) {
    return {
      headline: `${Math.abs(plan.slip_days)} days early`,
      detail: `Planned ${shortDate(plan.planned_finish)}, forecast ${shortDate(o.velocity.finish_date)}.${caveat}`,
      tone: "good",
    };
  }
  return {
    headline: "On the plan",
    detail: `Planned and forecast both ${shortDate(plan.planned_finish)}.${caveat}`,
    tone: "good",
  };
}

/**
 * The people line: how many are carrying work, and what we know about them.
 *
 * ⚠️ `leaving_within_90d` is a risk no velocity can see. Somebody whose
 * engagement ends inside the forecast window takes their throughput with
 * them, and the forecast above will not notice until after it happens.
 */
export function peopleLine(o: OutlookReport): OutlookLine {
  const p = o.people;
  const n = p?.holding_open_work ?? 0;
  if (n === 0) {
    return {
      headline: "Nobody assigned",
      detail:
        "No open task in this scope has an assignee, so there is no team to" +
        " measure. That is usually the finding.",
      tone: "warn",
    };
  }
  const known = p.with_stated_capacity ?? 0;
  const detail =
    known === 0
      ? "None of them has stated weekly hours, so capacity is unknown."
      : known < n
        ? `${known} of ${n} have stated weekly hours, totalling ${p.hours_per_week}h a week.`
        : `${p.hours_per_week}h a week between them.`;
  const risk =
    p.leaving_within_90d > 0
      ? ` ⚠️ ${p.leaving_within_90d} ${p.leaving_within_90d === 1 ? "engagement ends" : "engagements end"} within 90 days.`
      : "";
  return {
    headline: `${n} ${n === 1 ? "person" : "people"}`,
    detail: detail + risk,
    tone: p.leaving_within_90d > 0 ? "warn" : known === 0 ? "quiet" : "good",
  };
}
