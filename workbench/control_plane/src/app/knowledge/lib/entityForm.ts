// ── The pure half of the Add a product / Add a topic form (WS-61 KC-8d) ─────
//
// `knowledge_curation.md` §6. The gateway holds the rule (`pages.writable_space`
// and the length checks). This file only decides what the form OFFERS, so a
// member is not shown a space the route would refuse. Fence: `entityForm.test.ts`.

import type { CreatableType, Entity } from "./entities";
import { canWriteIn } from "./labels";
import type { Space } from "./types";

/** The longest name and alias, and the most aliases, that the route accepts. */
export const NAME_MAX = 200;
export const ALIAS_MAX = 120;
export const ALIASES_MAX = 50;

/**
 * The spaces that the form offers: those the member can write in. ⚠️ Not
 * `writableSpaces`: that adds the "My notes" choice with no id, and the route
 * needs a real `space_id`.
 */
export function entitySpaces(spaces: readonly Space[]): Array<{ value: string; label: string }> {
  return spaces.filter((s) => canWriteIn(s.level) && !s.archived_at).map((s) => ({ value: s.id, label: s.name }));
}

/** True when the member can write in at least one space. Hides every act when false. */
export function canAddEntity(spaces: readonly Space[]): boolean {
  return entitySpaces(spaces).length > 0;
}

/** The company space when the member can write there, else the first space they can. */
export function defaultEntitySpace(spaces: readonly Space[]): string {
  const choices = entitySpaces(spaces);
  const company = spaces.find((s) => s.kind === "company" && choices.some((c) => c.value === s.id));
  return company?.id ?? choices[0]?.value ?? "";
}

/** The alias words of a text, split on commas, trimmed, once each, in order. */
export function splitAliases(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(",")) {
    const word = part.split(/\s+/).filter(Boolean).join(" ");
    if (word && !out.some((w) => w.toLowerCase() === word.toLowerCase())) out.push(word.slice(0, ALIAS_MAX));
  }
  return out;
}

/** The act labels, by type. */
export const ADD_LABEL: Record<CreatableType, string> = {
  product: "Add a product",
  topic: "Add a topic",
};

/** The row of a directory that already holds this name (or one of its aliases), or `null`. */
export function sameNameIn(rows: readonly Pick<Entity, "name" | "aliases">[], name: string): Pick<Entity, "name"> | null {
  const key = name.split(/\s+/).filter(Boolean).join(" ").toLowerCase();
  if (!key) return null;
  return (
    rows.find((r) => r.name.toLowerCase() === key || (r.aliases ?? []).some((a) => a.toLowerCase() === key)) ?? null
  );
}
