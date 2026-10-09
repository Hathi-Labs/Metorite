"use client";

/**
 * The account switcher (MT-1k slice A2, `saas_multitenancy.md`). The Gmail
 * model: every account this browser is signed in to, one tap apart.
 *
 * Three places, one menu:
 *   • `SidebarAccountFooter` — the desktop sidebar's foot. Expanded, a button
 *     with the name and email. Folded, an avatar at the foot of the rail.
 *   • `DrawerAccountSection` — the phone drawer's foot. The same list unfolds
 *     in place, so a phone needs no second overlay over the drawer.
 *
 * `useAccounts()` reads `/api/accounts`. While the flag is off it answers
 * `enabled: false`, and the callers keep the footer they had.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import ThemeToggle from "@/components/ThemeToggle";
import { useAccess } from "@/components/AccessProvider";
import { categoricalAccent } from "@/lib/categorical";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import {
  NO_ACCOUNTS,
  addAccount,
  fetchAccounts,
  removeAccount,
  signOutAll,
  switchTo,
  type Accounts,
  type OtherAccount,
} from "@/lib/accountSwitch";
import type { AccountLink } from "@/lib/shell/shellNav";

/**
 * The accounts this browser holds. `withOrgs` asks for each account's
 * organization name on the first read too, for a surface that shows them at
 * once (the phone menu's organization block).
 */
export function useAccounts(withOrgs = false) {
  const [accounts, setAccounts] = useState<Accounts>(NO_ACCOUNTS);
  const reload = useCallback(async (orgs: boolean) => {
    setAccounts(await fetchAccounts(orgs));
  }, []);
  useEffect(() => {
    void reload(withOrgs);
  }, [reload, withOrgs]);
  return { accounts, reload };
}

/**
 * One tap to another account: the cover while the page reloads, and the
 * error when the account is no longer signed in. The menu and the phone's
 * organization block share it, so the two switch the same way.
 */
function useSwitchAccount(onChanged: () => void) {
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pick = async (o: OtherAccount) => {
    setError(null);
    setSwitching(o.email);
    if (!(await switchTo(o.slot))) {
      setSwitching(null);
      setError(`${o.email} is no longer signed in here. Add it again.`);
      onChanged();
    }
  };
  return { switching, error, pick };
}

function initial(name: string | null | undefined, email: string): string {
  return (name?.trim() || email).charAt(0).toUpperCase();
}

/** A round initial, in the account's own stable hue, as Gmail does. */
function Avatar({ email, name, size = "md" }: { email: string; name?: string | null; size?: "xs" | "sm" | "md" | "lg" }) {
  const box =
    size === "lg" ? "h-10 w-10 text-base"
    : size === "sm" ? "h-7 w-7 text-xs"
    : size === "xs" ? "h-5 w-5 text-[10px]"
    : "h-8 w-8 text-sm";
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full border font-semibold ${box} ${categoricalAccent(email).chip}`}
    >
      {initial(name, email)}
    </span>
  );
}

/** "Switching to …" over the whole page, until the reload lands. */
function SwitchingCover({ email }: { email: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-0 z-[90] flex flex-col items-center justify-center gap-3 bg-background/85 backdrop-blur-sm"
    >
      <Icon name="Loader2" size={22} className="animate-spin text-muted-foreground" />
      <p className="text-sm text-muted-foreground">
        Switching to <span className="font-medium text-foreground">{email}</span>
      </p>
    </div>
  );
}

/**
 * The menu body. The active account first, with its organization, then the
 * others, then the two actions.
 */
function AccountMenu({
  accounts,
  onChanged,
  showActive = true,
  showOthers = true,
  you,
  onNavigate,
}: {
  accounts: Accounts;
  onChanged: () => void;
  /** False in the phone drawer, whose row above already shows it. */
  showActive?: boolean;
  /** False in the phone drawer, whose organization block at the top lists them. */
  showOthers?: boolean;
  /**
   * The shell nav's account rows (NS-2): My Profile, My access, Appearance,
   * and Organisation for an admin. Absent, the menu is the switcher alone.
   */
  you?: readonly AccountLink[];
  /** Called when a row opens a page, so the menu or the drawer can close. */
  onNavigate?: () => void;
}) {
  const { access } = useAccess();
  const orgName = access.organization?.display_name || access.organization?.slug || null;
  const { switching, error, pick } = useSwitchAccount(onChanged);
  const active = accounts.active;
  if (!active) return null;

  return (
    <div className="text-sm">
      {showActive && (
      <div className="flex items-center gap-3 px-3 pb-3 pt-2">
        <Avatar email={active.email} name={active.name} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-foreground">{active.name ?? active.email}</div>
          <div className="truncate text-xs text-muted-foreground">{active.email}</div>
          {orgName && (
            <div className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground">
              <Icon name="Building2" size={11} className="shrink-0" />
              {orgName}
            </div>
          )}
        </div>
        <Icon name="Check" size={15} className="shrink-0 text-primary" aria-label="Active account" />
      </div>
      )}

      {you && you.length > 0 && (
        <nav aria-label="Your account" className={`${showActive ? "border-t border-border" : ""} p-1`}>
          {you.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              onClick={onNavigate}
              className="flex items-center gap-3 rounded-md px-3 py-2 text-[13px] text-foreground hover:bg-secondary tech-transition"
            >
              <Icon name={l.icon} size={15} className="shrink-0 text-muted-foreground" />
              {l.label}
            </Link>
          ))}
        </nav>
      )}

      {showOthers && accounts.others.length > 0 && (
        <div className={`${showActive || you?.length ? "border-t border-border" : ""} py-1`} role="list" aria-label="Other accounts">
          {accounts.others.map((o) => (
            <div key={o.slot} role="listitem" className="group flex items-center gap-1 px-1">
              <button
                onClick={() => void pick(o)}
                className="flex min-w-0 flex-1 items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-secondary tech-transition"
              >
                <Avatar email={o.email} name={o.name} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-foreground">{o.name ?? o.email}</span>
                  {/* The email once: a nameless account already shows it above. */}
                  {(o.organization || o.name) && (
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {[o.organization, o.name ? o.email : null].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                icon="X"
                aria-label={`Remove ${o.email} from this browser`}
                title="Remove from this browser"
                onClick={async () => {
                  await removeAccount(o.slot);
                  onChanged();
                }}
                // Always shown. A hover-only control never appears on touch
                // (`revealOnHover.test.ts`).
                className="text-muted-foreground"
              />
            </div>
          ))}
        </div>
      )}

      {error && <p className="px-3 pb-2 text-xs text-destructive">{error}</p>}

      <div className="border-t border-border p-1">
        {/* Only with the switcher on: off, there is no second slot to add. */}
        {accounts.enabled && (
          <Button
            variant="ghost"
            size="none"
            layout="flex items-center"
            icon="UserPlus"
            onClick={() => void addAccount()}
            className="w-full gap-3 px-3 py-2 text-[13px]"
          >
            Add another account
          </Button>
        )}
        <Button
          variant="ghost"
          size="none"
          layout="flex items-center"
          icon="LogOut"
          onClick={() => void signOutAll()}
          className="w-full gap-3 px-3 py-2 text-[13px]"
        >
          {accounts.others.length > 0 ? "Sign out of all accounts" : "Sign out"}
        </Button>
      </div>
      {switching && <SwitchingCover email={switching} />}
    </div>
  );
}

/**
 * With the switcher off, the shell nav still needs the active account for its
 * foot. It comes from the session then, with no other accounts.
 */
function withSessionFallback(
  accounts: Accounts,
  session: { user?: { email?: string | null; name?: string | null } } | null | undefined,
): Accounts {
  if (accounts.active || !session?.user?.email) return accounts;
  return { ...accounts, active: { email: session.user.email, name: session.user.name ?? null } };
}

/** Close on a click outside `root`, or on Escape. */
function useDismiss(open: boolean, close: () => void, root: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (shouldDismiss(e.target as Element | null, domClickWalk(root.current))) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, close, root]);
}

/**
 * The desktop sidebar's foot. Null while the switcher is off: the sidebar then
 * draws the footer it always had.
 */
export function SidebarAccountFooter({
  collapsed,
  accounts: switcher,
  reload,
  you,
}: {
  collapsed: boolean;
  accounts: Accounts;
  reload: (withOrgs: boolean) => Promise<void>;
  /** The shell nav's account rows (NS-2). With them, the foot always shows. */
  you?: readonly AccountLink[];
}) {
  const { data: session } = useSession();
  const accounts = withSessionFallback(switcher, you ? session : null);
  const [open, setOpen] = useState(false);
  // Where the menu sits: its BOTTOM edge just above the button, its left edge
  // on the button's. ⚠️ Not AnchoredPanel, which places a flipped panel by
  // its top edge from its MAXIMUM height. A short menu then floated about
  // 200px above the button it belongs to.
  const [box, setBox] = useState<{ left: number; bottom: number } | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, rootRef);
  const active = accounts.active;
  if (!active || (!accounts.enabled && !you)) return null;

  const toggle = () => {
    if (!open) {
      void reload(true);
      const r = rootRef.current?.getBoundingClientRect();
      if (r) setBox({ left: Math.max(8, r.left), bottom: window.innerHeight - r.top + 6 });
    }
    setOpen((o) => !o);
  };
  const count = accounts.others.length;
  const label = `Accounts: signed in as ${active.email}${count ? `, ${count} more` : ""}`;

  return (
    <div className={`border-t border-sidebar-border ${collapsed ? "flex justify-center p-2" : "px-2 py-2"}`}>
      <div ref={rootRef} className={collapsed ? "" : "flex items-center gap-1"}>
        <button
          onClick={toggle}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={label}
          title={collapsed ? label : undefined}
          className={`flex min-w-0 items-center gap-2.5 rounded-lg text-left hover:bg-sidebar-accent tech-transition ${
            collapsed ? "p-1" : "flex-1 px-2 py-1.5"
          }`}
        >
          <span className="relative">
            <Avatar email={active.email} name={active.name} size="sm" />
            {count > 0 && (
              <span className="absolute -bottom-0.5 -right-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full border border-sidebar bg-muted px-0.5 text-[8px] font-semibold text-muted-foreground">
                +{count}
              </span>
            )}
          </span>
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] font-medium text-sidebar-foreground/90">
                  {active.name ?? active.email}
                </span>
                <span className="block truncate text-[10px] text-muted-foreground">{active.email}</span>
              </span>
              <Icon name="ChevronsUpDown" size={13} className="shrink-0 text-muted-foreground" />
            </>
          )}
        </button>
        {!collapsed && <ThemeToggle />}
        {/* `position: fixed` inside the rail, so the rail's overflow does not
            clip it, and a click inside it is inside `rootRef`. */}
        {open && box && (
          <div
            role="dialog"
            aria-label="Accounts"
            style={{ left: box.left, bottom: box.bottom }}
            className="fixed z-[60] max-h-[calc(100vh-1rem)] w-72 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-md border border-border bg-card py-1 shadow-md"
          >
            <AccountMenu accounts={accounts} onChanged={() => void reload(true)} you={you} onNavigate={close} />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The phone drawer's foot: the active account as a row, and the menu unfolded
 * under it. While the switcher is off, `fallback`: the Sign out row it had.
 */
export function DrawerAccountSection({
  fallback,
  you,
  onNavigate,
  othersAtTop = false,
  actionsOnly = false,
}: {
  fallback: React.ReactNode;
  /**
   * The account sheet: the actions alone. Its organization block above it
   * already shows the account, so a row naming it again is noise.
   */
  actionsOnly?: boolean;
  /** The shell nav's account rows (NS-2). With them, the section always shows. */
  you?: readonly AccountLink[];
  onNavigate?: () => void;
  /**
   * The organization block at the top of the drawer lists the other accounts
   * (`DrawerOrgSwitch`), so this foot keeps only the actions.
   */
  othersAtTop?: boolean;
}) {
  // ⚠️ Its own hook, never props. The drawer captures its content once, when
  // it opens (`useMobileDrawer().open(node)`), so props from the shell would
  // stay as they were at that moment.
  const { accounts: switcher, reload } = useAccounts();
  const { data: session } = useSession();
  const accounts = withSessionFallback(switcher, you ? session : null);
  const [open, setOpen] = useState(false);
  const active = accounts.active;
  if ((!accounts.enabled && !you) || !active || !session?.user) return <>{fallback}</>;
  const count = accounts.others.length;

  if (actionsOnly) {
    return (
      <div className="rounded-lg border border-border">
        <AccountMenu
          accounts={accounts}
          onChanged={() => void reload(true)}
          showActive={false}
          showOthers={!othersAtTop}
          you={you}
          onNavigate={onNavigate}
        />
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border">
      <button
        onClick={() => {
          if (!open) void reload(true);
          setOpen((o) => !o);
        }}
        aria-expanded={open}
        aria-controls="drawer-accounts"
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left"
      >
        <Avatar email={active.email} name={active.name} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">{active.name ?? active.email}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {count > 0 && !othersAtTop ? `${active.email} · ${count} more` : active.email}
          </span>
        </span>
        <Icon
          name="ChevronDown"
          size={16}
          className={`shrink-0 text-muted-foreground tech-transition ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div id="drawer-accounts" className="border-t border-border">
          <AccountMenu
            accounts={accounts}
            onChanged={() => void reload(true)}
            showActive={false}
            showOthers={!othersAtTop}
            you={you}
            onNavigate={onNavigate}
          />
        </div>
      )}
    </div>
  );
}

/**
 * The phone menu's organization block, at its TOP (owner request, 2026-10-09).
 *
 * Before, switching organization on a phone took four steps: open the menu,
 * scroll to its foot, unfold the account row, tap. And nothing at the top said
 * which organization was open. Now the first thing under the menu's header
 * names the organization that is open and the account signed in, each with
 * its label. Every other account is one tap below it. So a switch is two taps:
 * Menu, then the organization.
 *
 * With the switcher off, it still names the organization and the account.
 */
export function DrawerOrgSwitch() {
  // Its own hooks, never props: the drawer captures its content once.
  const { accounts, reload } = useAccounts(true);
  const { access } = useAccess();
  const { data: session } = useSession();
  const { switching, error, pick } = useSwitchAccount(() => void reload(true));
  const orgName = access.organization?.display_name || access.organization?.slug || null;
  const email = accounts.active?.email ?? session?.user?.email ?? null;
  const name = accounts.active?.name ?? session?.user?.name ?? null;
  if (!orgName && !email) return null;
  const others = accounts.enabled ? accounts.others : [];

  return (
    <section aria-label="Organization and account" className="border-b border-border px-3 py-3" data-testid="drawer-org">
      <div className="flex items-center gap-3 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2.5">
        {email && <Avatar email={email} name={name} />}
        <div className="min-w-0 flex-1">
          {orgName && (
            <>
              <div className="text-xs text-muted-foreground">Organization</div>
              <div className="truncate text-sm font-semibold text-foreground">{orgName}</div>
            </>
          )}
          {email && (
            <div className="mt-0.5 truncate text-xs text-muted-foreground">
              Signed in as <span className="font-medium text-foreground">{email}</span>
            </div>
          )}
        </div>
        <Icon name="Check" size={15} className="shrink-0 text-primary" aria-label="Open now" />
      </div>

      {others.length > 0 && (
        <div className="mt-2">
          <div className="px-1 pb-1 text-xs font-medium text-muted-foreground">Switch to</div>
          <div role="list" aria-label="Switch organization" className="flex flex-col gap-1">
            {others.map((o) => (
              <div key={o.slot} role="listitem">
                <button
                  type="button"
                  onClick={() => void pick(o)}
                  className="flex w-full items-center gap-3 rounded-lg border border-border px-3 py-2.5 text-left hover:bg-secondary tech-transition"
                >
                  <Avatar email={o.email} name={o.name} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-foreground">
                      {o.organization ?? o.name ?? o.email}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">{o.email}</span>
                  </span>
                  <Icon name="ArrowRight" size={15} className="shrink-0 text-muted-foreground" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      {error && <p className="px-1 pt-2 text-xs text-destructive">{error}</p>}
      {switching && <SwitchingCover email={switching} />}
    </section>
  );
}

/**
 * The account tab, at the right end of the phone's bottom bar (owner request,
 * 2026-10-09: "a separate button for the account switcher, so that there's no
 * confusion"). It is on every screen, so the member always sees who they are:
 * the account's avatar, in that account's own hue, and the organization's
 * name under it. A "+N" says how many other accounts are one tap away. It
 * opens the account sheet (`onOpen`).
 */
export function AccountTab({ onOpen, open }: { onOpen: () => void; open: boolean }) {
  const { accounts } = useAccounts();
  const { access } = useAccess();
  const { data: session } = useSession();
  const orgName = access.organization?.display_name || access.organization?.slug || null;
  const email = accounts.active?.email ?? session?.user?.email ?? null;
  const name = accounts.active?.name ?? session?.user?.name ?? null;
  if (!email) return null;
  const count = accounts.enabled ? accounts.others.length : 0;
  const label = `Account: ${email}${orgName ? `, in ${orgName}` : ""}${count ? `, ${count} more` : ""}`;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={label}
      aria-haspopup="dialog"
      data-testid="account-tab"
      className={`flex flex-1 min-w-0 flex-col items-center gap-0.5 px-1 py-1 rounded-lg transition-colors ${
        open ? "text-primary" : "text-muted-foreground hover:text-foreground"
      }`}
    >
      <span className="relative">
        <Avatar email={email} name={name} size="xs" />
        {count > 0 && (
          <span className="absolute -bottom-1 -right-2 rounded-full border border-background bg-muted px-0.5 text-[8px] font-semibold leading-tight text-muted-foreground">
            +{count}
          </span>
        )}
      </span>
      <span className="max-w-full truncate text-[10px] font-medium leading-none">{orgName ?? "Account"}</span>
    </button>
  );
}
