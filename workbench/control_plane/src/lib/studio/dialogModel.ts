/**
 * The model of the add, edit and delete dialogs of a Studio app (STU-AF6, `ai_studio.md` §5.5 and
 * the AF-6 row). Pure: no React, no I/O.
 *
 * **Which buttons.** A button follows a typed action (D1) that the app declares. `object:<o>.create`
 * gives "New <Object>" in the page toolbar. `object:<o>.update` gives "Edit", and `object:<o>.delete`
 * gives "Delete", both on the record page. A button shows only when the viewer may write (J2a), so a
 * viewer sees none, and an app that declares no such action shows none. An action with a `set` is not
 * a plain verb (`verbMap`), so it gives no button.
 *
 * **One write path.** The dialog holds the Form block, and the Form block sends through `door.run`.
 * A second press while a send is pending does nothing (`pressOnce` in `formModel.sendRecord`), so a
 * double press makes one record. A refusal of the door (422) shows under its field (`afterSend`).
 */
import { type DeclaredAction, verbMap } from "./realDoor";

export interface DialogObject {
  name: string;
  label?: string | null;
}

export interface NewButton {
  object: string;
  /** The button text, "New <Object>". */
  label: string;
  /** The typed action that the Form block sends. */
  action: string;
  /** The title of the dialog. */
  title: string;
}

const nameOf = (o: DialogObject): string => (typeof o.label === "string" && o.label.trim() ? o.label.trim() : o.name);

const declared = (actions: readonly DeclaredAction[], verb: string): boolean => verbMap(actions)[verb] !== undefined;

/**
 * The "New" buttons of a page: one for each object that the page shows and that the app can create.
 * `shown` lists the object names of the page in order. A name twice gives one button. A viewer gets none.
 */
export function newButtons(
  shown: readonly (string | null | undefined)[],
  objects: readonly DialogObject[],
  actions: readonly DeclaredAction[],
  owner: boolean,
): NewButton[] {
  if (!owner) return [];
  const out: NewButton[] = [];
  const seen = new Set<string>();
  for (const name of shown) {
    if (!name || seen.has(name)) continue;
    seen.add(name);
    const object = objects.find((o) => o.name === name);
    const action = `object:${name}.create`;
    if (!object || !declared(actions, action)) continue;
    const label = nameOf(object);
    out.push({ object: name, label: `New ${label}`, action, title: `New ${label}` });
  }
  return out;
}

export interface RecordButtons {
  edit: boolean;
  remove: boolean;
}

/** Edit and Delete of the record page. A viewer gets neither. */
export function recordButtons(
  object: string,
  actions: readonly DeclaredAction[],
  owner: boolean,
): RecordButtons {
  if (!owner) return { edit: false, remove: false };
  return {
    edit: declared(actions, `object:${object}.update`),
    remove: declared(actions, `object:${object}.delete`),
  };
}

/** The title of the Edit dialog. */
export function editTitle(object: DialogObject): string {
  return `Edit ${nameOf(object)}`;
}
