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
 * Is this response complete enough to draw at all?
 *
 * ⚠️ **Found by ROUTING `/analytics/outlook` TO `{}` AND LOOKING, 2026-09-17.**
 * `headlineVerdict` read `o.velocity.verdict` and threw
 * *"Cannot read properties of undefined"*, which took the entire Projects
 * page down — not the panel, the page. In production that is the layout
 * boundary and a blank pane.
 *
 * ⚠️ **This is the FOURTH time this exact class has hit this component**, and
 * the cause is the same every time: `api.call` CASTS the response rather than
 * validating it, so a TypeScript interface here is a claim about the server
 * and not a check on it. `stuck.overdue` was a scalar typed as a list.
 * `stuck.stale` was a list typed as a record. Two panels read a field behind
 * only a `!data` guard. And now this.
 *
 * So the check lives at the boundary, once, and the panel draws nothing when
 * it fails. Nothing is the honest rendering of a response we cannot read —
 * the same rule the pane already applies to a rejected fetch.
 */
export function isDrawableOutlook(o: OutlookReport | null | undefined): boolean {
  if (!o || typeof o !== "object") return false;
  // `velocity` is the only block with no sensible default: every sentence on
  // the panel is keyed off its verdict.
  const v = (o as OutlookReport).velocity;
  return Boolean(v && typeof v === "object" && typeof v.verdict === "string");
}


/**
 * How much of the backlog the PLAN actually covers.
 *
 * ⚠️ "Planned to finish 12 Mar" over 4 of 71 open tasks is a claim about 4
 * tasks, and it reads identically to a claim about all 71. Any sentence that
 * quotes the planned date carries this.
 */
function planCaveat(o: OutlookReport): string {
  const plan = o?.plan;
  if (!plan || plan.tasks <= 0 || plan.dated >= plan.tasks) return "";
  const pct = Math.round((plan.dated / plan.tasks) * 100);
  return ` Only ${plan.dated} of ${plan.tasks} open tasks carry a due date (${pct}%).`;
}


/**
 * THE ANSWER, in one sentence, before any evidence.
 *
 * ⚠️ **Photographed 2026-09-17 and this is what it found.** The panel drew
 * six equal-weight facts and no verdict. The most important number on the
 * portfolio view — *79 days late* — sat in the third quadrant of one card,
 * in the same size and weight as "3 people". A reader had to synthesise four
 * sentences to learn whether the project was in trouble.
 *
 * An executive read is not a denser dashboard. It is the conclusion first,
 * with the evidence underneath for whoever wants to check it. That is the
 * same order BLUF asks of prose, applied to a screen.
 */
export function headlineVerdict(o: OutlookReport): OutlookLine {
  // Every read below is optional-chained. The outer guard proves
  // `velocity.verdict` exists; it proves nothing about `plan`.
  const v = o?.velocity;
  const slip = o?.plan?.slip_days;
  if (!v?.verdict) {
    return {
      headline: "—",
      detail: "No forecast came back from the server.",
      tone: "quiet",
    };
  }

  if (v.verdict === "nothing_left") {
    return {
      headline: "Nothing open",
      detail: "Every task in this scope is closed.",
      tone: "good",
    };
  }
  if (v.verdict === "not_converging") {
    // ⚠️ The loudest state the product has, and it earns it: no completion
    // date exists, and the reason is one subtraction the reader should not
    // have to do.
    return {
      headline: "Not converging",
      detail: `Work is arriving faster than it is finished — adding ${v.created_per_week} a week against ${v.finished_per_week} finished. No completion date exists at this rate.`,
      tone: "bad",
    };
  }
  if (v.verdict === "no_history") {
    return {
      headline: "Too early to forecast",
      detail: `${v.remaining_tasks} open, and fewer than three finished in ${v.weeks_sampled} weeks. Not enough to read a rate from.`,
      tone: "quiet",
    };
  }
  // Converging. The plan, when there is one, outranks the raw date — "79
  // days late" is the finding and "18 Feb 2027" is only how we know.
  if (typeof slip === "number" && slip > 0) {
    return {
      headline: `${slip} days late`,
      detail:
        `Forecast ${shortDate(v.finish_date)} against a plan of ${shortDate(o.plan.planned_finish)}, clearing ${v.net_per_week} a week net.` +
        planCaveat(o),
      tone: slip > 14 ? "bad" : "warn",
    };
  }
  if (typeof slip === "number" && slip < 0) {
    return {
      headline: `${Math.abs(slip)} days early`,
      detail: `Forecast ${shortDate(v.finish_date)} against a plan of ${shortDate(o.plan?.planned_finish)}.`,
      tone: "good",
    };
  }
  return {
    headline: `On track for ${shortDate(v.finish_date)}`,
    detail: `${v.remaining_tasks} open, clearing ${v.net_per_week} a week net after new work arriving.`,
    tone: "good",
  };
}

/**
 * What the two forecasts DISAGREEING means.
 *
 * ⚠️ **They sat five months apart on screen and nothing said so.** Both were
 * rendered as calm coloured dates, in the same size, and the reader was left
 * to subtract them and then work out which to believe. The gap is the most
 * informative thing on the panel, and it was the one thing not written down.
 *
 * Returns `null` when there is nothing to compare, or when the two agree
 * closely enough that saying anything would be noise.
 */
export function forecastGap(o: OutlookReport): OutlookLine | null {
  const a = o?.velocity?.weeks_remaining;
  const b = o?.capacity?.weeks_remaining;
  if (
    o?.velocity?.verdict !== "converging" ||
    o?.capacity?.verdict !== "ok" ||
    typeof a !== "number" ||
    typeof b !== "number"
  ) {
    return null;
  }
  const gap = a - b;
  // Under a month apart is two methods agreeing, not a finding.
  if (Math.abs(gap) < 4) return null;

  const cover = Math.round((o.capacity?.estimate_coverage ?? 0) * 100);
  // ⚠️ "1 weeks" — photographed 2026-09-17. A plural bug in a sentence whose
  // whole job is to be believed costs more than it looks like it should.
  const wk = (n: number) => `${n} ${n === 1 ? "week" : "weeks"}`;
  if (gap > 0) {
    // The team has more capacity on paper than its delivery rate uses.
    return {
      headline: wk(gap),
      detail:
        `The plan needs ${wk(b)} of the team's stated hours. The team's actual rate needs ${wk(a)}.` +
        (cover < 80
          ? ` Only ${cover}% of open tasks are sized, so the shorter figure may simply be missing work.`
          : " Either capacity is going elsewhere, or the estimates are optimistic."),
      tone: "warn",
    };
  }
  return {
    headline: wk(Math.abs(gap)),
    detail: `The team is delivering faster than the estimates imply. The remaining hours would take ${wk(b)}; the actual rate clears it in ${wk(a)}.`,
    tone: "good",
  };
}

/**
 * The velocity forecast as a sentence.
 *
 * ⚠️ `not_converging` is `bad` and everything else that lacks a date is
 * `quiet`. The difference matters: "we cannot tell yet" and "this will never
 * finish at the current rate" must not wear the same colour.
 */
export function velocityLine(v: VelocityForecast): OutlookLine {
  if (!v?.verdict) {
    return { headline: "—", detail: "Not returned.", tone: "quiet" };
  }
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
  if (!c?.verdict) {
    return { headline: "—", detail: "Not returned.", tone: "quiet" };
  }
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
 * The people line: how many are carrying work, and what we know about them.
 *
 * ⚠️ `leaving_within_90d` is a risk no velocity can see. Somebody whose
 * engagement ends inside the forecast window takes their throughput with
 * them, and the forecast above will not notice until after it happens.
 */
export function peopleLine(o: OutlookReport): OutlookLine {
  const p = o?.people;
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

/**
 * WS-27bn R3a — the slip as a range, for the bar that draws it.
 *
 * The track starts at the server's today and ends at the later of the two
 * dates. `plan` and `forecast` are positions on it, 0 to 100. `start` and
 * `width` are the filled segment between them, so the slip is the bar.
 *
 * ⚠️ **It computes positions and no figure.** Each input is the server's:
 * the forecast is today plus `weeks_remaining` whole weeks
 * (`analytics.py` `project_forecast`), and `slip_days` is the forecast
 * minus the plan (`slip_days`). So the plan sits `weeks_remaining` × 7 −
 * `slip_days` days from today, and no date is parsed in the browser.
 * `test_h185_3_the_forecast_is_today_plus_whole_weeks` in
 * `tests/unit/test_projects_report_sections_r3b.py` pins that link, so a
 * forecast that stops being whole weeks fails there first.
 *
 * Returns `null` when either date is absent. The verdicts `not_converging`,
 * `no_history` and `nothing_left` carry no forecast date, and a bar with one
 * end would draw a slip that nobody measured. The verdict line says why.
 */
export type SlipRange = {
  /** The plan's position on the track, 0 to 100. */
  plan: number;
  /** The forecast's position on the track, 0 to 100. */
  forecast: number;
  /** Where the filled segment starts, 0 to 100. */
  start: number;
  /** How wide the filled segment is, never under 1 so it stays visible. */
  width: number;
  /** "79 days late", "3 days early" or "on the plan date". */
  slip: string;
  /** The plan and the forecast as dates a person reads. */
  planLabel: string;
  forecastLabel: string;
  /** True when the plan date is already before today. */
  planPassed: boolean;
  tone: Tone;
  /** The whole range in one sentence, for a screen reader. */
  title: string;
};

export function slipRange(o: OutlookReport | null | undefined): SlipRange | null {
  const v = o?.velocity;
  const p = o?.plan;
  const slip = p?.slip_days;
  const weeks = v?.weeks_remaining;
  if (
    !v?.finish_date ||
    !p?.planned_finish ||
    typeof slip !== "number" ||
    !Number.isFinite(slip) ||
    typeof weeks !== "number" ||
    !Number.isFinite(weeks) ||
    weeks < 0
  ) {
    return null;
  }
  const forecastDays = weeks * 7;
  const planDaysRaw = forecastDays - slip;
  const planPassed = planDaysRaw < 0;
  const planDays = Math.max(0, planDaysRaw);
  const span = Math.max(forecastDays, planDays, 1);
  const plan = (planDays / span) * 100;
  const forecast = (forecastDays / span) * 100;
  const lo = Math.min(plan, forecast);
  const days = (n: number) => `${n} ${n === 1 ? "day" : "days"}`;
  const words =
    slip > 0
      ? `${days(slip)} late`
      : slip < 0
        ? `${days(Math.abs(slip))} early`
        : "on the plan date";
  const planLabel = shortDate(p.planned_finish);
  const forecastLabel = shortDate(v.finish_date);
  return {
    plan,
    forecast,
    start: lo,
    width: Math.max(1, Math.abs(forecast - plan)),
    slip: words,
    planLabel,
    forecastLabel,
    planPassed,
    tone: slip > 14 ? "bad" : slip > 0 ? "warn" : "good",
    title: `Plan ${planLabel}, forecast ${forecastLabel}: ${words}.`,
  };
}
