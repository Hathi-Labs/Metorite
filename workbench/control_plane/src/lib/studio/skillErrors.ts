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

/** The plain sentences of the form. The browser checks use them too. */
export const WORDS = {
  nameEmpty: "Add a name.",
  descriptionEmpty: "Write when to use this skill.",
  instructionsEmpty: "Write the instructions.",
  nameLong: (limit: string) => `The name is too long. Use ${limit} characters or fewer.`,
  descriptionLong: (limit: string) => `The description is too long. Use ${limit} characters or fewer.`,
  catalogGone: "This action is not in the catalog now. Remove it.",
  twice: "This action is in the list twice. Remove one.",
  tooMany: (limit: string) => `A skill holds at most ${limit} actions. Remove some.`,
  actionList: "The action list is not valid. Reload the page, then try again.",
  slug: "The name needs letters or digits. Change the name.",
  slugLong: "The name is too long to make a web name. Use fewer characters.",
  badChar: (field: string) => `The ${field} holds a character that is not allowed. Remove it.`,
} as const;

/** A 409 on a move: another tab, or another call, changed the skill. */
export const CHANGED_ELSEWHERE = "Another tab changed this skill. Reload to see it.";
/** The server text of a publish with no version (`ADD_A_VERSION`), with its full stop. */
export const ADD_A_VERSION = "Add a version first.";

function upperFirst(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A text that no rule knows, with no `body.` or `$.` path in front of it. */
function bare(text: string): string {
  const cut = text.replace(/^(?:body|\$)\.[\w.[\]]*\s*:?\s*/, "");
  return upperFirst(cut);
}

/** Map the detail of a 422 (or a 400) to the place that shows it. */
export function placeOfDetail(detail: string): ErrorPlace {
  const text = detail.trim();

  const row = /^\$\.actions\[(\d+)\]:\s*([\s\S]*)$/.exec(text);
  if (row) {
    const rest = row[2];
    const plain = /catalog serves no action/.test(rest)
      ? WORDS.catalogGone
      : /is in the list twice/.test(rest)
        ? WORDS.twice
        : upperFirst(rest);
    return { place: "action", index: Number(row[1]), text: plain };
  }

  const picker = /^\$\.actions:\s*([\s\S]*)$/.exec(text);
  if (picker) {
    const most = /at most (\d+) actions/.exec(picker[1]);
    return { place: "picker", text: most ? WORDS.tooMany(most[1]) : upperFirst(picker[1]) };
  }

  const body = /^body\.(description|instructions|actions)\b/.exec(text);
  if (body) {
    const key = body[1];
    if (key === "actions") return { place: "picker", text: WORDS.actionList };
    if (/non-empty/.test(text)) {
      return {
        place: key as "description" | "instructions",
        text: key === "description" ? WORDS.descriptionEmpty : WORDS.instructionsEmpty,
      };
    }
    return { place: key as "description" | "instructions", text: bare(text) };
  }

  // The store's texts and a pydantic miss ("name: Field required"). The route
  // makes the slug from the name, so a slug text belongs to the name field.
  const own = /^(name|slug|description)\b/.exec(text);
  if (own) {
    const field = own[1];
    const place = field === "description" ? "description" : "name";
    const limit = /holds at most (\d+) characters/.exec(text);
    if (/must not be empty|: Field required/.test(text)) {
      return { place, text: place === "name" ? WORDS.nameEmpty : WORDS.descriptionEmpty };
    }
    if (limit) {
      if (field === "slug") return { place, text: WORDS.slugLong };
      return { place, text: field === "name" ? WORDS.nameLong(limit[1]) : WORDS.descriptionLong(limit[1]) };
    }
    if (field === "slug" && /lower-case/.test(text)) return { place, text: WORDS.slug };
    if (/NUL|lone surrogate/.test(text)) return { place, text: WORDS.badChar(place) };
    return { place, text: upperFirst(text) };
  }
  if (text.startsWith("The name gives no slug")) return { place: "name", text: WORDS.slug };

  return { place: "form", text: bare(text) };
}

/**
 * The place of a failed call.
 *   create: a 409 is the name taken.
 *   move:   a 409 shows under the buttons with a Reload button.
 *   others: a 422 maps by its prefix, and any other text shows under the form.
 */
export function placeOfFailure(
  call: "create" | "version" | "move",
  status: number,
  detail: string,
): ErrorPlace {
  if (status === 409) {
    if (call === "create") return { place: "name", text: detail || NAME_TAKEN };
    // A publish with no version is a 409 too. It keeps its own sentence.
    if (/^Add a version first/.test(detail)) return { place: "move", text: ADD_A_VERSION };
    return { place: "move", text: CHANGED_ELSEWHERE };
  }
  if (status === 422 || status === 400) {
    // A publish checks the catalog again, so a row fault of a publish shows
    // at its row too.
    return placeOfDetail(detail);
  }
  return call === "move" ? { place: "move", text: detail } : { place: "form", text: detail };
}
