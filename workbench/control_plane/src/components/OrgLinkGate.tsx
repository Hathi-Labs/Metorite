"use client";

/**
 * OrgLinkGate — opens an org-aware link in the account it belongs to
 * (MT-1k, `saas_multitenancy.md` "Org-aware links", owner 2026-10-10).
 *
 * A link with `?org=<organization uuid>` names the organization of the record
 * it points at. This gate reads that parameter BEFORE the page mounts, so the
 * page never fetches as the wrong account:
 *
 *   • the active account's org, or a malformed id → remove `org`, show the page
 *   • another signed-in account's org → switch to it, then reload on the same
 *     path and query without `org`
 *   • no signed-in account of that org → one notice, with "Sign in to that
 *     account" and "Stay here"
 *
 * The rule is `decideOrgLink` in `lib/orgLink.ts`, pure and fenced there.
 * This file is the wiring. It is presentation only: a switch goes through the
 * same-origin `POST /api/accounts/switch`, and the gateway re-authorizes every
 * request as the active session.
 *
 * `useSearchParams` needs a Suspense boundary at build. The fallback is the
 * page itself, which is what this gate rendered before it existed.
 */

import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useAccess } from "@/components/AccessProvider";
import { SwitchingCover } from "@/components/AccountSwitcher";
import { addAccount, fetchAccounts, switchTo, type Accounts } from "@/lib/accountSwitch";
import { ORG_PARAM, decideOrgLink, signInUrl, withoutOrg } from "@/lib/orgLink";

/** The link in the address bar: path, query and hash. */
function here(): string {
  const { pathname, search, hash } = window.location;
  return `${pathname}${search}${hash}`;
}

/** Remove `org` from the address bar. Next syncs `useSearchParams` with it. */
function stripOrg(): void {
  window.history.replaceState(null, "", withoutOrg(here()));
}

function OrgLinkNotice({ canAdd, onStay }: { canAdd: boolean; onStay: () => void }) {
  const [pending, setPending] = useState(false);
  const signIn = async () => {
    setPending(true);
    const link = here();
    // Keep this account in a slot first, so it is one tap away later. With
    // the switcher off there is no slot, and the sign-in replaces it.
    if (canAdd && (await addAccount(undefined, link))) return;
    window.location.assign(signInUrl(link, false));
  };
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-md rounded-xl border border-border bg-card p-8 text-center">
        <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted">
          <Icon name="Building2" size={20} className="text-muted-foreground" />
        </div>
        {/* A second-level heading, because the app bar holds the first (`pageHeading.test.ts`). */}
        <h2 className="text-base font-semibold text-foreground">
          This link is for a Metorite workspace you&apos;re not signed in to.
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {canAdd
            ? "Sign in to the account that belongs to that workspace. This account stays signed in."
            : "Sign in to the account that belongs to that workspace to open it."}
        </p>
        <div className="mt-6 flex flex-col items-center gap-2">
          <Button onClick={() => void signIn()} loading={pending} disabled={pending}>
            Sign in to that account
          </Button>
          <Button variant="ghost" onClick={onStay} disabled={pending}>
            Stay here
          </Button>
        </div>
      </div>
    </div>
  );
}

function OrgLinkGateInner({ children }: { children: ReactNode }) {
  const params = useSearchParams();
  const param = params?.get(ORG_PARAM) ?? null;
  const { access, loading } = useAccess();
  // The accounts read, kept with the `org` value it was made for, so a new
  // link reads them again.
  const [read, setRead] = useState<{ param: string; accounts: Accounts } | null>(null);
  // The `org` value whose switch the server refused (the slot is gone).
  const [refused, setRefused] = useState<string | null>(null);
  const started = useRef<string | null>(null);

  const accounts = read && read.param === param ? read.accounts : null;
  let decision = loading
    ? ({ kind: "none" } as const)
    : decideOrgLink({
        param,
        authenticated: access.authenticated,
        activeOrgId: access.organization?.id,
        accounts,
      });
  if (decision.kind === "switch" && refused === param) decision = { kind: "notice", canAdd: true };

  const kind = decision.kind;
  const slot = decision.kind === "switch" ? decision.slot : null;

  useEffect(() => {
    if (kind === "strip") stripOrg();
  }, [kind, param]);

  useEffect(() => {
    if (kind !== "load" || param === null) return;
    let alive = true;
    void fetchAccounts(true).then((a) => {
      if (alive) setRead({ param, accounts: a });
    });
    return () => {
      alive = false;
    };
  }, [kind, param]);

  useEffect(() => {
    if (kind !== "switch" || slot === null || param === null) return;
    // One POST per link, also under React's double effect in development.
    if (started.current === param) return;
    started.current = param;
    void switchTo(slot, undefined, withoutOrg(here())).then((ok) => {
      if (!ok) setRefused(param);
    });
  }, [kind, slot, param]);

  if (decision.kind === "load") return <SwitchingCover email={null} />;
  if (decision.kind === "switch") return <SwitchingCover email={decision.email} />;
  if (decision.kind === "notice") return <OrgLinkNotice canAdd={decision.canAdd} onStay={stripOrg} />;
  return <>{children}</>;
}

export default function OrgLinkGate({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<>{children}</>}>
      <OrgLinkGateInner>{children}</OrgLinkGateInner>
    </Suspense>
  );
}
