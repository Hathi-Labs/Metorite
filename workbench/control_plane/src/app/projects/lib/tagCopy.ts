/**
 * Projects · what the tag manager's confirmations say (2026-09-24).
 *
 * Both draw through the shared `ConfirmDialog`. Until then the org-wide
 * rename asked with `window.confirm`, and a tag DELETE asked nothing at all.
 *
 * **What a tag delete really does** (`routes/projects/tags.py`
 * `delete_tag`): it runs `array_remove` on every task in the tag's scope,
 * then deletes the registry row. The tasks stay. The tag comes off each one,
 * and nothing restores it. So the copy says "taken off N tasks" and "cannot
 * be undone", and never says that tasks are deleted.
 *
 * Fence: `tagCopy.test.ts`.
 */

import type { DeleteCopy } from "./deleteCopy";

const tasks = (n: number) => `${n} task${n === 1 ? "" : "s"}`;

/** Deleting one tag. `taskCount` is the registry's own count. */
export function tagDeleteCopy(tag: { name: string; task_count?: number | null }): DeleteCopy {
  const n = tag.task_count ?? 0;
  return {
    title: "Delete this tag?",
    subject: tag.name,
    body:
      (n
        ? `It is taken off ${tasks(n)} and deleted for good. The tasks stay.`
        : "No task wears it. It is deleted for good.") + " This cannot be undone.",
    note: null,
    confirmLabel: "Delete tag",
  };
}

/**
 * Renaming an org-wide tag. The count is the point of the question
 * (D-PM-33). `impact` is null when the preview failed, and then the copy
 * says the size is unknown rather than guessing.
 */
export function orgRenameCopy(
  to: string,
  impact: { tag: string; tasks: number; projects: number } | null,
): DeleteCopy {
  if (!impact) {
    return {
      title: "Rename this organization-wide tag?",
      subject: null,
      body:
        `It is renamed to “${to}” in every project. The number of tasks ` +
        "it rewrites could not be read.",
      note: null,
      confirmLabel: "Rename",
    };
  }
  const projects = `${impact.projects} project${impact.projects === 1 ? "" : "s"}`;
  return {
    title: "Rename this organization-wide tag?",
    subject: impact.tag,
    body: `Renaming it to “${to}” rewrites ${tasks(impact.tasks)} across ${projects}.`,
    note: "That includes projects you may not be able to open.",
    confirmLabel: "Rename",
  };
}
