/**
 * Several accounts in one browser, the Gmail model (MT-1k slice A2,
 * `saas_multitenancy.md`). Server only: this file reads session tokens.
 *
 * Auth.js holds ONE session cookie, and a new sign-in overwrites it. So the
 * browser keeps each other account's own session token, unchanged, in a slot
 * cookie. A switch swaps the active token and a slot token.
 *
 * ⚠️ Nothing here mints a token. Every token in a slot came out of a completed
 * Auth.js sign-in, through the `signIn` gate in `auth.ts`. This file only moves
 * tokens between cookies, and decodes them to check they are still good.
 *
 * Fence: `accountSlots.test.ts`.
 */
import { decode } from "next-auth/jwt";

/** Other accounts a browser may hold, besides the active one. */
export const SLOT_COUNT = 4;

/** Auth.js's session cookie names. `__Secure-` on https (`@auth/core` cookie.js). */
export const SESSION_COOKIE_SECURE = "__Secure-authjs.session-token";
export const SESSION_COOKIE_PLAIN = "authjs.session-token";

/**
 * `__Host-`, not `__Secure-`. A sibling subdomain (`metorite.com`,
 * `operator.metorite.com`) can set a `__Secure-` cookie with
 * `Domain=.metorite.com`, and so could plant its own account in the list. The
 * browser refuses a `__Host-` cookie that names a domain (security review
 * 2026-10-07, P3). The options here (Secure, `Path=/`, no Domain) already
 * meet the prefix.
 */
export function slotCookieName(index: number, secure: boolean): string {
  return `${secure ? "__Host-" : ""}mt-acct-${index}`;
}

/** A slot cookie of either form, for the proxy's sweep. */
export function isSlotCookieName(name: string): boolean {
  return /^(__Host-)?mt-acct-\d$/.test(name);
}

/** The part of a cookie store this file reads. NextRequest's `cookies` fits. */
export interface CookieReader {
  get(name: string): { value: string } | undefined;
}

export interface ActiveCookie {
  name: string;
  secure: boolean;
  value: string;
}

/**
 * The browser's session cookie, or null.
 *
 * ⚠️ Null for a CHUNKED session too (`…session-token.0`). Auth.js splits a
 * token larger than one cookie, and a slot holds one cookie. Our tokens carry
 * an email, a name and a provider, so they never chunk. If one ever does, the
 * switcher declines rather than store half a token.
 */
export function activeSessionCookie(cookies: CookieReader): ActiveCookie | null {
  for (const [name, secure] of [
    [SESSION_COOKIE_SECURE, true],
    [SESSION_COOKIE_PLAIN, false],
  ] as const) {
    const value = cookies.get(name)?.value;
    if (value) return { name, secure, value };
  }
  return null;
}

/** What the menu shows for an account. */
export interface Account {
  email: string;
  name: string | null;
  /** Seconds since the epoch, from the token's own `exp`. */
  exp: number;
}

/**
 * Decode one session token. Null unless it decrypts with `secret` and has not
 * expired. A rotated secret therefore drops every slot, which is correct: the
 * active session ends too.
 *
 * `salt` is the SESSION cookie's name, because that is what Auth.js encrypted
 * the token with, whichever cookie holds it now.
 */
export async function decodeAccount(
  token: string,
  salt: string,
  secret: string,
  nowMs: number,
): Promise<Account | null> {
  try {
    const t = await decode({ token, salt, secret });
    const email = typeof t?.email === "string" ? t.email.trim() : "";
    const exp = typeof t?.exp === "number" ? t.exp : 0;
    if (!email || exp * 1000 <= nowMs) return null;
    return { email, name: typeof t?.name === "string" ? t.name : null, exp };
  } catch {
    return null;
  }
}

export interface Slot {
  index: number;
  raw: string;
  account: Account;
}

/** Every good slot, in index order. A bad or empty slot is null. */
export async function readSlots(
  cookies: CookieReader,
  active: ActiveCookie,
  secret: string,
  nowMs: number,
): Promise<(Slot | null)[]> {
  const out: (Slot | null)[] = [];
  for (let i = 0; i < SLOT_COUNT; i++) {
    const raw = cookies.get(slotCookieName(i, active.secure))?.value;
    const account = raw ? await decodeAccount(raw, active.name, secret, nowMs) : null;
    out.push(raw && account ? { index: i, raw, account } : null);
  }
  return out;
}

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/**
 * Where to keep the token of `email`: the slot that already holds that email,
 * else an empty slot, else the slot that expires first (rule 5: a fifth
 * account replaces the oldest).
 */
export function stashIndex(slots: readonly (Slot | null)[], email: string): number {
  const own = slots.findIndex((s) => s !== null && same(s.account.email, email));
  if (own >= 0) return own;
  const empty = slots.findIndex((s) => s === null);
  if (empty >= 0) return empty;
  let oldest = 0;
  slots.forEach((s, i) => {
    if (s && slots[oldest] && s.account.exp < slots[oldest]!.account.exp) oldest = i;
  });
  return oldest;
}

/**
 * The other accounts to show: never the active email, and each email once
 * (the newest token wins). A slot that holds the active email is a leftover
 * from an "Add another account" the member cancelled.
 */
export function otherAccounts(active: Account, slots: readonly (Slot | null)[]): Slot[] {
  const byEmail = new Map<string, Slot>();
  for (const s of slots) {
    if (!s || same(s.account.email, active.email)) continue;
    const key = s.account.email.toLowerCase();
    const seen = byEmail.get(key);
    if (!seen || s.account.exp > seen.account.exp) byEmail.set(key, s);
  }
  return [...byEmail.values()].sort((a, b) => a.index - b.index);
}
