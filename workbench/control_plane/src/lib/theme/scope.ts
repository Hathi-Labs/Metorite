/**
 * Which account's appearance this browser paints (owner bug, 2026-10-11).
 *
 * The owner reported it: "setting the appearance in one organization carries
 * over to others." Mode, density and accent lived under fixed keys, so every
 * account signed in to one browser shared them. Now each account has its own
 * copy, and `storage.ts` keeps the keys.
 *
 * The scope is `<email>|<organizationId>`. It is the format of `chatScope`
 * in `lib/sessions.ts`, so the browser has one scope vocabulary for both.
 *
 * Two things hold a scope, and they are different on purpose.
 *
 * - The POINTER `APPEARANCE_SCOPE_KEY` names the account that loaded last.
 *   The boot script reads it before paint, because the session cookie is
 *   httpOnly. `pointAppearanceAt` (in `switchTo`, before the reload) and
 *   `bindAppearanceScope` write it.
 * - Each TAB keeps its own scope in `storage.ts`. Every read and write of a
 *   preference goes there, never to the pointer, which other tabs also move
 *   (review round 2, P1). A tab whose pointer another tab moves goes passive.
 *
 * A bind that disagrees with the tab's scope moves it, and the store and the
 * mode re-read. That is a one-frame correction, and only in that case.
 *
 * ⚠️ **Legacy values.** A browser from before this change holds the bare
 * keys. The FIRST bind to a full scope copies them into that scope, deletes
 * the bare member keys, and sets `APPEARANCE_ADOPTED_KEY`. A second account
 * then starts from its organization's defaults and inherits nothing. The bare
 * `theme` key stays, because next-themes reads it.
 *
 * ⚠️ **Sign-out.** "Sign out of all accounts" and a detected sign-out clear
 * that account's keys and the pointer (`clearAppearanceForAccounts`), as the
 * chat namespaces are cleared. The sign-in page then paints the bare keys.
 *
 * Fence: `scope.test.ts`.
 */

import { chatScope, NO_EMAIL } from "@/lib/sessions";
import {
  APPEARANCE_ADOPTED_KEY,
  APPEARANCE_LAST_SCOPE_PREFIX,
  APPEARANCE_SCOPE_KEY,
  DEFAULT_MODE,
  MODE_STORAGE_KEY,
  SCOPED_KEYS,
  SCOPE_SEPARATOR,
  STORAGE_KEYS,
  isAppearancePassive,
  rawStorage,
  scopedKey,
  setTabAppearanceScope,
  tabAppearanceScope,
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
 * The appearance scope of one account, or null when there is no FULL scope
 * to name. A missing org id takes the last one seen for the same email.
 *
 * ⚠️ A scope with no org (`email|`) is NO scope (review round 2, P2).
 * `/auth/me` answers `organization: {}` when its org query fails. A first
 * bind on `email|` adopted the legacy values into a scope that the next load
 * never read again, and they were lost. With no org and none remembered, the
 * tab stays on the keys it already uses.
 */
export function appearanceScopeFor(
  email: string | null | undefined,
  organizationId: string | null | undefined,
): string | null {
  const who = normalEmail(email);
  if (!who) return null;
  const org = (organizationId ?? "").trim() || rememberedOrg(who);
  return org ? chatScope(who, org) : null;
}

/** Copy the bare values into `scope` once, then delete the bare member keys. */
function adoptLegacy(scope: string): void {
  for (const key of LEGACY_COPIED_KEYS) {
    const value = rawStorage.read(key);
    const target = scopedKey(key, scope);
    if (value !== null && rawStorage.read(target) === null) rawStorage.write(target, value);
  }
  for (const key of LEGACY_MEMBER_KEYS) rawStorage.write(key, null);
  rawStorage.write(APPEARANCE_ADOPTED_KEY, "1");
}

/**
 * Bind the account the session names to THIS tab. Returns true when the
 * tab's scope moved, and the caller must then re-read the store and the
 * colour mode. It also points the next page load at this account, because
 * this tab loaded last. It changes nothing with no full scope to name, or
 * in a passive tab (see `storage.ts`).
 */
export function bindAppearanceScope(
  email: string | null | undefined,
  organizationId: string | null | undefined,
): boolean {
  if (isAppearancePassive()) return false;
  const scope = appearanceScopeFor(email, organizationId);
  if (!scope) return false;
  // Read the tab's scope BEFORE the pointer moves: a tab that never read one
  // takes it from the pointer, and must take the scope its boot painted.
  const before = tabAppearanceScope();
  if (rawStorage.read(APPEARANCE_ADOPTED_KEY) === null) adoptLegacy(scope);
  const who = scope.slice(0, scope.indexOf("|"));
  if (rawStorage.read(APPEARANCE_LAST_SCOPE_PREFIX + who) !== scope) {
    rawStorage.write(APPEARANCE_LAST_SCOPE_PREFIX + who, scope);
  }
  if (rawStorage.read(APPEARANCE_SCOPE_KEY) !== scope) rawStorage.write(APPEARANCE_SCOPE_KEY, scope);
  setTabAppearanceScope(scope);
  return before !== scope;
}

/**
 * Point the next page load at the account `email`, before a switch reloads.
 * The boot script then paints that account's appearance on the first frame.
 * An email with no remembered org leaves the pointer, and the bind after the
 * reload corrects it.
 */
export function pointAppearanceAt(email: string | null | undefined): void {
  const scope = appearanceScopeFor(email, null);
  if (scope) rawStorage.write(APPEARANCE_SCOPE_KEY, scope);
}

/**
 * The client half: bind the session's account, and when the tab's scope
 * moved, re-read the store and apply the scope's colour mode.
 * `ThemeProvider` calls it from an effect, once for each identity.
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

/**
 * Forget the appearance of each account in `emails` (review round 2, P2):
 * every `<key>:<email>|*` copy, the remembered scope and, when it names one
 * of them, the pointer. It follows the chat idiom of `lib/sessions.ts`. A
 * sign-out leaves no address of a signed-out account in a key name.
 */
export function clearAppearanceForAccounts(emails: readonly string[]): void {
  if (typeof window === "undefined") return;
  const who = [...new Set(emails.map((e) => normalEmail(e)).filter((e): e is string => !!e))];
  if (who.length === 0) return;
  const prefixes = who.flatMap((e) => SCOPED_KEYS.map((k) => `${k}${SCOPE_SEPARATOR}${e}|`));
  const owns = (scope: string | null) => !!scope && who.some((e) => scope.startsWith(`${e}|`));
  try {
    const ls = window.localStorage;
    const doomed: string[] = [];
    for (let i = 0; i < ls.length; i++) {
      const k = ls.key(i);
      if (k && prefixes.some((p) => k.startsWith(p))) doomed.push(k);
    }
    for (const k of doomed) ls.removeItem(k);
    for (const e of who) ls.removeItem(APPEARANCE_LAST_SCOPE_PREFIX + e);
    if (owns(ls.getItem(APPEARANCE_SCOPE_KEY))) ls.removeItem(APPEARANCE_SCOPE_KEY);
  } catch {
    /* storage unavailable: nothing is stored */
  }
  // This tab is signed out now. It falls back to the bare keys, as before.
  if (owns(tabAppearanceScope())) setTabAppearanceScope(null);
}

/**
 * A sign-out the page did not start (an expiry, the middleware redirect):
 * forget the appearance of the account this tab showed. `useChatSignOutClear`
 * calls it beside `clearSignedOutAccount`.
 */
export function clearSignedOutAppearance(): void {
  const scope = tabAppearanceScope() ?? rawStorage.read(APPEARANCE_SCOPE_KEY);
  if (scope && scope.includes("|")) clearAppearanceForAccounts([scope.slice(0, scope.indexOf("|"))]);
}
