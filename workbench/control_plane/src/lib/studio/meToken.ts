/**
 * The `me` token of a view filter (STU-AF3b, `ai_studio.md` §5.5 rule 6). Pure.
 *
 * A manifest view may hold the value `"me"` in a filter on a person field. The browser replaces it
 * with the email of the signed-in member, and the door only ever sees that plain value. The page
 * read and the count of the rail both call this function, so a count equals the rows of its page.
 *
 * This is not the `$me` of a row rule (`draftProblems.ts`) and not the filter bar (`filterBarModel.ts`).
 */
import type { StudioSource } from "@/lib/studio/dataDoor";

export const ME = "me";

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** True when `value` is the token. The match is exact. */
const isToken = (value: unknown): boolean => value === ME;

/** True when a filter condition of the source holds the token. */
export function hasMe(source: StudioSource | null | undefined): boolean {
  const filter = source?.args?.filter;
  return Array.isArray(filter) && filter.some((c) => isObject(c) && isToken(c.value));
}

/**
 * The source with each token replaced by the email in lower case. `isPerson` limits the change to
 * person fields. A source with no token comes back unchanged. A source with a token and no email
 * gives `null`, so the caller does not read.
 */
export function resolveMe(
  source: StudioSource,
  email: string | null | undefined,
  isPerson?: (field: string) => boolean,
): StudioSource | null {
  const filter = source.args?.filter;
  if (!Array.isArray(filter)) return source;
  const hit = (c: unknown): c is Record<string, unknown> =>
    isObject(c) && isToken(c.value) && (!isPerson || isPerson(String(c.field)));
  if (!filter.some(hit)) return source;
  const who = typeof email === "string" ? email.trim().toLowerCase() : "";
  if (!who) return null;
  return { ...source, args: { ...source.args, filter: filter.map((c) => (hit(c) ? { ...c, value: who } : c)) } };
}
