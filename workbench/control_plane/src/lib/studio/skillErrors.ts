/**
 * Where an error of the skill routes shows (STU-F2b, `ai_studio.md` §14 F2b).
 *
 * F2a answers a refusal as `{"detail": "<one string>"}`. It has no structured
 * path, so the path is the text before the colon. ONE pure function maps a
 * detail to the field that holds the fault. A text that no rule knows shows
 * whole under the form. **This file never invents a path.**
 *
 * Fence: `skillErrors.test.ts`, one case per spelling that F2a has.
 */

export type ErrorPlace =
  | { place: "name"; text: string }
  | { place: "description"; text: string }
  | { place: "instructions"; text: string }
  /** The picker as a whole (a count, or a list that is not a list). */
  | { place: "picker"; text: string }
  /** One row of the picker, by its position in the list the member saved. */
  | { place: "action"; index: number; text: string }
  /** Under the form, whole. */
  | { place: "form"; text: string }
  /** Under the buttons of a move. */
  | { place: "move"; text: string };

/** The text of a 409 on create, as the route says it. */
export const NAME_TAKEN = "You already have a skill with this name. Choose another name.";

function upperFirst(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Map the detail of a 422 (or a 400) to the place that shows it. */
export function placeOfDetail(detail: string): ErrorPlace {
  const text = detail.trim();

  const row = /^\$\.actions\[(\d+)\]:\s*([\s\S]*)$/.exec(text);
  if (row) return { place: "action", index: Number(row[1]), text: upperFirst(row[2]) };

  const picker = /^\$\.actions:\s*([\s\S]*)$/.exec(text);
  if (picker) return { place: "picker", text: upperFirst(picker[1]) };

  const body = /^body\.(description|instructions|actions)\b/.exec(text);
  if (body) {
    const key = body[1];
    if (key === "actions") return { place: "picker", text };
    return { place: key as "description" | "instructions", text };
  }

  // The store's texts and a pydantic miss ("name: Field required"). The route
  // makes the slug from the name, so a slug text belongs to the name field.
  const own = /^(name|slug|description)\b/.exec(text);
  if (own) {
    return { place: own[1] === "description" ? "description" : "name", text };
  }
  if (text.startsWith("The name gives no slug")) return { place: "name", text };

  return { place: "form", text };
}

/**
 * The place of a failed call.
 *   create: a 409 is the name taken.
 *   move:   a 409 shows under the buttons, and the caller loads the skill again.
 *   others: a 422 maps by its prefix, and any other text shows under the form.
 */
export function placeOfFailure(
  call: "create" | "version" | "move",
  status: number,
  detail: string,
): ErrorPlace {
  if (status === 409) {
    return call === "create"
      ? { place: "name", text: detail || NAME_TAKEN }
      : { place: "move", text: detail };
  }
  if (status === 422 || status === 400) {
    // A publish checks the catalog again, so a row fault of a publish shows
    // at its row too.
    return placeOfDetail(detail);
  }
  return call === "move" ? { place: "move", text: detail } : { place: "form", text: detail };
}
