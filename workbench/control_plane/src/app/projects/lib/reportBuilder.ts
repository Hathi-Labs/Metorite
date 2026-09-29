/**
 * WS-27bn R1 — the report builder's choices, as pure functions.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.2 and §8 R1.
 *
 * The builder is one sentence of chips: a scope, a period, a subtree toggle,
 * the sections and a name. This module turns those chips into the `config`
 * the server validates, and back. It computes no figure. Every number in a
 * report comes from `/render` or `/preview` on the server (§9.12.7).
 *
 * ⚠️ **Only what the config can express.** `normalise_report_config` knows
 * `weeks`, `skip_current_week`, `include_subtree` and `sections`. So the
 * period chip offers only `PERIODS`, and nothing that needs a field the
 * server does not have ("today", a custom range, a forward period).
 *
 * WS-27bn R5f (§8 R5f) adds Overview, the start of the one Reports app:
 * `overviewState`, `saveAsReportState` and `overviewTableShown`.
 *
 * ⚠️ **The scope is a project node or the whole organization.** People and
 * teams are R5, behind `may_report_on` (§7.1). A picker that listed a person
 * now would show a subject the server cannot scope to.
 *
 * WS-27bn R2 (§4, §6.1) adds templates. The catalogue is the server's
 * (`GET /projects/reports/templates`), and this module holds no copy of it.
 * A template presets the chips and stays on the config as `template`.
 *
 * WS-27bn R5b-1 (§8 R5b) adds the subject chip, "about [subject]", beside
 * the scope chip, "in [scope]". The subject is one person or one team, from
 * `GET /projects/reports/subjects` only. The scope stays the project tree.
 * The two combine, so they are two chips and never one picker.
 */
import type { SelectOption } from "@/components/ui/SelectButton";
import { periodLabel } from "@/lib/reportEmail";

import type {
  ProjectRow,
  ReportConfig,
  ReportRow,
  ReportSubject,
  ReportSubjects,
  ReportTemplate,
} from "./api";
import { shortDate } from "./outlook";
import { flatten } from "./tree";

/**
 * The sections, in the server's `SECTIONS` order, with the ONE name of each.
 *
 * WS-27bn R5f round 2 (§6.6 C). The builder tile, the card title of the
 * panel, the clear row, the all-clear list and the email heading all say this
 * name, so a member can join a tile to its card. `sectionName` reads it.
 *
 * ⚠️ A mirror of `reports.py` `SECTIONS`. `test_projects_report_sections_lockstep.py`
 * reads this list as text and fails when the two differ in name or order.
 */
export const REPORT_SECTIONS: readonly { key: string; label: string }[] = [
  { key: "finished", label: "What we finished" },
  { key: "throughput", label: "How long it took" },
  { key: "outlook", label: "Forecast" },
  { key: "load", label: "Open work" },
  { key: "capacity", label: "Who has the hours" },
  { key: "pulse", label: "Team pulse" },
  { key: "stuck", label: "Stuck work" },
  { key: "hygiene", label: "Data hygiene" },
  { key: "conflicts", label: "Where the plan conflicts" },
  { key: "rebalance", label: "Who could help" },
];

/** The one name of a section (§6.6 C). An unknown key names itself. */
export function sectionName(key: string): string {
  return REPORT_SECTIONS.find((s) => s.key === key)?.label ?? key;
}

/**
 * The sections a new report starts with: the server's `DEFAULT_SECTIONS`.
 * The same lockstep test pins it. `outlook`, `capacity`, `pulse`,
 * `hygiene`, `conflicts` and `rebalance` are opt-in.
 */
export const DEFAULT_REPORT_SECTIONS: readonly string[] = [
  "finished",
  "throughput",
  "load",
  "stuck",
];

/** `reports.py` `MAX_NAME`. The server refuses a longer name with 422. */
export const MAX_REPORT_NAME = 120;

/**
 * The name a new custom report starts with. The member can change it. A
 * report from a template starts with the template's name (R5b-1 repair).
 */
export const NEW_REPORT_NAME = "Untitled report";

export interface PeriodOption {
  key: string;
  label: string;
  weeks: number;
  skip_current_week: boolean;
}

/**
 * The periods the config can express (§8 R1). WS-27bn R5f adds "The last 12
 * weeks", the period of Overview. So the chip names the Overview period,
 * and "Save as report" keeps it.
 */
export const PERIODS: readonly PeriodOption[] = [
  { key: "last_week", label: "Last week", weeks: 1, skip_current_week: true },
  { key: "this_week", label: "This week", weeks: 1, skip_current_week: false },
  {
    key: "last_4_weeks",
    label: "The last 4 weeks",
    weeks: 4,
    skip_current_week: true,
  },
  {
    key: "last_12_weeks",
    label: "The last 12 weeks",
    weeks: 12,
    skip_current_week: false,
  },
];

/** The scope value for the whole organization. The server reads `null`. */
export const WHOLE_ORGANIZATION = "";

export interface ScopeOption {
  value: string;
  label: string;
  depth: number;
}

/**
 * The scope chip's options: "Whole organization", then the project tree.
 *
 * Only nodes the caller can see arrive in `/tree`, so the picker never
 * offers a node that the server then refuses.
 */
export function scopeOptions(roots: readonly ProjectRow[]): ScopeOption[] {
  return [
    { value: WHOLE_ORGANIZATION, label: "Whole organization", depth: 0 },
    ...flatten(roots).map(({ node, depth }) => ({
      value: node.id,
      label: node.name,
      depth,
    })),
  ];
}

/** What the builder holds. `weeks` and the skip flag are the period. */
export interface BuilderState {
  name: string;
  /** `null` is the whole organization. */
  projectId: string | null;
  weeks: number;
  skipCurrentWeek: boolean;
  includeSubtree: boolean;
  sections: string[];
  /**
   * WS-27bn R2. The template key the report started from, or `null`. It is
   * an origin label: a change of chips keeps it.
   */
  template: string | null;
  /**
   * WS-27bn R5b. Who the report is about, or `null` for everyone in the
   * scope. ⚠️ PATCH replaces the config, so each payload carries it.
   */
  subject: ReportSubject | null;
  /**
   * §6.5 item 5. False until the member types a name. While it is
   * false, `builderName` derives the name from the chips.
   */
  nameTouched: boolean;
  /**
   * §6.5 item 7. True when the subject was chosen: by the member,
   * by a link, or by a saved row. `startingTeam` changes only an untouched
   * subject.
   */
  subjectTouched: boolean;
}

/** A new report: the server's defaults, for the whole organization. */
export function newBuilderState(): BuilderState {
  return {
    name: NEW_REPORT_NAME,
    projectId: null,
    weeks: 1,
    skipCurrentWeek: true,
    includeSubtree: true,
    sections: [...DEFAULT_REPORT_SECTIONS],
    template: null,
    subject: null,
    nameTouched: false,
    subjectTouched: false,
  };
}

// ── WS-27bn R5f: Overview, the start of the one Reports app ────────────────

/**
 * The seven sections of Overview, in `SECTIONS` order. The Analytics app
 * drew these seven, with the route defaults that `overviewState` copies, so
 * Overview shows the same numbers (§8 R5f rule 4). `pulse`, `hygiene` and
 * `rebalance` start off. A reader turns them on in the section list.
 */
export const OVERVIEW_SECTIONS: readonly string[] = [
  "finished",
  "throughput",
  "outlook",
  "load",
  "capacity",
  "stuck",
  "conflicts",
];

/** The title of Overview, and the name its preview shows. */
export const OVERVIEW_NAME = "Overview";

/**
 * The state Overview starts with (§8 R5f rule 3): the whole organization,
 * no subject, the subtree, and the Analytics routes' own period, 12 weeks
 * with the running week. It is not a template, so `template` is `null`.
 */
export function overviewState(): BuilderState {
  return {
    ...newBuilderState(),
    name: OVERVIEW_NAME,
    projectId: null,
    weeks: 12,
    skipCurrentWeek: false,
    includeSubtree: true,
    sections: orderedSections(OVERVIEW_SECTIONS),
    template: null,
    subject: null,
  };
}

/**
 * The builder that "Save as report" opens (§8 R5f rules 10 and 11). It keeps
 * the scope, the subject, the period, the subtree and the sections of
 * Overview. The template is `null` and the name is untouched, so
 * `builderName` names the report from the chips.
 */
export function saveAsReportState(state: BuilderState): BuilderState {
  return {
    ...newBuilderState(),
    projectId: state.projectId,
    subject: state.subject,
    weeks: state.weeks,
    skipCurrentWeek: state.skipCurrentWeek,
    includeSubtree: state.includeSubtree,
    sections: orderedSections(state.sections),
    template: null,
    nameTouched: false,
    subjectTouched: state.subject !== null,
  };
}

/**
 * True when Overview draws the KPI strip and the space table (§8 R5f rule 8).
 *
 * ⚠️ The summary routes take no subject, and they always count the subtree.
 * So with a subject, or without the subtree, their counts would disagree
 * with the sections under them. Then the strip and the table are absent.
 */
export function overviewTableShown(
  state: Pick<BuilderState, "subject" | "includeSubtree">
): boolean {
  return state.subject === null && state.includeSubtree === true;
}

/** A saved report, opened for an edit. Every field comes from the row. */
export function builderStateFrom(row: ReportRow): BuilderState {
  return {
    name: row.name,
    projectId: row.project_id,
    weeks: row.config.weeks,
    skipCurrentWeek: row.config.skip_current_week,
    includeSubtree: row.config.include_subtree,
    sections: orderedSections(row.config.sections),
    template: row.config.template ?? null,
    // WS-27bn R5b. Before R5b this line was absent, so an edit of a report
    // about a person sent a config with no subject, and the PATCH removed it.
    subject: row.config.subject ?? null,
    // The UX pass. A saved name and a saved subject are choices already.
    nameTouched: true,
    subjectTouched: true,
  };
}

/**
 * WS-27bn R2. A new report, preset from one template of the server's
 * catalogue. The member can still change each chip.
 *
 * ⚠️ **It refuses a coming-soon template** and answers `null`. Such a
 * template names no sections, and the server refuses its key with 422. So a
 * builder opened from it would offer a report that it cannot save.
 */
export function builderStateFromTemplate(
  template: ReportTemplate,
  subject: ReportSubject | null = null
): BuilderState | null {
  if (!template.available || !template.sections?.length) return null;
  const base = newBuilderState();
  const state: BuilderState = {
    ...base,
    name: template.name,
    weeks: template.weeks ?? base.weeks,
    skipCurrentWeek: template.skip_current_week ?? base.skipCurrentWeek,
    sections: orderedSections(template.sections),
    template: template.key,
    subject: null,
  };
  // WS-27bn R5b. A link can name the subject. A template that is not about
  // a person or a team takes none. `withSubject` keeps a section on.
  return withSubject(state, subject && subjectChipShown(template) ? subject : null);
}

/** The most cards "Your reports" shows (§6.1, the R2 narrowing). */
export const YOUR_REPORTS_LIMIT = 6;

/**
 * WS-27bn R2. "Your reports": the rows the server marks `mine`, newest
 * first, at most six. The server decides `mine`. This only filters.
 */
export function yourReports(
  rows: readonly ReportRow[],
  limit: number = YOUR_REPORTS_LIMIT
): ReportRow[] {
  return rows
    .filter((r) => r.mine === true)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    .slice(0, limit);
}

/** The name a card shows for a report's template, or "Custom". */
export function templateLabel(
  row: ReportRow,
  templates: readonly ReportTemplate[]
): string {
  const key = row.config.template;
  if (!key) return "Custom";
  return templates.find((t) => t.key === key)?.name ?? "Custom";
}

/** Sections in the server's order, with each name once. */
export function orderedSections(sections: readonly string[]): string[] {
  const asked = new Set(sections);
  return REPORT_SECTIONS.map((s) => s.key).filter((k) => asked.has(k));
}

/**
 * Add or remove one section.
 *
 * ⚠️ The last section stays. The server refuses an empty list with 422, so
 * a builder that let the member clear it would offer a report it cannot save.
 */
export function toggleSection(
  sections: readonly string[],
  key: string
): string[] {
  if (sections.includes(key)) {
    if (sections.length <= 1) return orderedSections(sections);
    return orderedSections(sections.filter((s) => s !== key));
  }
  return orderedSections([...sections, key]);
}

/** The period key for a state, or `null` when no offered period matches. */
export function periodKey(state: Pick<BuilderState, "weeks" | "skipCurrentWeek">): string | null {
  const hit = PERIODS.find(
    (p) => p.weeks === state.weeks && p.skip_current_week === state.skipCurrentWeek
  );
  return hit ? hit.key : null;
}

/** The value the period chip shows for a period that no option matches. */
export const SAVED_PERIOD = "saved";

/**
 * The period chip's options.
 *
 * ⚠️ A report saved elsewhere (the chat, or an older client) can hold a
 * period that the chip does not offer. That period stays as an option, so
 * an edit of the name does not change the period behind the member's back.
 */
export function periodOptions(
  state: Pick<BuilderState, "weeks" | "skipCurrentWeek">
): { value: string; label: string }[] {
  const options = PERIODS.map((p) => ({ value: p.key, label: p.label }));
  if (periodKey(state) === null) {
    const tail = state.skipCurrentWeek ? "before this week" : "to date";
    options.push({
      value: SAVED_PERIOD,
      label: `${state.weeks} weeks ${tail} (as saved)`,
    });
  }
  return options;
}

/** Apply a period chip choice. The saved option changes nothing. */
export function withPeriod(state: BuilderState, key: string): BuilderState {
  const hit = PERIODS.find((p) => p.key === key);
  if (!hit) return state;
  return { ...state, weeks: hit.weeks, skipCurrentWeek: hit.skip_current_week };
}

/**
 * The FULL config. PATCH replaces `config` on the server, so the builder
 * always sends every field, never a part.
 */
export function configFor(state: BuilderState): ReportConfig {
  const config: ReportConfig = {
    weeks: state.weeks,
    skip_current_week: state.skipCurrentWeek,
    include_subtree: state.includeSubtree,
    sections: sectionsFor(state),
  };
  // WS-27bn R2. PATCH replaces the config, so a template left out here
  // would be lost on every edit. No template sends no key, as in R1.
  if (state.template) config.template = state.template;
  // WS-27bn R5b. The same rule for the subject. No subject sends no key.
  if (state.subject) config.subject = state.subject;
  return config;
}

// ── WS-27bn R5b-1: the subject chip ─────────────────────────────────────────

/**
 * The sections that refuse a subject: `report_scope.py`
 * `NO_SUBJECT_SECTIONS`. `test_projects_report_sections_lockstep.py` reads
 * this list as text and fails when the two differ.
 */
export const NO_SUBJECT_SECTIONS: readonly string[] = ["outlook", "hygiene"];

/** The line under the sections when a subject turns sections off. */
export const NO_SUBJECT_NOTE =
  "Forecast and Data hygiene cover a whole project, so they are off for a person or team.";

/** The subject chip's value for "Everyone": no subject. */
export const EVERYONE = "";

/** The note beside the locked chip of "My day", for its author. */
export const SELF_SUBJECT_NOTE = "My day is always about you.";

/**
 * The note beside the locked chip of "My day" when an admin edits the
 * report of another member. The server keeps a T2 about its author.
 */
export const AUTHOR_SUBJECT_NOTE = "My day is always about its author.";

/**
 * What the subject chip says when the subjects read fails. The UX pass
 * (§6.5 item 8) draws it as the prompt of a disabled chip, with Retry beside it.
 */
export const SUBJECTS_FAILED = "People did not load";

/** The subject chip's line when the answer lists nobody. */
export const NOBODY_TO_CHOOSE = "There is no person or team to choose yet.";

/**
 * The sections `configFor` sends. With a subject, `outlook` and `hygiene`
 * are off, and the server would refuse them with 422. If nothing is left,
 * the default sections take their place, because a report needs one.
 */
export function sectionsFor(
  state: Pick<BuilderState, "sections" | "subject">
): string[] {
  const ordered = orderedSections(state.sections);
  if (!state.subject) return ordered;
  const kept = ordered.filter((s) => !NO_SUBJECT_SECTIONS.includes(s));
  return kept.length ? kept : [...DEFAULT_REPORT_SECTIONS];
}

/** True when a section's checkbox is off because of the subject. */
export function sectionBlockedBySubject(
  state: Pick<BuilderState, "subject">,
  key: string
): boolean {
  return state.subject !== null && NO_SUBJECT_SECTIONS.includes(key);
}

/**
 * The muted line under the sections, or `null`. It shows whenever a
 * subject is set, because the two checkboxes are then off, and an option
 * must never go silently.
 */
export function subjectSectionNote(
  state: Pick<BuilderState, "subject">
): string | null {
  return state.subject ? NO_SUBJECT_NOTE : null;
}

/** The chip value of a subject: `person:<email>`, `team:<slug>`, or "". */
export function subjectValue(subject: ReportSubject | null): string {
  if (!subject) return EVERYONE;
  return subject.kind === "person"
    ? `person:${subject.email}`
    : `team:${subject.slug}`;
}

/**
 * A chip value, or the `subject` key of a link, as a subject. `undefined`
 * when the value has a bad shape. `null` is "Everyone".
 */
export function parseSubjectValue(
  value: string
): ReportSubject | null | undefined {
  if (value === EVERYONE) return null;
  const at = value.indexOf(":");
  if (at < 0) return undefined;
  const kind = value.slice(0, at);
  const rest = value.slice(at + 1).trim();
  if (kind === "person" && /^[^\s@:]+@[^\s@]+$/.test(rest)) {
    return { kind: "person", email: rest.toLowerCase() };
  }
  if (kind === "team" && /^[a-z0-9_-]{1,48}$/i.test(rest)) {
    return { kind: "team", slug: rest.toLowerCase() };
  }
  return undefined;
}

/** A team's name as the chip says it: "Hardware team". */
export function teamLabel(name: string): string {
  const trimmed = name.trim();
  return /\bteam$/i.test(trimmed) ? trimmed : `${trimmed} team`;
}

/**
 * The subject chip's options, from the subjects answer only (§8 R5b).
 *
 * §6.5 item 6 sets the order. "Everyone" comes first, with
 * no heading. The teams come next, under "Teams", then the people, under
 * "People". The reader's own row reads "Me" and comes first among the
 * people. The address shows only on "Me", and on two people who share one
 * name, because only there does the name not say who it is. The server
 * already applied §7.1, so this lists what it answered and adds nobody.
 *
 * ⚠️ A saved subject that the answer does not list stays as an option, as
 * the period chip keeps a saved period. So an edit of the name does not
 * change the subject. It goes at the end of its own group, so the menu
 * draws each heading once. `editing` decides its hint: "as saved" for a
 * saved row, and "not in your list" for a subject that a link named.
 */
export function subjectOptions(
  answer: ReportSubjects | null | undefined,
  current: ReportSubject | null = null,
  editing = false
): SelectOption[] {
  const me = (answer?.me ?? "").trim().toLowerCase();
  const people = [...(answer?.people ?? [])].sort((a, b) =>
    a.email === me ? -1 : b.email === me ? 1 : 0
  );
  const named = new Map<string, number>();
  for (const p of people) {
    const key = (p.name ?? "").trim().toLowerCase();
    if (key) named.set(key, (named.get(key) ?? 0) + 1);
  }
  const teamRows: SelectOption[] = (answer?.teams ?? []).map((t) => ({
    value: subjectValue({ kind: "team", slug: t.slug }),
    label: teamLabel(t.name || t.slug),
    group: "Teams",
  }));
  const personRows: SelectOption[] = people.map((p) => {
    const own = p.email === me;
    const twin = (named.get((p.name ?? "").trim().toLowerCase()) ?? 0) > 1;
    return {
      value: subjectValue({ kind: "person", email: p.email }),
      label: own ? "Me" : p.name || p.email,
      hint: own || (p.name && twin) ? p.email : undefined,
      // The filter matches the address too, so a search by address finds a
      // named person whose address the menu does not show.
      keywords: p.email,
      group: "People",
    };
  });
  const saved = subjectValue(current);
  const listed = [...teamRows, ...personRows].some((o) => o.value === saved);
  if (current && !listed) {
    const row: SelectOption = {
      value: saved,
      label: current.kind === "person" ? current.email : teamLabel(current.slug),
      // "as saved" is true only for a saved row. A subject from a link was
      // never saved, and the server refuses it on the preview.
      hint: editing ? "as saved" : "not in your list",
      group: current.kind === "person" ? "People" : "Teams",
    };
    (current.kind === "person" ? personRows : teamRows).push(row);
  }
  return [{ value: EVERYONE, label: "Everyone" }, ...teamRows, ...personRows];
}

/** The subject for "Me", or `null` when the answer has not arrived. */
export function selfSubject(
  answer: ReportSubjects | null | undefined
): ReportSubject | null {
  const me = (answer?.me ?? "").trim().toLowerCase();
  return me ? { kind: "person", email: me } : null;
}

/**
 * True when the subject chip shows. A template with no `person` or `team`
 * in `scope_kinds` hides it. A report with no template shows it.
 */
export function subjectChipShown(
  template: ReportTemplate | null | undefined
): boolean {
  if (!template) return true;
  return template.scope_kinds.some((k) => k === "person" || k === "team");
}

/** What a template needs as its subject, or `null`. */
export function requiredSubject(
  template: ReportTemplate | null | undefined
): "self" | "person" | null {
  return template?.requires_subject ?? null;
}

/**
 * Apply a subject chip choice. `outlook` and `hygiene` stay in `sections`,
 * so "Everyone" gives them back, and `configFor` leaves them out.
 *
 * ⚠️ When the subject turns off EVERY chosen section, the default sections
 * go into `sections` here (R5b-1 repair). Before, only `configFor` added
 * them, so the preview showed four sections and no checkbox showed them.
 */
export function withSubject(
  state: BuilderState,
  subject: ReportSubject | null
): BuilderState {
  const ordered = orderedSections(state.sections);
  const kept = ordered.filter((s) => !NO_SUBJECT_SECTIONS.includes(s));
  const sections =
    subject && kept.length === 0
      ? orderedSections([...ordered, ...DEFAULT_REPORT_SECTIONS])
      : ordered;
  return { ...state, subject, sections };
}

/**
 * The state the builder shows and sends (R5b-1 repair).
 *
 * A NEW "My day" takes the reader as its subject, derived here and never
 * typed. ⚠️ **An edit keeps the stored subject.** A T2 is always about its
 * AUTHOR, and an admin may edit it (§9 Q13). Before this function, the
 * builder put the admin in as the subject of the member's report, and the
 * member then lost it.
 */
export function builderSubject(
  draft: BuilderState,
  needs: "self" | "person" | null,
  self: ReportSubject | null,
  editing: boolean
): BuilderState {
  if (needs === "self" && !editing && self) return withSubject(draft, self);
  return draft;
}

/** What the subject chip draws while the subjects read runs, or fails. */
export type SubjectChipStatus = "loading" | "failed" | "ready";

/** The subject chip's status. An answer wins over an old error. */
export function subjectChipStatus(
  answer: ReportSubjects | null | undefined,
  failed: boolean
): SubjectChipStatus {
  if (answer) return "ready";
  return failed ? "failed" : "loading";
}

/**
 * The muted note beside a ready subject chip, or `null`.
 *
 * "My day" says who it is about: "you" for its author, and "its author"
 * for an admin who edits the report of another member. A reader with
 * nobody to choose reads that, and not an empty list.
 */
export function subjectChipNote(
  needs: "self" | "person" | null,
  answer: ReportSubjects,
  subject: ReportSubject | null,
  editing: boolean
): string | null {
  if (needs === "self") {
    const me = (answer.me ?? "").trim().toLowerCase();
    const mine = subject?.kind === "person" && subject.email === me;
    return editing && !mine ? AUTHOR_SUBJECT_NOTE : SELF_SUBJECT_NOTE;
  }
  if (answer.people.length === 0 && answer.teams.length === 0) {
    return NOBODY_TO_CHOOSE;
  }
  return null;
}

/**
 * Why the preview cannot run yet, as one line that says what to do, or
 * `null`. A template about one person shows this line and asks the server
 * for nothing, because the server would answer 422.
 */
export function subjectPrompt(
  state: Pick<BuilderState, "subject">,
  template: ReportTemplate | null | undefined,
  listFailed = false
): string | null {
  const needs = requiredSubject(template);
  if (!needs) return null;
  if (state.subject?.kind === "person") return null;
  if (needs === "person") {
    return "Choose a person in the About chip to see this report.";
  }
  // The builder puts a Retry button beside the failed line.
  return listFailed
    ? "The list of people did not load, so your own day cannot show."
    : "Your own day shows when the list of people loads.";
}

/** Which chip an error from the server belongs to. */
export type ErrorChip = "subject" | "scope" | "other";

/**
 * The chip that caused a 403 or a 422, from the server's words. The
 * builder shows the sentence next to that chip. The words are the server's
 * (`report_scope.py`, `reports.py`), and each case has a test.
 */
export function errorChip(message: string): ErrorChip {
  const m = message.toLowerCase();
  if (
    /subject|about one person|must be you|lead role|admin grant|is not an active member|is not a team|report on/.test(
      m
    )
  ) {
    return "subject";
  }
  if (/project|not found|scope/.test(m)) return "scope";
  return "other";
}

// ── WS-27bn R5b: the link contract ──────────────────────────────────────────

/** What a link to the builder names. Each key is optional. */
export interface ReportLinkParts {
  template?: string | null;
  subject?: ReportSubject | null;
  /** A project node, as the scope. */
  node?: string | null;
}

/**
 * The ONE builder of a link to Reports (§8 R5b, the link contract):
 * `/projects?app=reports&template=<key>&subject=<kind>:<value>&report_node=<id>`.
 *
 * ⚠️ The key is `report_node`, never `project`. The page consumes
 * `?project=` and closes the app pane. A second builder of this address is
 * a defect. `origin` is passed in, as `taskDeepLink` takes it.
 */
export function reportLink(parts: ReportLinkParts = {}, origin = ""): string {
  const q = new URLSearchParams({ app: "reports" });
  if (parts.template) q.set("template", parts.template);
  if (parts.subject) q.set("subject", subjectValue(parts.subject));
  if (parts.node) q.set("report_node", parts.node);
  return `${origin}/projects?${q.toString()}`;
}

/** The keys of the link that `ReportsView` reads once and then removes. */
export const REPORT_LINK_KEYS: readonly string[] = [
  "template",
  "subject",
  "report_node",
];

/** What a link opens: the builder with these parts. `null` is the home. */
export interface ReportLinkIntent {
  template: ReportTemplate | null;
  subject: ReportSubject | null;
  node: string | null;
}

/** A UUID, the shape of every project id. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The parser of `reportLink`. `null` opens the home screen: no key after
 * `app`, an unknown or coming-soon template, or a subject with a bad shape.
 *
 * ⚠️ **The node must be a UUID in the reader's tree** (R5b-1 repair). The
 * server casts the id to a uuid, and a bad one made the preview answer 500.
 * A node that fails either test is dropped. `roots` is the `/tree` answer.
 */
export function parseReportLink(
  params: URLSearchParams,
  templates: readonly ReportTemplate[],
  roots: readonly ProjectRow[] = []
): ReportLinkIntent | null {
  const key = params.get("template");
  const rawSubject = params.get("subject");
  const rawNode = (params.get("report_node") || "").trim();
  const node =
    rawNode &&
    UUID.test(rawNode) &&
    scopeOptions(roots).some((o) => o.value === rawNode)
      ? rawNode
      : null;
  if (!key && !rawSubject && !node) return null;
  let template: ReportTemplate | null = null;
  if (key) {
    template = templates.find((t) => t.key === key && t.available) ?? null;
    if (!template) return null;
  }
  let subject: ReportSubject | null = null;
  if (rawSubject) {
    const parsed = parseSubjectValue(rawSubject);
    if (!parsed) return null;
    subject = parsed;
  }
  return { template, subject, node };
}

/**
 * The builder a link opens. A template presets the chips, and the subject
 * and the node follow. A template about one person takes a person only.
 */
export function builderStateFromLink(intent: ReportLinkIntent): BuilderState {
  const subject =
    intent.subject &&
    requiredSubject(intent.template) &&
    intent.subject.kind !== "person"
      ? null
      : intent.subject;
  const base =
    (intent.template && builderStateFromTemplate(intent.template, subject)) ||
    withSubject(newBuilderState(), subject);
  // A subject the link named is a choice, so `startingTeam` leaves it.
  return { ...base, projectId: intent.node, subjectTouched: base.subject !== null };
}

/** The body of `POST /projects/reports` for this state. */
export function createPayload(state: BuilderState): {
  name: string;
  project_id: string | null;
  config: ReportConfig;
} {
  return {
    name: state.name.trim(),
    project_id: state.projectId,
    config: configFor(state),
  };
}

/** The body of `PATCH /projects/reports/{id}`: the name and the whole config. */
export function patchPayload(state: BuilderState): {
  name: string;
  config: ReportConfig;
} {
  return { name: state.name.trim(), config: configFor(state) };
}

/** Why the builder cannot save yet, or `null`. The server checks it again. */
export function saveRefusal(state: BuilderState): string | null {
  const name = state.name.trim();
  if (!name) return "A report needs a name.";
  if (name.length > MAX_REPORT_NAME) {
    return `A report name is at most ${MAX_REPORT_NAME} characters.`;
  }
  if (state.sections.length === 0) return "Choose at least one section.";
  return null;
}

// ── The UX pass (2026-09-29), `projects_reports.md` §6.5 ────────────────────

/** §6.5 item 2. The preview line of a project-only template with no project. */
export const SCOPE_PROMPT = "Choose a project in the In chip to see this report.";

/**
 * True for a template whose `scope_kinds` is exactly `["project"]`, such
 * as "Project status". Such a report is about one project, and the whole
 * organization is not a choice.
 */
export function projectOnly(template: ReportTemplate | null | undefined): boolean {
  return (
    !!template &&
    template.scope_kinds.length === 1 &&
    template.scope_kinds[0] === "project"
  );
}

/**
 * §6.5 item 2. Why the preview cannot run yet because of the scope, or `null`.
 *
 * ⚠️ **A new report only.** PATCH cannot change the scope, so an edit of a
 * saved org-wide "Project status" cannot answer the prompt. With the prompt
 * on, Save stayed off and the report could not be edited (repair round 1).
 */
export function scopePrompt(
  state: Pick<BuilderState, "projectId">,
  template: ReportTemplate | null | undefined,
  editing: boolean
): string | null {
  if (editing) return null;
  return projectOnly(template) && state.projectId === null ? SCOPE_PROMPT : null;
}

/**
 * §6.5 item 2. The scope chip's options. A NEW project-only report has no
 * "Whole organization". An edit keeps it, so the chip names the saved scope.
 */
export function scopeChoices(
  scopes: readonly ScopeOption[],
  template: ReportTemplate | null | undefined,
  editing: boolean
): ScopeOption[] {
  return projectOnly(template) && !editing
    ? scopes.filter((s) => s.value !== WHOLE_ORGANIZATION)
    : [...scopes];
}

/**
 * §6.5 item 7. A new report from a template that takes a team starts on the
 * reader's team, when the reader leads exactly one and is not an admin.
 *
 * ⚠️ A lead already receives the rows of their team with "Everyone"
 * (`report_scope.py` `ReaderScope.people`). The team start only puts the
 * report on the people the lead may see, so no hidden line shows. A chosen
 * subject, an edit and a subject from a link stay as they are.
 */
export function startingTeam(
  draft: BuilderState,
  template: ReportTemplate | null | undefined,
  answer: ReportSubjects | null | undefined,
  editing: boolean
): BuilderState {
  if (editing || draft.subjectTouched || draft.subject !== null) return draft;
  if (!template || template.requires_subject) return draft;
  if (!template.scope_kinds.includes("team")) return draft;
  if (!answer || answer.everyone || answer.teams.length !== 1) return draft;
  return withSubject(draft, { kind: "team", slug: answer.teams[0].slug });
}

/** §6.5 item 16. What the hidden line adds for a lead who can choose a team. */
export const TEAM_HINT = "Choose a team in About to report on that team only.";

/**
 * §6.5 item 16. The hint after "This report hides N other people", or `null`.
 *
 * It shows only where a team choice changes the report: the reader leads a
 * team, is not an admin, and chose no subject yet. ⚠️ A team choice does
 * not SHOW the hidden people. §7.1 hides them from a lead on any subject,
 * so the words do not promise that.
 */
export function hiddenTeamHint(
  answer: ReportSubjects | null | undefined,
  subject: ReportSubject | null,
  chipShown: boolean
): string | null {
  // No About chip, no hint: the words must not name a control that is absent.
  if (!chipShown) return null;
  if (!answer || answer.everyone || answer.teams.length === 0) return null;
  return subject === null ? TEAM_HINT : null;
}

/**
 * §6.5 item 3. The sections that ignore the period: `render_body` reads each of
 * them as the state now. `finished` and `throughput` are the only sections
 * that read `weeks` and `skip_current_week`.
 *
 * ⚠️ `test_projects_report_sections_lockstep.py` reads this list as text,
 * and fails when it differs from the branches of `render_body`.
 */
export const PERIOD_FREE_SECTIONS: readonly string[] = [
  "outlook",
  "load",
  "capacity",
  "pulse",
  "stuck",
  "hygiene",
  "conflicts",
  "rebalance",
];

/** §6.5 item 3. What the builder shows in place of the period chip. */
export const AS_OF_TODAY = "As of today";

/**
 * §6.5 item 3. The day a period-free report is "as of", as `YYYY-MM-DD`.
 *
 * The server's day wins when the body names one: `pulse` reads one UTC day
 * and sends it as `today`. Otherwise the reader's own date, because the
 * render ran now. ⚠️ Never `toISOString()`, which is the UTC date and names
 * yesterday for a reader east of Greenwich before dawn in UTC.
 */
export function asOfDay(serverDay: string | null | undefined, now: Date): string {
  if (serverDay && /^\d{4}-\d{2}-\d{2}/.test(serverDay)) return serverDay.slice(0, 10);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`;
}

/** §6.5 item 3. True when every section the config sends ignores the period. */
export function periodFree(state: Pick<BuilderState, "sections" | "subject">): boolean {
  const sent = sectionsFor(state);
  return sent.length > 0 && sent.every((s) => PERIOD_FREE_SECTIONS.includes(s));
}

/** §6.5 item 13. A subject as its name, from the subjects answer. */
export function subjectLabel(
  subject: ReportSubject | null,
  answer: ReportSubjects | null | undefined
): string | null {
  if (!subject) return null;
  if (subject.kind === "team") {
    const team = answer?.teams.find((t) => t.slug === subject.slug);
    return teamLabel(team?.name || subject.slug);
  }
  const person = answer?.people.find((p) => p.email === subject.email);
  return person?.name || subject.email;
}

/** The label of a scope option, or `null` when the tree does not hold it. */
function scopeLabelOf(
  projectId: string,
  scopes: readonly ScopeOption[]
): string | null {
  return scopes.find((s) => s.value === projectId)?.label ?? null;
}

/**
 * §6.5 item 13. The scope, as the header says it. "Whole organization", or "In"
 * and the node. With the subtree on and a node that has children, it adds
 * "and the projects under it". `scopes` is `scopeOptions`, in tree order,
 * so a child is the next option at a greater depth.
 */
export function scopePhrase(
  projectId: string | null,
  includeSubtree: boolean,
  scopes: readonly ScopeOption[]
): string {
  if (projectId === null) return "Whole organization";
  const at = scopes.findIndex((s) => s.value === projectId);
  if (at < 0) return "In a project";
  const node = scopes[at];
  const next = scopes[at + 1];
  const parent = includeSubtree && next !== undefined && next.depth > node.depth;
  return parent ? `In ${node.label} and the projects under it` : `In ${node.label}`;
}

/**
 * §6.5 items 13 and 3. The line under the report's title: "About [subject]
 * · [scope] · [period]", or "As of [day]" in place of the period for a
 * period-free report. The dates are the server's.
 */
export function reportHeaderLine(parts: {
  subject: string | null;
  scope: string;
  periodStart: string;
  periodEnd: string;
  periodFree: boolean;
  /** The day of the render, as `YYYY-MM-DD`. */
  asOf: string;
}): string {
  const when = parts.periodFree
    ? `As of ${shortDate(parts.asOf)}`
    : periodLabel(parts.periodStart, parts.periodEnd);
  return [parts.subject ? `About ${parts.subject}` : null, parts.scope, when]
    .filter(Boolean)
    .join(" · ");
}

/** The three labels of §6.5 item 4, and the sections under each. */
const SECTION_GROUP_OF: Readonly<Record<string, string>> = {
  finished: "What happened",
  throughput: "What happened",
  outlook: "Where we stand",
  load: "Where we stand",
  capacity: "Where we stand",
  stuck: "Where we stand",
  hygiene: "Where we stand",
  pulse: "Who needs help",
  conflicts: "Who needs help",
  rebalance: "Who needs help",
};

const SECTION_GROUP_ORDER = ["What happened", "Where we stand", "Who needs help"];

/**
 * §6.5 item 4. The section checkboxes under three small labels. Each group
 * keeps the server's `SECTIONS` order, because it reads `REPORT_SECTIONS`.
 */
export function sectionGroups(): {
  label: string;
  sections: { key: string; label: string }[];
}[] {
  return SECTION_GROUP_ORDER.map((label) => ({
    label,
    sections: REPORT_SECTIONS.filter((s) => SECTION_GROUP_OF[s.key] === label),
  }));
}

/**
 * §6.5 item 5. The name the builder shows and saves.
 *
 * A typed name wins (`nameTouched`). Otherwise the template name, and what
 * the report is about: "1:1 prep: Meera Iyer", "Project status: Printer X2"
 * or "Team pulse: Hardware team". The subject comes first, and the scope
 * when there is no subject. "My day" is always about its author, so it adds
 * nothing. A custom report with nothing to name is "Untitled report".
 */
export function builderName(
  state: BuilderState,
  template: ReportTemplate | null | undefined,
  answer: ReportSubjects | null | undefined,
  scopes: readonly ScopeOption[]
): string {
  if (state.nameTouched) return state.name;
  const about =
    template?.requires_subject === "self"
      ? null
      : (subjectLabel(state.subject, answer) ??
        (state.projectId ? scopeLabelOf(state.projectId, scopes) : null));
  const base = template?.name ?? (about ? "Report" : NEW_REPORT_NAME);
  return (about ? `${base}: ${about}` : base).slice(0, MAX_REPORT_NAME);
}

/**
 * §6.5 item 12. The muted line on a "Your reports" card. The template and the
 * subject show only when the name does not say them already, as a derived
 * name ("1:1 prep: Meera Iyer") does. The scope always shows.
 */
export function reportCardLine(
  row: ReportRow,
  template: string,
  subject: string | null,
  scope: string
): string {
  const name = row.name.trim().toLowerCase();
  const said = (part: string | null) => !!part && name.includes(part.trim().toLowerCase());
  return [said(template) ? null : template, subject && !said(subject) ? `About ${subject}` : null, scope]
    .filter(Boolean)
    .join(" · ");
}

/**
 * §6.5 item 12. The one empty line of the Reports home. The rail shows no
 * line of its own. The finished count shows only when it is a number, so the
 * line never says "undefined". Since R5f the gallery is above "Your
 * reports", so the line says "above". The count comes from Overview.
 */
export function homeEmptyLine(finishedCount?: number | null): string {
  const count =
    typeof finishedCount === "number" && Number.isFinite(finishedCount) && finishedCount > 0
      ? ` There ${finishedCount === 1 ? "is 1 finished task" : `are ${finishedCount} finished tasks`} to report on.`
      : "";
  return `You have not saved a report yet.${count} Pick a question above.`;
}

/** §6.5 item 12. The rail's two lists, by the server's `mine`, newest first. */
export function railGroups(rows: readonly ReportRow[]): {
  yours: ReportRow[];
  shared: ReportRow[];
} {
  const newest = [...rows].sort((a, b) =>
    a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0
  );
  return {
    yours: newest.filter((r) => r.mine === true),
    shared: newest.filter((r) => r.mine !== true),
  };
}

/**
 * §6.5 item 10. "Delete report" shows in edit mode only when the server's
 * `can_delete` is true. It is absent, never disabled, as Edit is (R5d).
 */
export function deleteShown(row: ReportRow | null): boolean {
  return row?.can_delete === true;
}

/** §6.5 item 10. The builder's title in edit mode. */
export function editTitle(row: ReportRow): string {
  return `Edit: ${row.name}`;
}

// ── WS-27bn R5f repair round 1 ──────────────────────────────────────────────

/**
 * True when the builder must ask the server for a preview (rule 6).
 *
 * The preview on screen answers `shownKey`. When the member returns to Home,
 * Overview comes back with the preview that Home kept, and its key equals
 * the key of the choices. Then nothing is asked. A prompt asks nothing too.
 */
export function previewNeeded(
  shownKey: string | null,
  key: string,
  blocked: boolean
): boolean {
  if (blocked) return false;
  return shownKey !== key;
}

/** What the Reports pane shows. */
export type ReportsPane = "wait-for-link" | "overview" | "builder" | "saved";

/**
 * The pane of `ReportsView` (rule 7).
 *
 * ⚠️ **A builder link decides the pane before the first preview.** While the
 * address still holds a key of `REPORT_LINK_KEYS`, the pane waits, and
 * Overview does not mount. Before round 1 Overview mounted first and sent
 * its heavy preview for a link that opened the builder a moment later.
 */
export function reportsPane(parts: {
  building: boolean;
  selected: string | null;
  linkPending: boolean;
}): ReportsPane {
  if (parts.building) return "builder";
  if (parts.linkPending) return "wait-for-link";
  if (parts.selected !== null) return "saved";
  return "overview";
}

/** True when the address holds a key of a builder link. */
export function linkPending(params: URLSearchParams): boolean {
  return REPORT_LINK_KEYS.some((k) => params.has(k));
}
