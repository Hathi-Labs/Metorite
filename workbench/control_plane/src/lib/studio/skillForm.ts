/**
 * The model of `SkillForm` (STU-F2b). Every rule of the form that a test can
 * reach lives here as a pure function, because the unit tests run in Node
 * with no DOM. `SkillForm.tsx` only draws it.
 *
 * Fence: `app/studio/components/SkillForm.test.ts`.
 */
import type { CatalogAction, Skill, SkillBody } from "./skillsApi";
import { placeOfFailure, type ErrorPlace } from "./skillErrors";

/** One WS-67 tool pack: the most actions a skill holds (F2a `MAX_ACTIONS`). */
export const MAX_ACTIONS = 35;

export interface FormValues {
  name: string;
  description: string;
  instructions: string;
  /** Catalog ids, in the order the member picked. No repeat. */
  actions: string[];
}

export const EMPTY_FORM: FormValues = { name: "", description: "", instructions: "", actions: [] };

/**
 * The rows the picker offers. F2a accepts only the ids of `catalog_rows()`,
 * which never holds a `package` row, so a `package` row is dropped here.
 * Otherwise the save would answer 422. A class X row never arrives.
 */
export function pickableActions(rows: readonly CatalogAction[]): CatalogAction[] {
  return rows.filter((row) => row.source !== "package" && ["A", "B", "C"].includes(String(row.class)));
}

/** The word of a class: A reads, B changes, C sends. */
export function classWord(cls: string): string {
  if (cls === "A") return "read";
  if (cls === "B") return "change";
  if (cls === "C") return "send";
  return cls;
}

/** Toggle one id: add it at the end (at most 35, no repeat), or remove it. */
export function toggleAction(actions: readonly string[], id: string): string[] {
  if (actions.includes(id)) return actions.filter((a) => a !== id);
  if (actions.length >= MAX_ACTIONS) return [...actions];
  return [...actions, id];
}

/** The body of a version: `description` is the one description of the form. */
export function toBody(values: FormValues): SkillBody {
  return {
    description: values.description.trim(),
    instructions: values.instructions.trim(),
    actions: [...values.actions],
  };
}

export function toCreate(values: FormValues): { name: string; description: string; body: SkillBody } {
  return { name: values.name.trim(), description: values.description.trim(), body: toBody(values) };
}

/** The form that an existing skill and its newest version fill. */
export function valuesOf(skill: Skill, body: SkillBody | null): FormValues {
  return {
    name: skill.name,
    description: body?.description ?? skill.description,
    instructions: body?.instructions ?? "",
    actions: [...(body?.actions ?? [])],
  };
}

/** The hue of a skill state, by name. `statusAccent.ts` turns it into classes. */
export const SKILL_STATE_HUE: Record<string, "gray" | "amber" | "green"> = {
  draft: "gray",
  published: "green",
  paused: "amber",
  archived: "gray",
};

/** The move buttons that a state shows. `archived` shows none (F2b has no archive). */
export function movesOf(state: string): ("publish" | "pause" | "resume")[] {
  if (state === "draft") return ["publish"];
  if (state === "published") return ["pause"];
  if (state === "paused") return ["resume"];
  return [];
}

export function stateTarget(move: "publish" | "pause" | "resume"): "published" | "paused" {
  return move === "pause" ? "paused" : "published";
}

/**
 * One call at a time. A second `run` while the first has not answered sends
 * nothing and answers `undefined`. The guard lives outside React state, so two
 * clicks in one frame cannot both pass.
 */
export function createSubmitGuard() {
  let busy = false;
  return {
    get busy() {
      return busy;
    },
    async run<T>(send: () => Promise<T>): Promise<T | undefined> {
      if (busy) return undefined;
      busy = true;
      try {
        return await send();
      } finally {
        busy = false;
      }
    },
  };
}

/** The errors of the form, by the place that shows each. */
export interface FormErrors {
  name?: string;
  description?: string;
  instructions?: string;
  picker?: string;
  /** Row index in the saved list -> text. */
  actions: Record<number, string>;
  form?: string;
  move?: string;
}

export const NO_ERRORS: FormErrors = { actions: {} };

/** Put one failure at its place. */
export function errorsOf(
  call: "create" | "version" | "move",
  status: number,
  detail: string,
): FormErrors {
  return placeErrors(placeOfFailure(call, status, detail));
}

export function placeErrors(place: ErrorPlace): FormErrors {
  const out: FormErrors = { actions: {} };
  if (place.place === "action") out.actions[place.index] = place.text;
  else out[place.place] = place.text;
  return out;
}
