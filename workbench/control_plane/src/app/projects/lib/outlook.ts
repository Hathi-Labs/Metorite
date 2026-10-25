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
 *
 * ⚠️ **WS-27bn RA-6: the forecast is by COUNT** (`projects_reports.md` §6.8
 * B). The server runs the past weeks again 2,000 times and sends the 50% and
 * 85% dates, the converge line and one sentence. This file computes no date.
 * It chooses words, and it places the two dates on a track.
 */
import { accentForSlot, type CategoricalAccent } from "@/lib/categorical";

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
  // WS-27bn RA-6. The server's sentence comes first (§6.8 E5). An older
  // server sends none, and the old words stand in.
  const said = typeof v.summary === "string" && v.summary ? v.summary : null;
  if (v.verdict === "not_converging") {
    // ⚠️ The loudest state the product has, and it earns it: no completion
    // date exists, and the reason is one subtraction the reader should not
    // have to do.
    return {
      headline: "Not converging",
      detail:
        said ??
        `Work is arriving faster than it is finished — adding ${v.created_per_week} a week against ${v.finished_per_week} finished. No completion date exists at this rate.`,
      tone: "bad",
    };
  }
  if (v.verdict === "no_history") {
    // WS-27bn RA-6: the §5 word is "Not enough history".
    return {
      headline: "Not enough history",
      detail:
        said ??
        `${v.remaining_tasks} open, and too few finished in ${v.weeks_sampled} weeks. Not enough to read a rate from.`,
      tone: "quiet",
    };
  }
  // Converging. The plan, when there is one, outranks the raw date — "79
  // days late" is the finding and "18 Feb 2027" is only how we know.
  if (typeof slip === "number" && slip > 0) {
    return {
      headline: `${slip} days late`,
      detail:
        (said
          ? `${said} The plan is ${shortDate(o.plan.planned_finish)}.`
          : `Forecast ${shortDate(v.finish_date)} against a plan of ${shortDate(o.plan.planned_finish)}, clearing ${v.net_per_week} a week net.`) +
        planCaveat(o),
      tone: slip > 14 ? "bad" : "warn",
    };
  }
  if (typeof slip === "number" && slip < 0) {
    return {
      headline: `${Math.abs(slip)} days early`,
      detail: said
        ? `${said} The plan is ${shortDate(o.plan?.planned_finish)}.`
        : `Forecast ${shortDate(v.finish_date)} against a plan of ${shortDate(o.plan?.planned_finish)}.`,
      tone: "good",
    };
  }
  return {
    headline: `On track for ${shortDate(v.finish_date)}`,
    detail:
      said ??
      `${v.remaining_tasks} open, clearing ${v.net_per_week} a week net after new work arriving.`,
    tone: "good",
  };
}

/**
 * WS-27bn RA-6 — the rule of the forecast that is on screen, for its
 * `InfoTip`, so a keyboard and a touch reach it (§6.8 E6). `null` when there
 * is nothing to explain. Each figure in it is the server's.
 */
export function forecastRule(v: VelocityForecast | null | undefined): string | null {
  if (!v?.verdict || v.verdict === "nothing_left") return null;
  const weeks = v.weeks_sampled ?? 6;
  const runs = (v.trials ?? 2000).toLocaleString("en-US");
  const cap = v.cap_weeks ?? 52;
  if (v.verdict === "no_history") {
    const need = v.min_weeks_with_finishes ?? 4;
    return (
      `A forecast needs ${need} of the last ${weeks} weeks with a finished task.` +
      " With fewer, the runs would say more than the history knows."
    );
  }
  if (v.verdict === "not_converging") {
    return (
      "Not converging: new work arrives at least as fast as work finishes," +
      ` or fewer than 85% of ${runs} runs finish within ${cap} weeks.` +
      " The rate that converges it is the new tasks of a week, plus the" +
      ` open tasks spread over ${cap} weeks.`
    );
  }
  return (
    `The forecast plays the last ${weeks} weeks again, ${runs} times.` +
    " Each run picks past weeks at random: a week's finished tasks come off" +
    " the open count, and its new tasks go back on. The 50% date is the week" +
    " by which half the runs finish, and the 85% date the week by which 85%" +
    " finish. No estimate is used."
  );
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
  // Both figures carry one decimal, so the raw difference can print as
  // "8.700000000000001 weeks" (visual review, 2026-09-26). Round to one.
  const gap = Math.round((a - b) * 10) / 10;
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
        `The plan needs ${wk(b)} of the team's working hours. The team's actual rate needs ${wk(a)}.` +
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
        // WS-27bn RA-6. The headline is the 50% date, so the 85% date
        // comes first in the detail.
        detail: v.p85_date
          ? `${v.remaining_tasks} open: 50% by then, 85% by ${shortDate(v.p85_date)}. ${rates}`
          : `${v.remaining_tasks} tasks left, clearing at ${v.net_per_week}/wk net. ${rates}`,
        tone: "good",
      };
    case "not_converging":
      // ⚠️ The two rates ARE the finding. A refusal with no numbers reads as
      // a broken feature; with them it reads as the problem it is. WS-27bn
      // RA-6 adds the rate that would converge it.
      return {
        headline: "Not converging",
        detail:
          (typeof v.converge_per_week === "number"
            ? `To converge, it needs ${v.converge_per_week} finished a week. `
            : "Work is arriving at least as fast as it is finished, so no completion date exists at this rate. ") +
          rates,
        tone: "bad",
      };
    case "no_history":
      return {
        headline: "Not enough history",
        detail:
          typeof v.weeks_with_finishes === "number"
            ? `${v.weeks_with_finishes} of the last ${v.weeks_sampled} weeks had a finished task. A forecast needs ${v.min_weeks_with_finishes ?? 4}.`
            : `Too few tasks finished in the last ${v.weeks_sampled} weeks. A rate needs more than that.`,
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

/**
 * WS-27bm S11 (`projects_ai_chat.md` §17.3 rule 10). A reader without
 * `admin:members:read` gets hours that ignore leave, so the panel says so.
 * The sentence is exported so the test pins the exact words.
 */
export const LEAVE_NOT_COUNTED =
  " This forecast does not count leave. An admin sees it with leave.";

/**
 * The capacity forecast as a sentence.
 *
 * `absencesApplied` is `people.absences_applied`. Only an explicit `false`
 * adds the leave sentence. An older server sends nothing, and the panel
 * then makes no claim about leave either way.
 */
export function capacityLine(
  c: CapacityForecast,
  absencesApplied?: boolean,
): OutlookLine {
  if (!c?.verdict) {
    return { headline: "—", detail: "Not returned.", tone: "quiet" };
  }
  const cover = Math.round((c.estimate_coverage ?? 0) * 100);
  const leave = absencesApplied === false ? LEAVE_NOT_COUNTED : "";
  switch (c.verdict) {
    case "ok":
      return {
        headline: shortDate(c.finish_date),
        detail:
          `${c.hours_left}h of estimated work left against ${c.hours_per_week} working hours a week` +
          (cover < 100
            ? `, sized over ${cover}% of open tasks — so the real figure is higher.`
            : ", across every open task.") +
          leave,
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
          "Nobody holding open work is in the people directory, so nobody has" +
          " working hours and this cannot be divided into a date.",
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
 * WS-27bn RA-6 — does the estimate line draw at all?
 *
 * §6.8 B: "Nothing sized" answered nothing (the screenshot of 2026-10-25
 * had 26 open tasks and none sized). So the estimates add a second line
 * ONLY when they exist. "No capacity on file" still draws, because it says
 * that estimates exist and hours do not. The server still sends the block.
 */
export function capacityShown(c: CapacityForecast | null | undefined): boolean {
  return Boolean(c?.verdict) && c?.verdict !== "no_estimates" && c?.verdict !== "nothing_left";
}

/**
 * The people line: how many are carrying work, and what we know about them.
 *
 * ⚠️ `leaving_within_90d` is a risk no velocity can see. Somebody whose
 * engagement ends inside the forecast window takes their throughput with
 * them, and the forecast above will not notice until after it happens.
 *
 * WS-27bm S12 (H-188): the server sends the count to an admin only. A missing
 * count reads as no warning, because the reader cannot see end dates.
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
  // WS-27bm S11. Hours come from each holder's working schedule, and only a
  // holder with a directory row has one.
  const known = p.in_directory ?? 0;
  const detail =
    known === 0
      ? "None of them is in the people directory, so their working hours are unknown."
      : known < n
        ? `${known} of ${n} are in the people directory, with ${p.hours_per_week} working hours a week between them.`
        : `${p.hours_per_week} working hours a week between them.`;
  const leaving = p.leaving_within_90d ?? 0;
  const risk =
    leaving > 0
      ? ` ⚠️ ${leaving} ${leaving === 1 ? "engagement ends" : "engagements end"} within 90 days.`
      : "";
  return {
    headline: `${n} ${n === 1 ? "person" : "people"}`,
    detail: detail + risk,
    tone: leaving > 0 ? "warn" : known === 0 ? "quiet" : "good",
  };
}

/**
 * WS-27bn RA-6 — the forecast as a band on a track from today, for the bar
 * that draws it (`projects_reports.md` §6.8 B).
 *
 * The track starts at the server's today and ends at the later of the 85%
 * date and the plan. `p50` and `p85` are positions on it, 0 to 100. The bar
 * fills today to the 50% date in one slot, and the 50% to the 85% date in
 * another. A plan, when there is one, is a tick.
 *
 * ⚠️ **It computes positions and no date.** Each input is the server's: the
 * 50% and 85% dates are today plus `p50_weeks` and `p85_weeks` whole weeks
 * (`analytics_forecast.count_forecast`), and `slip_days` is the 50% date
 * minus the plan. So the plan sits `p50_weeks` × 7 − `slip_days` days from
 * today, and no date is parsed in the browser.
 * `test_h185_3_the_forecast_is_today_plus_whole_weeks` in
 * `tests/unit/test_projects_report_sections_r3b.py` pins that link.
 *
 * Returns `null` unless the forecast converges with both dates. The other
 * verdicts carry no date, and the verdict line says why.
 */
export type ForecastBand = {
  /** The 50% date's position on the track, 0 to 100. */
  p50: number;
  /** The 85% date's position on the track, 0 to 100. */
  p85: number;
  /** The plan's position, or `null` with no plan. */
  plan: number | null;
  /** True when the plan date is already before today. */
  planPassed: boolean;
  p50Label: string;
  p85Label: string;
  planLabel: string | null;
  /** "23 days late", "3 days early" or "on the plan date", or `null`. */
  slip: string | null;
  tone: Tone;
  /** The whole band in one sentence, for a screen reader. */
  title: string;
};

export function forecastBand(o: OutlookReport | null | undefined): ForecastBand | null {
  const v = o?.velocity;
  const a = v?.p50_weeks;
  const b = v?.p85_weeks;
  if (
    v?.verdict !== "converging" ||
    !v.p50_date ||
    !v.p85_date ||
    typeof a !== "number" ||
    typeof b !== "number" ||
    !Number.isFinite(a) ||
    !Number.isFinite(b) ||
    a < 0 ||
    b < a
  ) {
    return null;
  }
  const d50 = a * 7;
  const d85 = b * 7;
  const p = o?.plan;
  const slipDays = p?.slip_days;
  const planDays =
    p?.planned_finish && typeof slipDays === "number" && Number.isFinite(slipDays)
      ? d50 - slipDays
      : null;
  const span = Math.max(d85, planDays ?? 0, 1);
  const at = (days: number) => (Math.max(0, days) / span) * 100;
  const days = (n: number) => `${n} ${n === 1 ? "day" : "days"}`;
  const slip =
    planDays === null || typeof slipDays !== "number"
      ? null
      : slipDays > 0
        ? `${days(slipDays)} late`
        : slipDays < 0
          ? `${days(Math.abs(slipDays))} early`
          : "on the plan date";
  const p50Label = shortDate(v.p50_date);
  const p85Label = shortDate(v.p85_date);
  const planLabel = planDays === null ? null : shortDate(p?.planned_finish);
  return {
    p50: at(d50),
    p85: at(d85),
    plan: planDays === null ? null : at(planDays),
    planPassed: planDays !== null && planDays < 0,
    p50Label,
    p85Label,
    planLabel,
    slip,
    tone:
      typeof slipDays === "number" && planDays !== null
        ? slipDays > 14
          ? "bad"
          : slipDays > 0
            ? "warn"
            : "good"
        : "good",
    title:
      `50% by ${p50Label}, 85% by ${p85Label}.` +
      (planLabel && slip ? ` Plan ${planLabel}: ${slip}.` : ""),
  };
}

/**
 * WS-27bn RA-6 — the hue of the band. ⚠️ An explicit pick of one slot of the
 * ramp, as RA-2 and RA-4 pick theirs, never an index of a changing list.
 * Slot 9 is reachable only by an explicit pick, so no hashed name shares it.
 * The 50% part is solid and the 85% part is the same hue, lighter, and the
 * label beside each dot carries the meaning, never the hue alone.
 */
export const FORECAST_SLOT = 9;

export function forecastAccent(): CategoricalAccent {
  return accentForSlot(FORECAST_SLOT);
}
