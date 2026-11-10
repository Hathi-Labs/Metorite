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

/** Words that end a name badly when the cut lands on them. */
const STOP_WORDS = new Set(["for", "to", "a", "the", "of", "and", "with"]);
/** A sentence that starts with one of these is a clause: "Use this " + it. */
const CLAUSE_WORDS = new Set(["when", "whenever", "if", "after", "before", "once"]);
/** A sentence that starts with one of these is a noun phrase: "Use this for " + it. */
const NOUN_WORDS = new Set(["a", "an", "the", "my", "our", "your", "this", "these", "some"]);
/** Product names that stay as they are, and start a noun phrase. */
const PROPER_NAMES = new Set(["gmail", "google", "slack", "zoho", "whatsapp", "outlook", "github", "excel", "notion", "jira", "hubspot", "linkedin"]);

/** The name: the first six words, in sentence case, cut to 120 characters. */
export function starterName(raw: string): string {
  const words = sentenceOf(raw).split(" ").filter(Boolean).slice(0, NAME_WORDS);
  // After the cut, a closing stop-word ("... thread for") reads as broken.
  while (words.length > 1 && STOP_WORDS.has(words[words.length - 1].toLowerCase().replace(/[.,;:!?-]+$/, ""))) {
    words.pop();
  }
  const joined = words.join(" ").replace(/[\s.,;:!?-]+$/, "");
  return upperFirst(joined).slice(0, NAME_MAX).trim();
}

function isI(word: string): boolean {
  return word === "I" || word.startsWith("I'") || word.startsWith("I’");
}

/** Lower the first letter, unless the word is "I", "I'm", an acronym or a product name. */
function lowerFirstWord(sentence: string): string {
  const first = sentence.split(" ")[0];
  if (isI(first)) return sentence;
  if (first.length > 1 && first.charAt(1) !== first.charAt(1).toLowerCase()) return sentence;
  if (PROPER_NAMES.has(first.toLowerCase().replace(/[^a-z]/g, ""))) return sentence;
  return sentence.charAt(0).toLowerCase() + sentence.slice(1);
}

/**
 * The description: "Use this " and the sentence, cut to 1024. The words that
 * join them depend on the first word of the sentence:
 *   when, if, after ... : "Use this when I get a long thread"
 *   to                  : "Use this to summarise ..."
 *   a, the, my ...      : "Use this for a weekly report"
 *   anything else       : "Use this to summarise ..." (an imperative verb)
 */
export function starterDescription(raw: string): string {
  const sentence = sentenceOf(raw).replace(/\.$/, "").trim();
  if (!sentence) return "";
  const first = sentence.split(" ")[0];
  const key = first.toLowerCase().replace(/[^a-z']/g, "");
  const acronym = first.length > 1 && first.charAt(1) !== first.charAt(1).toLowerCase();
  const noun = NOUN_WORDS.has(key) || PROPER_NAMES.has(key) || acronym;
  const body = lowerFirstWord(sentence);
  // A clause or "to" already joins itself to "Use this". "I" starts a clause with no "when".
  const joined = isI(first)
    ? `when ${body}`
    : CLAUSE_WORDS.has(key) || key === "to"
      ? body
      : noun
        ? `for ${body}`
        : `to ${body}`;
  const text = `Use this ${joined}`;
  return text.slice(0, DESCRIPTION_MAX);
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
