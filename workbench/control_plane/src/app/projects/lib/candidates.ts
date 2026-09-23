/**
 * Projects · "Suggested" in the task panel's assignee picker (WS-27bm S7b).
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §13.4, §10.4 item 10.
 *
 * The server ranks. `GET /projects/tasks/{id}/candidates` returns at most
 * three people, ordered by `rank_candidates` — skill, spare hours and
 * availability — with every factor on the row. This module only DECIDES what
 * the picker shows from that answer. It ranks nothing and counts nothing,
 * so the order a member sees is the order the chat quotes.
 *
 * ⚠️ **No HR grant, no list** (§13.4 rule 1). A caller without
 * `admin:members:read` gets `hr_visible: false` and no `candidates` key, and
 * the picker then draws no "Suggested" heading at all. An empty heading would
 * say "nobody fits", which is a different fact from "you may not see fit".
 *
 * Only `TaskBody` shows it. The bulk bar and the move dialog also render the
 * picker, and they hold many tasks or none, so a list ranked for ONE task
 * has nothing to say there.
 */

import { normalize } from "./assignees";

/** One ranked person, as the candidates route returns it. */
export interface FitCandidate {
  person_id: string | null;
  name: string;
  /** The value the task stores. */
  email: string;
  skill_points: number;
  matched_skills: string[];
  /** Absent when any matching person had no estimated work (§13.4 rule 2). */
  spare_hours?: number;
  away?: { kind: string; until: string } | null;
  rank: number;
  /** §13.4 rule 5. Shown, never enforced. */
  warnings: string[];
}

export interface CandidatesResponse {
  hr_visible: boolean;
  due_on: string | null;
  window: { starts_on: string; ends_on: string; days: number; basis: string };
  /** ABSENT, not empty, for a caller without the HR grant. */
  candidates?: FitCandidate[];
  hours_basis?: boolean;
  hours_note?: string;
  pool_size?: number;
}

/**
 * The rows under "Suggested", best first, or none.
 *
 * * Nothing without the HR grant, and nothing from a malformed answer.
 * * Nobody already on the task: the server leaves the saved assignees out,
 *   and this drops one the member added a moment ago.
 * * While the member types, only the suggestions that match the typed text,
 *   so "Suggested" never contradicts the name search under it.
 *
 * The server's order is kept. Nothing here re-sorts.
 */
export function suggestedRows(
  res: CandidatesResponse | null,
  opts: { assigned?: readonly string[]; query?: string } = {},
): FitCandidate[] {
  if (!res || res.hr_visible !== true || !Array.isArray(res.candidates)) return [];
  const taken = new Set((opts.assigned ?? []).map(normalize));
  const needle = normalize(opts.query ?? "");
  return res.candidates.filter((c) => {
    if (!c || typeof c.email !== "string" || !c.email) return false;
    if (taken.has(normalize(c.email))) return false;
    if (!needle) return true;
    return (
      normalize(c.name ?? "").includes(needle) || normalize(c.email).includes(needle)
    );
  });
}

/**
 * The one line under a suggested person: why they rank, then the warnings.
 * Spare hours only when the server sent them — their absence means the
 * ranks used skill and availability only, and a zero would read as "free".
 */
export function describeCandidate(c: FitCandidate): string {
  const parts: string[] = [];
  if (c.matched_skills?.length) parts.push(`Knows ${c.matched_skills.join(", ")}`);
  if (typeof c.spare_hours === "number") parts.push(`${c.spare_hours}h spare`);
  const warnings = c.warnings ?? [];
  // The server's away-on-the-due-date warning restates the same absence, with
  // the date that matters. Say it once.
  if (c.away && !warnings.some((w) => w.startsWith("Away ("))) {
    parts.push(`away (${c.away.kind}) until ${c.away.until}`);
  }
  parts.push(...warnings);
  return parts.join(" · ");
}
