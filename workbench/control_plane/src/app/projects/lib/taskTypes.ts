/**
 * Task types — the pure rules of the manager (WS-42 PS-2).
 *
 * Spec: `project-docs/specs/projects_settings.md` §7 row PS-2.
 */

import type { TaskTypeRow } from "./api";

/**
 * The icons a type can wear. A short list on purpose: a picker of every
 * Lucide icon is a search box, and a type needs to be told apart at card size.
 * Each name must resolve (`taskTypes.test.ts`), or the card draws a fallback.
 */
export const TYPE_ICON_CHOICES = [
  "Circle",
  "CircleCheck",
  "CheckSquare",
  "Bug",
  "Sparkles",
  "Wrench",
  "BookOpen",
  "Flag",
  "Target",
  "Lightbulb",
  "Layers",
  "Rocket",
  "FileText",
  "Shield",
  "Zap",
] as const;

/**
 * The picker's value for a stored icon. The seed writes Lucide names in
 * kebab or lower case ("bug", "check-square"), the picker lists PascalCase,
 * so the two are compared without case or dashes. An icon outside the list
 * reads as the first choice.
 */
export function iconChoiceFor(icon: string | null | undefined): (typeof TYPE_ICON_CHOICES)[number] {
  const key = (icon ?? "").replace(/[-_\s]/g, "").toLowerCase();
  return TYPE_ICON_CHOICES.find((c) => c.toLowerCase() === key) ?? TYPE_ICON_CHOICES[0];
}

/**
 * What the picker shows for a stored icon: its list spelling when it is on the
 * list, or the stored name itself when it is not (an imported "star"). So a
 * render never claims "Circle" for an icon it is not drawing, and picking the
 * first choice cannot overwrite an icon by accident.
 */
export function iconValueFor(icon: string | null | undefined): string {
  if (!icon) return TYPE_ICON_CHOICES[0];
  const key = icon.replace(/[-_\s]/g, "").toLowerCase();
  return TYPE_ICON_CHOICES.find((c) => c.toLowerCase() === key) ?? icon;
}

/** The list, plus a stored icon that is not on it. */
export function iconOptionsFor(icon: string | null | undefined): string[] {
  const value = iconValueFor(icon);
  const list: string[] = [...TYPE_ICON_CHOICES];
  return list.includes(value) ? list : [value, ...list];
}

/** A row the whole organization owns. PS-3 edits those; this screen does not. */
export function typeOrgWide(row: Pick<TaskTypeRow, "project_id">): boolean {
  return row.project_id === null;
}

/**
 * Epic first (the top level), then by name.
 *
 * ⚠️ No "default" ranks here, and the screen offers no default. The column
 * `is_default` exists, but nothing reads it when a task is created
 * (`tasks.py create_task`), so a star that set it would promise a result
 * that never happens (the PS-2 review's P0).
 */
export function sortTypes<T extends TaskTypeRow>(rows: readonly T[]): T[] {
  const rank = (t: T) => (t.is_system ? 0 : 1);
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/** What a delete will do, in the member's words, before it happens. */
export function typeDeleteBody(name: string): string {
  return `Deletes “${name}”. Tasks of this type stay, with no type.`;
}
