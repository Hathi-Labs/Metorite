/**
 * The "Starting point" of the skill form (STU-F2b, `ai_studio.md` §14 F2b).
 *
 * A pure function. A member types one sentence, and this fills the fields of
 * the form from it. **No model, no network, no credit**, so this file imports
 * no fetch. A model drafts a skill in F2c, not here.
 *
 * It fills a field only when that field is empty, so it never overwrites typed
 * text. It picks no action, because choosing actions is the job of F2c.
 * Fence: `skillStarter.test.ts`, which also checks that this file names no
 * fetch.
 */

/** The longest sentence the box takes. */
export const STARTER_MAX = 500;
/** The limits of F2a: the name and the description of the skill row. */
export const NAME_MAX = 120;
export const DESCRIPTION_MAX = 1024;
const NAME_WORDS = 6;

export interface SkillFields {
  name: string;
  description: string;
  instructions: string;
}

function sentenceOf(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().slice(0, STARTER_MAX);
}

function upperFirst(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

/** The name: the first six words, in sentence case, cut to 120 characters. */
export function starterName(raw: string): string {
  const words = sentenceOf(raw).split(" ").filter(Boolean).slice(0, NAME_WORDS);
  const joined = words.join(" ").replace(/[\s.,;:!?-]+$/, "");
  return upperFirst(joined).slice(0, NAME_MAX).trim();
}

/** The description: "Use this when " and the sentence, cut to 1024. */
export function starterDescription(raw: string): string {
  const sentence = sentenceOf(raw);
  if (!sentence) return "";
  return `Use this when ${lowerFirst(sentence)}`.slice(0, DESCRIPTION_MAX);
}

/** The instructions: a fixed Markdown skeleton that holds the sentence. */
export function starterInstructions(raw: string): string {
  const sentence = sentenceOf(raw);
  if (!sentence) return "";
  return `## When to use\n\n${sentence}\n\n## Steps\n\n1. \n\n## Rules\n\n- \n`;
}

/** The three fields a sentence gives, with no regard for what the form holds. */
export function starterFor(raw: string): SkillFields {
  return {
    name: starterName(raw),
    description: starterDescription(raw),
    instructions: starterInstructions(raw),
  };
}

/**
 * The form after the starter ran. A field that holds text (a blank counts as
 * empty) keeps its text. An empty field takes the starter's.
 */
export function applyStarter(current: SkillFields, raw: string): SkillFields {
  const fill = starterFor(raw);
  const empty = (value: string) => value.trim() === "";
  return {
    name: empty(current.name) ? fill.name : current.name,
    description: empty(current.description) ? fill.description : current.description,
    instructions: empty(current.instructions) ? fill.instructions : current.instructions,
  };
}
