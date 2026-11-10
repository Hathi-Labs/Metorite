/**
 * Projects · the words of "Convert to project" (WS-64 OP-10, D-OP-16).
 *
 * `POST /projects/nodes/{id}/convert` turns a folder into a project. The
 * projects inside it become its subprojects, and nothing they hold changes.
 * These are the dialog's sentences, pure, so a node test can read them.
 *
 * The server decides. A folder whose projects have subprojects of their own
 * is refused with a 422 that names them, and the dialog shows that sentence.
 */

/** How many child names the dialog lists before it says "and N more". */
export const CONVERT_LIST_LIMIT = 8;

export interface ConvertCopy {
  /** The one line under the title. */
  description: string;
  /** What happens to the projects inside the folder. */
  children: string;
  /** What does not change, and that there is no way back. */
  note: string;
}

export function convertCopy(name: string, childNames: readonly string[]): ConvertCopy {
  const shown = childNames.slice(0, CONVERT_LIST_LIMIT);
  const more = childNames.length - shown.length;
  const list = shown.join(", ") + (more > 0 ? ` and ${more} more` : "");
  let children: string;
  if (childNames.length === 0) {
    children = `${name} holds no projects yet.`;
  } else if (childNames.length === 1) {
    children = `${list} becomes a subproject of ${name}.`;
  } else {
    children = `These ${childNames.length} projects become subprojects of ${name}: ${list}.`;
  }
  return {
    description: `${name} becomes a project. The projects inside it become its subprojects.`,
    children,
    note:
      "Their tasks, statuses and access stay as they are. A project cannot turn back into a folder.",
  };
}
