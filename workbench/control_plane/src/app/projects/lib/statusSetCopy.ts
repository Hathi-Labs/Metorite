/**
 * Projects · every sentence about a status set (WS-42 PST-1, D185).
 *
 * Spec: `project-docs/specs/projects_settings.md` §9.5.
 *
 * The owner asked for screens that "leave no doubt in the minds of the users
 * when they're configuring the statuses and the lanes". So each Statuses
 * screen says whose set it shows, and how many projects use it. This module
 * holds every such sentence. It takes the read of §9.4
 * (`GET /projects/nodes/{id}/status-set`) and returns the text. No component
 * builds its own sentence (fence: `projectsSettings.test.ts`).
 *
 * ⚠️ **No possessive.** "Client Projects's set" was the old text. A name
 * phrase never takes "'s" here: the owner comes after "the statuses of".
 * Fence: `statusSetCopy.test.ts`.
 */

import type { StatusSetInfo, StatusSetOverride } from "./api";
import { overrideNote } from "./statusSwitch";

/** The pane section, its heading and the palette entry (§9.5). */
export const SPACE_DEFAULT_STATUSES = "Space default statuses";

/** The fields of the read that the sentences use. */
export type StatusSetRead = Pick<
  StatusSetInfo,
  "owns" | "owner_name" | "owner_kind" | "used_by" | "overrides" | "hidden_overrides"
>;

/** "1 project" or "N projects". */
function projects(n: number): string {
  return n === 1 ? "1 project" : `${n} projects`;
}

/**
 * The first sentence of the scope line, exactly as the table of §9.5 says.
 * The chip of PST-3 uses it as its tooltip.
 */
export function scopeSentence(info: StatusSetRead): string {
  const n = info.used_by;
  // Case 2 or 3: the set belongs to an ancestor.
  if (!info.owns) return `Uses the statuses of ${info.owner_name} (shared with ${n})`;
  if (info.owner_kind === "space") {
    if (n === 0) return "No other project uses these statuses.";
    return n === 1 ? "1 project uses these statuses." : `${n} projects use these statuses.`;
  }
  return n === 0
    ? "Uses its own statuses. No other project uses them."
    : `Uses its own statuses (shared with ${n})`;
}

/**
 * The second sentence, when an edit here reaches other projects. For the
 * owner and for each project that uses the set, that number is `used_by`.
 */
export function reachSentence(info: StatusSetRead): string {
  return info.used_by > 0 ? `A change here changes the lanes of ${projects(info.used_by)}.` : "";
}

/** The scope line under the title of every Statuses screen (§9.5). */
export function scopeLine(info: StatusSetRead): string {
  const first = scopeSentence(info);
  const second = reachSentence(info);
  if (!second) return first;
  return `${first.endsWith(".") ? first : `${first}.`} ${second}`;
}

/** The heading of the pane's list: every project that keeps its own set. */
export function overridesHeading(info: StatusSetRead): string {
  const total = info.overrides.length + info.hidden_overrides;
  if (total === 0) return "No project keeps its own statuses.";
  return total === 1
    ? "1 project keeps its own statuses"
    : `${total} projects keep their own statuses`;
}

/** The line under the list for the ones the member cannot see. Empty at 0. */
export function hiddenOverridesLine(info: StatusSetRead): string {
  const n = info.hidden_overrides;
  if (n <= 0) return "";
  // With no row above it, "and N more" would count projects twice.
  if (info.overrides.length === 0) return n === 1 ? "You cannot see it." : "You cannot see them.";
  return `and ${n} more that you cannot see`;
}

/** The path of a row: the names from the space down, joined. */
export function overridePath(row: Pick<StatusSetOverride, "path">): string {
  return row.path.join(" / ");
}

/** How many projects use the set of a row. Empty when none do. */
export function overrideShared(row: Pick<StatusSetOverride, "used_by">): string {
  return row.used_by > 0 ? `Shared with ${projects(row.used_by)}` : "";
}

/** The accessible name of the link that opens a row's own editor. */
export function openStatusesLabel(row: Pick<StatusSetOverride, "name">): string {
  return `Open the statuses of ${row.name}`;
}

/**
 * The dialog's note on the projects that left this set. The names come
 * through `overrideNote`. The ones the member cannot see are counted, and
 * never named.
 */
export function exceptionsNote(info: StatusSetRead): string {
  const names = info.overrides.map((o) => o.name);
  const hidden = info.hidden_overrides;
  if (hidden <= 0) return overrideNote(names);
  const unseen =
    hidden === 1
      ? "1 project that you cannot see keeps its own, so it will not change."
      : `${hidden} projects that you cannot see keep their own, so they will not change.`;
  if (names.length === 0) return unseen;
  const also = hidden === 1 ? "So does 1 project" : `So do ${hidden} projects`;
  return `${overrideNote(names)} ${also} that you cannot see.`;
}

/** The sentence of a member who may not edit (§9.5, "Member with no right"). */
export function readOnlyLine(info: Pick<StatusSetInfo, "owner_name">): string {
  return (
    `Statuses come from ${info.owner_name}. Changing them needs the ` +
    "projects:settings:write permission. Ask an organization admin."
  );
}
