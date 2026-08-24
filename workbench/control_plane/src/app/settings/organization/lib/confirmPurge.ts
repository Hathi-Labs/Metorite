// ── The type-to-confirm rule for a permanent deletion ─────────────────────
//
// Spec: project-docs/specs/colleague_onboarding.md §2 Step 5 (N8),
// done-when 6 — "the confirmation names both halves and requires the address
// to be typed".
//
// It lives in its own module for the same reason `selfGuard.ts` does: there is
// no jsdom/component harness in this repo, so a rule written inline in
// `page.tsx` can only ever be fenced by grepping the file for copy. The
// grep-the-copy version was in fact shipped, and replacing the comparison with
// `const confirmed = true;` left 32 pytest and 173 vitest cases green — the
// last barrier in front of the one irreversible action on the screen, with
// nothing at all behind it.
//
// Nothing here is a boundary. There is no server-side equivalent and there
// deliberately is not one: the gateway cannot tell a typed confirmation from
// an untyped one, and a "confirmed=true" body parameter would be a boundary
// that any client could satisfy. This is a speed bump in front of an
// authorized action, and the authorization is `admin:members:manage`.

/**
 * Whether what was typed matches the member's address well enough to arm the
 * Delete permanently button.
 *
 * Case-insensitive and whitespace-tolerant, matching `isSelf` and the
 * gateway: the point is to make the admin read the address and re-type it,
 * not to test their shift key. A trailing space from a copy-paste is not a
 * different intention.
 *
 * **An empty address on either side confirms nothing.** A member row that
 * arrived without an email — or a dialog rendered before one did — must not
 * turn an empty input box into consent, which is exactly what a bare
 * `typed === member.email` would do.
 */
export function purgeConfirmed(typed: string, memberEmail: string): boolean {
  const entered = (typed ?? "").trim().toLowerCase();
  const target = (memberEmail ?? "").trim().toLowerCase();
  return target !== "" && entered === target;
}
