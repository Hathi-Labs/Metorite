/**
 * The browser half of the account switcher (MT-1k slice A2,
 * `saas_multitenancy.md`). The server half is `/api/accounts/*`.
 *
 * ⚠️ Every switch ends in a FULL reload at `/` (rule 3). In-memory state, such
 * as the read cache, the stores and the module caches, belongs to the account
 * that loaded it, and a reload is the one reset that misses nothing. `/`,
 * because the current path may name a record of the other organization.
 * An org-aware link (`lib/orgLink.ts`) is the one exception. It names a
 * record of the TARGET organization, so it lands on that path instead.
 *
 * Before the reload, it clears the browser storage that belongs to ONE
 * organization. Without that, the other organization's logo paints first, and
 * Email asks for a mailbox the new account cannot see.
 *
 * Fence: `accountSwitch.test.ts`.
 */
import { useEffect } from "react";
import { bindIdentity } from "@/lib/dataCache";
import { signOut } from "next-auth/react";
import { clearAccountNamespaces } from "@/lib/sessions";
import { safeLocalPath, signInUrl } from "@/lib/orgLink";
import { clearAppearanceForAccounts, pointAppearanceAt } from "@/lib/theme/scope";

/**
 * The keys that belong to one organization. Each test in
 * `accountSwitch.test.ts` reads the module that owns a key, so a rename there
 * fails here instead of leaking quietly.
 */
export const ORG_SCOPED_KEYS = [
  // lib/orgBranding.ts — the logo, painted before the network answers.
  "cc-org-branding-v1",
  // lib/theme/storage.ts — the organization's default density, in the bare
  // key a browser with no appearance scope still reads. A scoped copy
  // (`cc-density-org:<scope>`) belongs to one account and stays.
  "cc-density-org",
  // app/email/lib/emailStore.ts — the last mailbox, an id of one org.
  "cc.email.selectedAccountId",
] as const;

export function clearOrgScopedStorage(): void {
  try {
    for (const k of ORG_SCOPED_KEYS) localStorage.removeItem(k);
  } catch {
    /* storage unavailable: nothing to clear */
  }
}

export interface OtherAccount {
  slot: number;
  email: string;
  name: string | null;
  organization: string | null;
  /** With `?orgs=1` only. The id that an org-aware link names. */
  organization_id?: string | null;
  organization_slug?: string | null;
}

export interface Accounts {
  enabled: boolean;
  active: {
    email: string;
    name: string | null;
    /** With `?orgs=1` only. */
    organization_id?: string | null;
    organization_slug?: string | null;
  } | null;
  others: OtherAccount[];
}

export const NO_ACCOUNTS: Accounts = { enabled: false, active: null, others: [] };

export async function fetchAccounts(withOrgs: boolean): Promise<Accounts> {
  try {
    const res = await fetch(`/api/accounts${withOrgs ? "?orgs=1" : ""}`, { cache: "no-store" });
    if (!res.ok) return NO_ACCOUNTS;
    const body = (await res.json()) as Partial<Accounts>;
    if (!body.enabled) return NO_ACCOUNTS;
    return { enabled: true, active: body.active ?? null, others: body.others ?? [] };
  } catch {
    return NO_ACCOUNTS;
  }
}

async function act(action: string, body?: unknown): Promise<Response> {
  return fetch(`/api/accounts/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * Make another signed-in account the active one. False if it is gone.
 *
 * `target` is where the reload lands, `/` by default. Only a same-origin
 * relative path is used, and anything else lands on `/`, so a link cannot
 * turn a switch into a redirect off the site.
 */
export async function switchTo(
  slot: number,
  go: (url: string) => void = (u) => window.location.assign(u),
  target = "/",
) {
  const res = await act("switch", { slot });
  if (!res.ok) return false;
  // Tell the other tabs NOW (security review round 2, P2). The cookie has
  // already changed under them. Waiting for this tab's reload to announce
  // leaves them writing as the new account for a few seconds, or for good
  // if this tab closes first. The announcement on mount stays as the backstop.
  // ⚠️ Mark this tab as leaving FIRST. Its own `useAccountTabSync` listener is
  // a second BroadcastChannel object, so it hears this announcement too, and
  // it started a second navigation that aborted this one (CI, 2026-10-07).
  leaving = true;
  try {
    const { email } = (await res.json()) as { email?: string };
    if (email) {
      announceAccount(email);
      // Paint the target's own appearance on the first frame, not this
      // account's (lib/theme/scope.ts). The boot script reads the pointer.
      pointAppearanceAt(email);
    }
  } catch {
    /* no body: the backstop still runs */
  }
  clearOrgScopedStorage();
  forgetMember();
  go(safeLocalPath(target) ?? "/");
  return true;
}

/**
 * Keep this account, then sign in to another one. With `callbackUrl`, the
 * sign-in comes back to that same-origin path.
 */
export async function addAccount(
  go: (url: string) => void = (u) => window.location.assign(u),
  callbackUrl?: string,
) {
  const res = await act("stash");
  if (!res.ok) return false;
  clearOrgScopedStorage();
  go(callbackUrl === undefined ? "/signin?add=1" : signInUrl(callbackUrl, true));
  return true;
}

export async function removeAccount(slot: number): Promise<boolean> {
  return (await act("remove", { slot })).ok;
}

/**
 * What `signOutThis` did: this account alone, every account, or a reload as
 * the account the cookie names after the fallback itself failed.
 */
export type SignOutResult = "one" | "all" | "reload";

/**
 * The words of the fallback, shown on `/signin` after the full sign-out
 * (`SignOutNotice.tsx`). The page reloads in between, so the flag waits in
 * this tab's `sessionStorage`. It is one word, never an account or a row.
 */
export const SIGN_OUT_ALL_NOTICE =
  "Couldn't sign out of that account alone, so we signed out of all accounts on this browser.";
export const SIGN_OUT_NOTICE_KEY = "cc-signout-notice";
/** How long the notice must be on screen before a reload stops showing it. */
export const NOTICE_SEEN_MS = 3_000;
/**
 * The flag's whole life. The fallback reaches `/signin` in a second or two,
 * so an older flag is left over from a sign-out that never showed it, and it
 * must not tell a later `/signin?add=1` a false story (review of 2026-10-12).
 */
export const NOTICE_MAX_AGE_MS = 60_000;

function markSignOutNotice(now: number = Date.now()): void {
  try {
    // The time it was set, so a stale flag can be told apart.
    sessionStorage.setItem(SIGN_OUT_NOTICE_KEY, String(now));
  } catch {
    /* no storage: the sign-out still runs, with no notice */
  }
}

/**
 * The notice, if a fallback left one. Reading does NOT clear it: a remount
 * of the page after the first read (Fast Refresh in dev, a hydration retry)
 * would lose it. `clearSignOutNotice` ends it.
 */
export function peekSignOutNotice(now: number = Date.now()): string | null {
  try {
    const raw = sessionStorage.getItem(SIGN_OUT_NOTICE_KEY);
    if (raw === null) return null;
    const at = Number(raw);
    // Not a time (the "all" of the first version), from the future, or older
    // than its life: a leftover. Clear it and show nothing.
    if (!Number.isFinite(at) || at > now || now - at > NOTICE_MAX_AGE_MS) {
      sessionStorage.removeItem(SIGN_OUT_NOTICE_KEY);
      return null;
    }
    return SIGN_OUT_ALL_NOTICE;
  } catch {
    return null;
  }
}

export function clearSignOutNotice(): void {
  try {
    sessionStorage.removeItem(SIGN_OUT_NOTICE_KEY);
  } catch {
    /* no storage: nothing to clear */
  }
}

/** One `remove`, true only for a 2xx. A network error is false. */
async function removed(slot: number): Promise<boolean> {
  try {
    return (await act("remove", { slot })).ok;
  } catch {
    return false;
  }
}

/**
 * Sign out of the ACTIVE account only, and keep the others (owner,
 * 2026-10-12, the top-right account menu). It uses the two server actions
 * that exist, so no new route moves a token:
 *
 *   1. `switch` to `next`. The server puts the active session in `next`'s
 *      slot and makes `next` the active one.
 *   2. `remove` that slot, which now holds the account that signs out.
 *
 * ⚠️ **It fails CLOSED** (review of 2026-10-12). A sign-out that leaves the
 * account one click away, with no password, while the screen says it left,
 * is worse than one that signs out too much. So:
 *
 * - `switch` refused (a 409 when another tab removed `next`, an expired
 *   token, any non-2xx) or thrown: sign out of EVERY account (`all`).
 * - `remove` refused or thrown: one retry. A second failure signs out of
 *   every account, so this tab never lands on `/` with the leaving
 *   account's slot still held.
 *
 * The fallback leaves `SIGN_OUT_ALL_NOTICE` for `/signin`. After a good
 * switch it tells the other tabs and points the appearance at once, as
 * `switchTo` does: the cookie already names `next`. Then it clears the
 * leaving account's browser storage (rule 4) and reloads at `/`.
 * ⚠️ With only one account, call `signOutAll`: there is no other to land on.
 */
export async function signOutThis(
  activeEmail: string,
  next: { slot: number },
  go: (url: string) => void = (u) => window.location.assign(u),
  all: () => Promise<void> = signOutAll,
): Promise<SignOutResult> {
  // This tab leaves, by one path or the other. Its own channel listener must
  // not start a second navigation (see `switchTo`).
  leaving = true;
  // True once the server swapped the cookie to `next`. From then on this tab
  // must never stay on the leaving account's page.
  let switched = false;
  const fallBack = async (): Promise<SignOutResult> => {
    markSignOutNotice();
    await all();
    return "all";
  };
  try {
    let res: Response;
    try {
      res = await act("switch", { slot: next.slot });
    } catch {
      return await fallBack();
    }
    if (!res.ok) return await fallBack();
    switched = true;
    let email: string | undefined;
    try {
      ({ email } = (await res.json()) as { email?: string });
    } catch {
      /* no body: the announcement on mount still runs */
    }
    // NOW, before the remove: the cookie already names `next`, so a stale
    // tab must not write as the leaving account meanwhile (security review
    // round 2, P2, the rule `switchTo` follows).
    if (email) {
      announceAccount(email);
      pointAppearanceAt(email);
    }
    // The switch moved the leaving account into `next.slot`. Forget it there.
    if (!(await removed(next.slot)) && !(await removed(next.slot))) return await fallBack();
    clearAccountNamespaces([activeEmail]);
    clearAppearanceForAccounts([activeEmail]);
    clearOrgScopedStorage();
    forgetMember();
    go("/");
    return "one";
  } catch (e) {
    // Only the fallback itself can throw here. Its notice was never shown,
    // so it must not wait for a later `/signin` (review of 2026-10-12).
    clearSignOutNotice();
    if (switched) {
      // ⚠️ The cookie already names `next`. Staying on this page would let a
      // later write go as `next` under the leaving account's screen, so load
      // `/` as the account the cookie names. The leaving account may still
      // sit in a slot, and the menu there lists it with its remove control.
      forgetMember();
      go("/");
      return "reload";
    }
    // No switch: the cookie still names this account, so this tab stays and
    // must hear the other tabs again.
    leaving = false;
    throw e;
  }
}

/**
 * Sign out of every account in this browser: empty the slots, clear each
 * account's chat namespace (rule 4), then end the active session.
 */
export async function signOutAll(): Promise<void> {
  try {
    const res = await act("signout-all");
    const body = res.ok ? ((await res.json()) as { emails?: string[] }) : {};
    clearAccountNamespaces(body.emails ?? []);
    // The same accounts' appearance keys, which carry each address in their
    // names (lib/theme/scope.ts).
    clearAppearanceForAccounts(body.emails ?? []);
  } catch {
    /* the sign-out below still runs */
  }
  clearOrgScopedStorage();
  await signOut({ callbackUrl: "/signin" });
}

/**
 * Keep every open tab on the account the cookie names (security review
 * 2026-10-07, P1).
 *
 * ⚠️ The session cookie belongs to the browser, not the tab. After a switch in
 * one tab, every other tab still shows the old account, while each of its
 * fetches and writes now goes as the new one. A task typed "in org A" would be
 * created in org B. So each tab announces its account when it loads, and a tab
 * that hears another account reloads at `/`.
 *
 * Only a tab that has just LOADED announces, so it always names the cookie's
 * current account. A stale tab hears, and never speaks first. Fence:
 * `accountSwitch.test.ts` (the rule) and `e2e/account-switcher.spec.ts` (two
 * tabs).
 */
export const ACCOUNT_CHANNEL = "mt-active-account";

/**
 * Forget the member of this page before it leaves for another account
 * (WS-58 MD-2b fix round 1). The cookie names the other account already, so
 * the read cache must not hold this one. The cache clear also stops each
 * queue that holds this member's writes, so a `pagehide` send during the
 * switch sends nothing (`lib/shell/feedState.ts`).
 */
export function forgetMember(): void {
  bindIdentity(null);
}

/** True once this tab has switched and is on its way to `/`. */
let leaving = false;

/** For tests: forget that this tab switched. */
export function resetLeaving(): void {
  leaving = false;
}

export function shouldReloadFor(mine: string | null, announced: unknown): boolean {
  return (
    !leaving &&
    !!mine &&
    typeof announced === "string" &&
    announced.trim() !== "" &&
    announced.toLowerCase() !== mine.toLowerCase()
  );
}

/** Post the active account to every other tab of this origin. */
export function announceAccount(email: string): void {
  if (typeof BroadcastChannel === "undefined") return;
  try {
    const channel = new BroadcastChannel(ACCOUNT_CHANNEL);
    channel.postMessage({ email });
    channel.close();
  } catch {
    /* no channel: each tab still announces when it loads */
  }
}

export function useAccountTabSync(email: string | null): void {
  useEffect(() => {
    if (!email || typeof BroadcastChannel === "undefined") return;
    let channel: BroadcastChannel;
    try {
      channel = new BroadcastChannel(ACCOUNT_CHANNEL);
    } catch {
      return;
    }
    channel.onmessage = (e: MessageEvent<{ email?: unknown }>) => {
      if (shouldReloadFor(email, e.data?.email)) {
        forgetMember();
        window.location.assign("/");
      }
    };
    channel.postMessage({ email });
    return () => channel.close();
  }, [email]);
}
