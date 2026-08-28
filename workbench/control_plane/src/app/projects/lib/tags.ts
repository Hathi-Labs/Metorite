/**
 * Projects · tags in the browser (WS-27m).
 *
 * The registry lives on the gateway (`routes/projects/tags.py`) and owns
 * identity, canonical spelling, rename and merge. This file owns what a text
 * box does before any of that: splitting what somebody typed, showing them
 * whether it will land on an existing tag, and colouring the chip.
 *
 * **The canonical-spelling rule is mirrored here, not re-decided.** Typing
 * "BUG" where the project knows "bug" must show `bug` in the picker *before*
 * the save, or the chip changes under the cursor on the round trip and reads
 * as the app rewriting what was typed.
 */

import { ACCENT_HUES, type AccentHue, statusAccent } from "@/lib/statusAccent";

import type { TagRow } from "./api";

/**
 * The tag wire type. **Declared once, in `./api`, and re-exported here.**
 *
 * It used to be declared in BOTH files, and the two were assignable only for
 * as long as they happened to agree (H-6). `page.tsx` passes rows between the
 * modules, so the day one gained a field the other did not, the build broke
 * somewhere unrelated to the change. That is what widening `project_id` for
 * org-wide vocabularies actually surfaced.
 *
 * `./api` owns it because that is where its siblings live — `TaskRow`,
 * `StatusRow`, `FieldRow`, `ViewRow` — and six of the eight consumers already
 * imported it from there. The re-export keeps `from "../lib/tags"` working for
 * the other two, so this collapses a duplicate without moving anybody.
 */
export type { TagRow } from "./api";

/** Mirrors the gateway's `MAX_TAGS_PER_TASK`. */
export const MAX_TAGS_PER_TASK = 25;

/**
 * The palette a tag may be given.
 *
 * Names, not values: `DESIGN_SYSTEM.md` forbids a colour literal at a call
 * site, and a tag stores what it means rather than what it looks like, so a
 * theme change repaints every tag instead of stranding them at last year's hex.
 *
 * WS-27ad: the names and their classes now live in `src/lib/statusAccent.ts`,
 * shared with statuses and with /tasks' stage accents. A tag and a status lane
 * that both say "green" have to BE the same green, and two tables of class
 * strings is how that stops being true.
 */
export const TAG_COLORS = ACCENT_HUES;

export type TagColor = AccentHue;

/**
 * A tag's chip classes.
 *
 * Delegates to the shared vocabulary. An unrecognised stored colour still lands
 * on gray, as it always did — `resolveHue` falls through to the positional
 * fallback, whose first slot is gray, rather than throwing at a tag somebody
 * coloured from an older palette.
 */
export const chipClass = (color: string | undefined): string =>
  statusAccent({ color }).chip;

/** `lower(name)` → the registry's display form. */
export function registryOf(tags: TagRow[]): Map<string, string> {
  return new Map(tags.map((t) => [t.name.toLowerCase(), t.name]));
}

/**
 * One typed tag → its storable form, or `null` if it is not a tag.
 *
 * Mirrors `tags.normalise_tag`: trimmed, internal whitespace collapsed. Both
 * halves matter — `"needs  review"` and `"needs review"` are the same label
 * typed twice, and letting the browser send both makes the registry decide
 * something the person could have seen.
 */
export function normaliseTag(raw: string): string | null {
  const cleaned = raw.replace(/\s+/g, " ").trim();
  return cleaned === "" ? null : cleaned;
}

/**
 * Add a tag to a list, resolved through the registry.
 *
 * Returns the list unchanged when the tag is already on it (whatever its
 * casing), so pressing Enter twice is not an error and does not duplicate.
 */
export function addTag(
  current: string[],
  raw: string,
  registry: Map<string, string>
): string[] {
  const cleaned = normaliseTag(raw);
  if (cleaned === null) return current;
  const key = cleaned.toLowerCase();
  if (current.some((t) => t.toLowerCase() === key)) return current;
  if (current.length >= MAX_TAGS_PER_TASK) return current;
  return [...current, registry.get(key) ?? cleaned];
}

/** Remove a tag, matching case-insensitively so a stale chip still comes off. */
export function removeTag(current: string[], name: string): string[] {
  const key = name.toLowerCase();
  return current.filter((t) => t.toLowerCase() !== key);
}

/**
 * Suggestions for what has been typed so far.
 *
 * Excludes tags already on the task — offering one that is already a chip is a
 * click that does nothing. Substring rather than prefix, because a tag set
 * reads `needs review` and `blocked: review` and somebody typing "review"
 * means both.
 */
export function suggest(
  query: string,
  tags: TagRow[],
  chosen: string[]
): TagRow[] {
  const needle = query.replace(/\s+/g, " ").trim().toLowerCase();
  const already = new Set(chosen.map((t) => t.toLowerCase()));
  return tags
    .filter((t) => !already.has(t.name.toLowerCase()))
    .filter((t) => !needle || t.name.toLowerCase().includes(needle))
    .slice(0, 8);
}

/**
 * Whether pressing Enter now would create a NEW tag rather than pick one.
 *
 * Drives the "+ create" affordance. Auto-registration is deliberate (a two-step
 * errand is how tagging gets abandoned) but silent auto-registration is how a
 * tag set fills with typos, so the moment of creation is shown.
 */
export function wouldCreate(query: string, registry: Map<string, string>): boolean {
  const cleaned = normaliseTag(query);
  return cleaned !== null && !registry.has(cleaned.toLowerCase());
}

/**
 * Group tasks by tag, for the board's `tag` axis.
 *
 * A task with three tags appears in three columns, for the same reason a task
 * with two assignees appears in both theirs (WS-27k): it genuinely belongs to
 * each, and picking one hides it from the others.
 */
export function tagsOf(task: { tags?: string[] }): string[] {
  return task.tags ?? [];
}

/** Tags ordered as the manager lists them: most used first, then by name. */
export function byUsage(tags: TagRow[]): TagRow[] {
  return [...tags].sort(
    (a, b) => (b.task_count ?? 0) - (a.task_count ?? 0) || a.name.localeCompare(b.name)
  );
}

/**
 * Whether merging `source` into `target` is a legal request.
 *
 * The one rule worth checking before the round trip: a tag cannot be merged
 * into itself. Everything else — cross-project, missing — needs the server.
 */
export function canMerge(source: TagRow | null, target: TagRow | null): boolean {
  return Boolean(source && target && source.id !== target.id);
}
