/**
 * The browser half of the account switcher (MT-1k slice A2,
 * `saas_multitenancy.md`). The server half is `/api/accounts/*`.
 *
 * ⚠️ Every switch ends in a FULL reload at `/` (rule 3). In-memory state, such
 * as the read cache, the stores and the module caches, belongs to the account
 * that loaded it, and a reload is the one reset that misses nothing. `/`,
 * because the current path may name a record of the other organization.
 *
 * Before the reload, it clears the browser storage that belongs to ONE
 * organization. Without that, the other organization's logo paints first, and
 * Email asks for a mailbox the new account cannot see.
 *
 * Fence: `accountSwitch.test.ts`.
 */
import { signOut } from "next-auth/react";
import { clearAccountNamespaces } from "@/lib/sessions";

/**
 * The keys that belong to one organization. Each test in
 * `accountSwitch.test.ts` reads the module that owns a key, so a rename there
 * fails here instead of leaking quietly.
 */
export const ORG_SCOPED_KEYS = [
  // lib/orgBranding.ts — the logo, painted before the network answers.
  "cc-org-branding-v1",
  // lib/theme/storage.ts — the organization's default density.
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
}

export interface Accounts {
  enabled: boolean;
  active: { email: string; name: string | null } | null;
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

/** Make another signed-in account the active one. False if it is gone. */
export async function switchTo(slot: number, go: (url: string) => void = (u) => window.location.assign(u)) {
  const res = await act("switch", { slot });
  if (!res.ok) return false;
  clearOrgScopedStorage();
  go("/");
  return true;
}

/** Keep this account, then sign in to another one. */
export async function addAccount(go: (url: string) => void = (u) => window.location.assign(u)) {
  const res = await act("stash");
  if (!res.ok) return false;
  clearOrgScopedStorage();
  go("/signin?add=1");
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
