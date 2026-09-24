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
 */
import type { ProjectRow, ReportConfig, ReportRow } from "./api";
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
  { key: "load", label: "Open work" },
  { key: "capacity", label: "Who has the hours" },
  { key: "stuck", label: "Overdue" },
  { key: "conflicts", label: "Where the plan conflicts" },
];

/**
 * The sections a new report starts with: the server's `DEFAULT_SECTIONS`.
 * The same lockstep test pins it. `capacity` and `conflicts` are opt-in.
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
  };
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
  return {
    weeks: state.weeks,
    skip_current_week: state.skipCurrentWeek,
    include_subtree: state.includeSubtree,
    sections: orderedSections(state.sections),
  };
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
