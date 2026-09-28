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
 * period chip offers three periods, and nothing that needs a field the
 * server does not have ("today", a custom range, a forward period).
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

import type {
  ProjectRow,
  ReportConfig,
  ReportRow,
  ReportSubject,
  ReportSubjects,
  ReportTemplate,
} from "./api";
import { flatten } from "./tree";

/**
 * The sections, in the server's `SECTIONS` order, with the heading
 * `RenderedBody` draws for each.
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
  { key: "stuck", label: "Overdue" },
  { key: "hygiene", label: "Data hygiene" },
  { key: "conflicts", label: "Where the plan conflicts" },
  { key: "rebalance", label: "Who could help" },
];

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

/** The name a new report starts with. The member can change it. */
export const NEW_REPORT_NAME = "Weekly delivery";

export interface PeriodOption {
  key: string;
  label: string;
  weeks: number;
  skip_current_week: boolean;
}

/** The three periods the config can express (§8 R1). */
export const PERIODS: readonly PeriodOption[] = [
  { key: "last_week", label: "Last week", weeks: 1, skip_current_week: true },
  { key: "this_week", label: "This week", weeks: 1, skip_current_week: false },
  {
    key: "last_4_weeks",
    label: "The last 4 weeks",
    weeks: 4,
    skip_current_week: true,
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
  };
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
  return {
    ...base,
    name: template.name,
    weeks: template.weeks ?? base.weeks,
    skipCurrentWeek: template.skip_current_week ?? base.skipCurrentWeek,
    sections: orderedSections(template.sections),
    template: template.key,
    // WS-27bn R5b. A link can name the subject. A template that is not
    // about a person or a team takes none.
    subject: subject && subjectChipShown(template) ? subject : null,
  };
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

/** The note beside the locked chip of "My day". */
export const SELF_SUBJECT_NOTE = "My day is always about you.";

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
 * "Everyone", then the people, then the teams, each under a heading. The
 * reader's own row reads "Me" and comes first among the people. A person
 * shows the name, with the address muted beside it. The server already
 * applied §7.1, so this lists what it answered and adds nobody.
 *
 * ⚠️ A saved subject that the answer does not list stays as an option, as
 * the period chip keeps a saved period. So an edit of the name does not
 * change the subject.
 */
export function subjectOptions(
  answer: ReportSubjects | null | undefined,
  current: ReportSubject | null = null
): SelectOption[] {
  const options: SelectOption[] = [
    { value: EVERYONE, label: "Everyone", group: "Everyone" },
  ];
  const me = (answer?.me ?? "").trim().toLowerCase();
  const people = [...(answer?.people ?? [])].sort((a, b) =>
    a.email === me ? -1 : b.email === me ? 1 : 0
  );
  for (const p of people) {
    const own = p.email === me;
    options.push({
      value: subjectValue({ kind: "person", email: p.email }),
      label: own ? "Me" : p.name || p.email,
      hint: own || p.name ? p.email : undefined,
      group: "People",
    });
  }
  for (const t of answer?.teams ?? []) {
    options.push({
      value: subjectValue({ kind: "team", slug: t.slug }),
      label: teamLabel(t.name || t.slug),
      group: "Teams",
    });
  }
  const saved = subjectValue(current);
  if (current && !options.some((o) => o.value === saved)) {
    options.push({
      value: saved,
      label: current.kind === "person" ? current.email : teamLabel(current.slug),
      hint: "as saved",
      group: current.kind === "person" ? "People" : "Teams",
    });
  }
  return options;
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
 */
export function withSubject(
  state: BuilderState,
  subject: ReportSubject | null
): BuilderState {
  return { ...state, subject };
}

/**
 * Why the preview cannot run yet, as one line that says what to do, or
 * `null`. A template about one person shows this line and asks the server
 * for nothing, because the server would answer 422.
 */
export function subjectPrompt(
  state: Pick<BuilderState, "subject">,
  template: ReportTemplate | null | undefined
): string | null {
  const needs = requiredSubject(template);
  if (!needs) return null;
  if (state.subject?.kind === "person") return null;
  return needs === "self"
    ? "Your own day shows when the list of people loads."
    : "Choose a person in the About chip to see this report.";
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

/**
 * The parser of `reportLink`. `null` opens the home screen: no key after
 * `app`, an unknown or coming-soon template, or a subject with a bad shape.
 */
export function parseReportLink(
  params: URLSearchParams,
  templates: readonly ReportTemplate[]
): ReportLinkIntent | null {
  const key = params.get("template");
  const rawSubject = params.get("subject");
  const node = params.get("report_node") || null;
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
  const base = (intent.template &&
    builderStateFromTemplate(intent.template, subject)) || {
    ...newBuilderState(),
    subject,
  };
  return { ...base, projectId: intent.node };
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
