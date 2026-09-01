// Who am I — the sidebar identity row's judgements (GET /operators/session).
//
// ⚠️ Pure functions only, because this app's suite carries no React renderer:
// everything `Identity.tsx` decides is decided HERE, and `whoami.test.ts` is
// the fence.

/** What the Console answers. `breakglass` carries no person and no role. */
export type Whoami = {
  method: "session" | "breakglass";
  actor: string | null;
  role: string | null;
};

/** Parse the Console's answer, refusing shapes that would render garbage.
 *
 * ⚠️ Null for ANYTHING unexpected — an identity row drawn from a half-parsed
 * body would show a name the audit log cannot back, which is worse than no
 * row at all. */
export function readWhoami(raw: unknown): Whoami | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (r.method === "breakglass") {
    return { method: "breakglass", actor: null, role: null };
  }
  if (r.method !== "session") return null;
  if (typeof r.actor !== "string" || r.actor.trim() === "") return null;
  return {
    method: "session",
    actor: r.actor,
    role: typeof r.role === "string" && r.role ? r.role : null,
  };
}

/** Only a real, named person gets a row. The break-glass token names nobody,
 *  and a made-up name over real audit lines teaches the team to trust it. */
export function showable(who: Whoami | null): who is Whoami & { actor: string } {
  return who !== null && who.method === "session" && who.actor !== null;
}

/** The bold line: the part of the email a human recognises. */
export function displayName(actor: string): string {
  const at = actor.indexOf("@");
  return at > 0 ? actor.slice(0, at) : actor;
}

/** Two letters for the avatar, from the email's local part.
 *
 * "vijay.varada@…" → "VV" · "vjvarada@…" → "VJ" · "x@…" → "X".
 * Separator-split first (a dot, dash or underscore marks a second name);
 * otherwise the first two letters. Never empty: "?" for garbage. */
export function initials(actor: string): string {
  const local = displayName(actor).toLowerCase();
  const parts = local.split(/[._-]+/).filter((p) => /[a-z0-9]/.test(p));
  if (parts.length === 0) return "?";
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase();
  }
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
