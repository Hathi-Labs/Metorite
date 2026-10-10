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
import { signOut } from "next-auth/react";
import { clearAccountNamespaces } from "@/lib/sessions";
import { safeLocalPath, signInUrl } from "@/lib/orgLink";
import { pointAppearanceAt } from "@/lib/theme/scope";

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
 * Sign out of every account in this browser: empty the slots, clear each
 * account's chat namespace (rule 4), then end the active session.
 */
export async function signOutAll(): Promise<void> {
  try {
    const res = await act("signout-all");
    const body = res.ok ? ((await res.json()) as { emails?: string[] }) : {};
    clearAccountNamespaces(body.emails ?? []);
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
      if (shouldReloadFor(email, e.data?.email)) window.location.assign("/");
    };
    channel.postMessage({ email });
    return () => channel.close();
  }, [email]);
}
