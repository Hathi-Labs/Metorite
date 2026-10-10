/**
 * Which account's appearance this browser paints (owner bug, 2026-10-11).
 *
 * The owner reported it: "setting the appearance in one organization carries
 * over to others." Mode, density and accent lived under fixed keys, so every
 * account signed in to one browser shared them. Now each account has its own
 * copy, and `storage.ts` keeps the keys.
 *
 * The scope is `<email>|<organizationId>`. It is the format of `chatScope`
 * in `lib/sessions.ts`, so the browser has one scope vocabulary for both. The
 * pointer `APPEARANCE_SCOPE_KEY` names the active scope. Three writers move it.
 *
 * 1. `pointAppearanceAt`, in `switchTo`, BEFORE the reload. The switch knows
 *    the email of the target and not its organization, so it takes the last
 *    full scope seen for that email.
 * 2. `bindAppearanceScope`, in `ThemeProvider`, once `useAccess()` resolves.
 *    When the pointer disagrees with the session, the pointer moves and the
 *    store re-reads. That is a one-frame correction, and only in that case.
 * 3. Nothing else. A sign-out leaves the pointer, so the sign-in page paints
 *    the last account's look, as it painted the shared look before.
 *
 * ⚠️ **Legacy values.** A browser from before this change holds the bare
 * keys. The FIRST bind, which finds no pointer at all, copies them into its
 * scope and deletes the bare member keys. A second account then starts from
 * its organization's defaults and inherits nothing. The bare `theme` key
 * stays, because next-themes reads it.
 *
 * Fence: `scope.test.ts`.
 */

import { chatScope, NO_EMAIL } from "@/lib/sessions";
import {
  APPEARANCE_LAST_SCOPE_PREFIX,
  APPEARANCE_SCOPE_KEY,
  DEFAULT_MODE,
  MODE_STORAGE_KEY,
  STORAGE_KEYS,
  rawStorage,
  scopedKey,
  themeStorage,
} from "./storage";
import type { ThemeMode } from "./types";

/** The member keys a legacy browser holds. They move, then they go. */
const LEGACY_MEMBER_KEYS = [STORAGE_KEYS.density, STORAGE_KEYS.accent, STORAGE_KEYS.accentInk] as const;

/** The keys the first scope copies. The org density and the mode stay too. */
const LEGACY_COPIED_KEYS = [...LEGACY_MEMBER_KEYS, STORAGE_KEYS.orgDensity, MODE_STORAGE_KEY] as const;

function normalEmail(email: string | null | undefined): string | null {
  const who = (email ?? "").trim().toLowerCase();
  return who && who !== NO_EMAIL ? who : null;
}

/** The org id of the last full scope seen for this email, or "". */
function rememberedOrg(email: string): string {
  const last = rawStorage.read(APPEARANCE_LAST_SCOPE_PREFIX + email) ?? "";
  return last.startsWith(`${email}|`) ? last.slice(email.length + 1) : "";
}

/**
 * The appearance scope of one account, or null with no member to name.
 * A missing org id takes the last one seen for the same email. `/auth/me`
 * answers `organization: {}` when its org query fails, and a scope that
 * flipped would repaint the page with no preferences.
 */
export function appearanceScopeFor(
  email: string | null | undefined,
  organizationId: string | null | undefined,
): string | null {
  const who = normalEmail(email);
  if (!who) return null;
  const org = (organizationId ?? "").trim() || rememberedOrg(who);
  return chatScope(who, org);
}

/** Copy the bare values into `scope` once, then delete the bare member keys. */
function adoptLegacy(scope: string): void {
  for (const key of LEGACY_COPIED_KEYS) {
    const value = rawStorage.read(key);
    const target = scopedKey(key, scope);
    if (value !== null && rawStorage.read(target) === null) rawStorage.write(target, value);
  }
  for (const key of LEGACY_MEMBER_KEYS) rawStorage.write(key, null);
}

/**
 * Bind the account the session names. Returns true when the pointer moved,
 * and the caller must then re-read the store and the colour mode.
 * With no member to name (signed out, auth off) it changes nothing.
 */
export function bindAppearanceScope(
  email: string | null | undefined,
  organizationId: string | null | undefined,
): boolean {
  const scope = appearanceScopeFor(email, organizationId);
  if (!scope) return false;
  const current = rawStorage.read(APPEARANCE_SCOPE_KEY);
  if (current === null) adoptLegacy(scope);
  const who = scope.slice(0, scope.indexOf("|"));
  if (!scope.endsWith("|")) rawStorage.write(APPEARANCE_LAST_SCOPE_PREFIX + who, scope);
  if (current === scope) return false;
  rawStorage.write(APPEARANCE_SCOPE_KEY, scope);
  return true;
}

/**
 * Point the next page load at the account `email`, before a switch reloads.
 * The boot script then paints that account's appearance on the first frame.
 */
export function pointAppearanceAt(email: string | null | undefined): void {
  const scope = appearanceScopeFor(email, null);
  if (scope) rawStorage.write(APPEARANCE_SCOPE_KEY, scope);
}

/**
 * The client half: bind the session's account, and when the pointer moved,
 * re-read the store and apply the scope's colour mode. `ThemeProvider` calls
 * it from an effect.
 */
export function reconcileAppearanceScope(
  email: string | null | undefined,
  organizationId: string | null | undefined,
  apply: { rehydrate: () => void; setMode: (mode: ThemeMode) => void },
): boolean {
  if (!bindAppearanceScope(email, organizationId)) return false;
  apply.rehydrate();
  apply.setMode(themeStorage.getMode() ?? DEFAULT_MODE);
  return true;
}
