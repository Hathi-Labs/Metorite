"use client";

/**
 * The account switcher (MT-1k slice A2, `saas_multitenancy.md`). The Gmail
 * model: every account this browser is signed in to, one tap apart.
 *
 * Three places:
 *   • `AccountFoot` — the desktop sidebar's foot with the shell bar on
 *     (owner, 2026-10-14). The avatar, the name and the email open ONE menu:
 *     the current account, the others, "Add another account", You, Settings
 *     and sign-out (`accountMenu` in `lib/shell/shellNav.ts`). Our comet and
 *     the word "Metorite" sign the menu's last row (owner, 2026-10-14). It
 *     replaced the 2026-10-12 shape, an account in the bar and a Metorite
 *     logo in the foot.
 *   • `SidebarAccountFooter` — the desktop sidebar's foot, in the classic
 *     frame only (shell bar off). Expanded, a button with the name and email.
 *     Folded, an avatar at the foot of the rail.
 *   • `DrawerAccountHeader` and `DrawerAccountFoot` — the phone drawer. The
 *     same list unfolds in place, so a phone needs no second overlay over the
 *     drawer. The phone did not change on 2026-10-12.
 *
 * `useAccounts()` reads `/api/accounts`. While the flag is off it answers
 * `enabled: false`, and the callers keep the footer they had.
 */

import Link from "next/link";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import ThemeToggle, { ThemeToggleMenuItem } from "@/components/ThemeToggle";
import { useAccess } from "@/components/AccessProvider";
import { MetoriteMark } from "@/components/MetoriteMark";
import { BrandMark, useOrgBranding } from "@/components/OrgBrandLockup";
import { categoricalAccent } from "@/lib/categorical";
import { useDismiss } from "@/hooks/useDismiss";
import {
  NO_ACCOUNTS,
  addAccount,
  fetchAccounts,
  removeAccount,
  signOutAll,
  signOutThis,
  switchTo,
  type Accounts,
  type OtherAccount,
} from "@/lib/accountSwitch";
import type { NavSection } from "@/lib/nav";
import { accountMenu, type AccountLink, type AccountMenuItem } from "@/lib/shell/shellNav";
import { CHANGE_LAYOUT } from "@/lib/shell/shellPrefs";

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
    let alive = true;
    void (async () => {
      // ⚠️ The plain read FIRST, then the names, in that order. The names
      // need one gateway call per account (up to 3 s), and rows that appear
      // only then push the menu down under a thumb that is already moving, so
      // a tap meant for the nav lands on "switch" (review of #765). The plain
      // read touches no gateway, so the rows take their place at once. In
      // sequence, so a late plain answer can never erase the names.
      const plain = await fetchAccounts(false);
      if (!alive) return;
      setAccounts(plain);
      if (!withOrgs || plain.others.length === 0) return;
      const full = await fetchAccounts(true);
      if (alive) setAccounts(full);
    })();
    return () => {
      alive = false;
    };
  }, [withOrgs]);
  return { accounts, reload };
}

/**
 * One tap to another account: the cover while the page reloads, and the
 * error when the account is no longer signed in. The menu and the phone's
 * organization block share it, so the two switch the same way.
 */
function useSwitchAccount(onChanged: () => void, onCover?: (cover: Cover | null) => void) {
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pick = async (o: OtherAccount) => {
    setError(null);
    setSwitching(o.email);
    onCover?.({ email: o.email });
    if (!(await switchTo(o.slot))) {
      setSwitching(null);
      onCover?.(null);
      setError(`${o.email} is no longer signed in here. Add it again.`);
      onChanged();
    }
  };
  return { switching, error, pick };
}

/** What the cover says: a switch to an email, or a sentence of its own. */
type Cover = { email: string | null; message?: string };

function initial(name: string | null | undefined, email: string): string {
  return (name?.trim() || email).charAt(0).toUpperCase();
}

/** A round initial, in the account's own stable hue, as Gmail does. */
function Avatar({ email, name, size = "md" }: { email: string; name?: string | null; size?: "xs" | "sm" | "md" | "lg" }) {
  const box =
    size === "lg"
      ? "h-10 w-10 text-base"
      : size === "sm"
        ? "h-7 w-7 text-xs"
        : size === "xs"
          ? "h-6 w-6 text-[11px]"
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

/**
 * "Switching to …" over the whole page, until the reload lands. With no
 * email, it covers the read of the accounts that an org-aware link makes
 * first (`components/OrgLinkGate.tsx`). `message` says something else, such
 * as "Signing out…".
 *
 * ⚠️ **It is a `Modal` that cannot be dismissed** (review of 2026-10-12).
 * The page under it belongs to an account the cookie may no longer name, so
 * nothing on it may take a key or a click while the page leaves. `Modal`
 * traps the focus, takes it, and marks the page under it. `onClose` does
 * nothing, so Escape and an outside press leave it up. `data-switching-cover`
 * tells `useDismiss` to leave every menu open meanwhile, so no menu can
 * unmount the cover it holds. This is the one cover for every switch and
 * sign-out. Fences: `shellAccount.test.ts` and `e2e/shell-account.spec.ts`.
 */
export function SwitchingCover({ email, message }: { email: string | null; message?: string }) {
  const text = message ?? (email ? `Switching to ${email}` : "Opening the link…");
  return (
    <Modal open onClose={NO_DISMISS} label={text} showClose={false} layer="alert" size="sm">
      <div data-switching-cover="" role="status" aria-live="polite" className="flex flex-col items-center gap-3 px-6 py-8">
        <Icon name="Loader2" size={22} className="animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          {message ? (
            message
          ) : email ? (
            <>
              Switching to <span className="font-medium text-foreground">{email}</span>
            </>
          ) : (
            "Opening the link…"
          )}
        </p>
      </div>
    </Modal>
  );
}

/** The cover's `onClose`: it stays up until the page leaves. */
const NO_DISMISS = () => {};

/**
 * The menu body. The active account first, with its organization, then the
 * others, then the two actions.
 */
function AccountMenu({
  accounts,
  onChanged,
  showActive = true,
  you,
  onNavigate,
}: {
  accounts: Accounts;
  onChanged: () => void;
  /** False in the phone drawer, whose row above already shows it. */
  showActive?: boolean;
  /**
   * The shell nav's account rows (NS-2): My Profile, My access and
   * Appearance. Organisation is an app in Admin since 2026-10-09, so it is
   * not here. Absent, the menu is the switcher alone.
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
          {/* NS-7: ask the first sign-in question again (§8.4 rule 3). */}
          <Button
            variant="ghost"
            size="none"
            layout="flex items-center"
            icon="LayoutTemplate"
            onClick={() => {
              onNavigate?.();
              window.dispatchEvent(new CustomEvent(CHANGE_LAYOUT));
            }}
            className="w-full gap-3 px-3 py-2 text-[13px]"
          >
            Change my layout
          </Button>
        </nav>
      )}

      {accounts.others.length > 0 && (
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

/** Where the foot's menu sits, from the trigger's box and the window's height. */
export type FootBox = { left: number; bottom: number; maxHeight: number };

/**
 * The menu's BOTTOM edge sits 6px above the trigger, and its left edge on the
 * trigger's. Its height is capped by the room ABOVE the trigger, less 8px, so
 * its top never leaves the window (review of 2026-10-14, P1). A cap of the
 * whole window height ignored `bottom`: with three accounts at 1280x600 the
 * menu's top sat at y=-36, and the header could not scroll back into view.
 * The menu scrolls inside itself. Fence: `shellAccount.test.ts` and
 * `e2e/shell-account.spec.ts` "a short window".
 */
export function footBox(rect: { left: number; top: number }, innerHeight: number): FootBox {
  const bottom = innerHeight - rect.top + 6;
  return { left: Math.max(8, rect.left), bottom, maxHeight: Math.max(0, innerHeight - bottom - 8) };
}

/**
 * The sidebar's foot with the shell bar on (owner, 2026-10-14). The owner
 * said: "move the account selection and the UI element that shows the
 * current account back to the bottom left where it was before. Combine it
 * with the pop-up settings … Remove the Metorite logo."
 *
 * - **Open rail.** The avatar, then the member's name, and the email in
 *   muted text under it. Both truncate. The tip holds the full name, the
 *   email and the organization.
 * - **Folded rail.** The avatar alone, in the geometry of a folded nav row
 *   (`p-2` block, centred), so it sits on the rail's icon column.
 *   `e2e/shell-account.spec.ts` measures that within 1px.
 * - **Focus.** `cc-link` gives it the house focus ring, as All apps wears it.
 * - **The menu** opens upward from the foot. Its rows come from ONE list,
 *   `accountMenu` in `lib/shell/shellNav.ts`, so a new option is one line
 *   there. `AccountFootMenu` draws each kind.
 *
 * ⚠️ **The cover lives HERE, not in the menu** (review of 2026-10-12), so
 * closing the menu can never take it down. While it is up, the menu ignores
 * Escape and an outside press (`useDismiss`). Escape gives the focus back to
 * the trigger. Fences: `shellAccount.test.ts` and `e2e/shell-account.spec.ts`.
 *
 * The classic frame (shell bar off) keeps `SidebarAccountFooter`. The phone
 * has no sidebar, so this never shows there.
 */
export function AccountFoot({
  collapsed,
  sections,
  layout,
}: {
  collapsed: boolean;
  /**
   * The member's `visibleSections`, the input of the You and Settings rows.
   * The sidebar passes none with the shell nav off, because its own list
   * then holds My Profile and Appearance already.
   */
  sections: readonly NavSection[];
  /** "Change my layout" shows only while the shell nav is on (NS-7). */
  layout: boolean;
}) {
  const { accounts: switcher, reload } = useAccounts();
  const { data: session } = useSession();
  const { access } = useAccess();
  // With the switcher off, the session names the account. Access answers
  // first in a fresh tab, so it stands in until the session does.
  const fallback = session?.user?.email ? session : access.email ? { user: { email: access.email } } : null;
  const accounts = withSessionFallback(switcher, fallback);
  const orgName = access.organization?.display_name?.trim() || access.organization?.slug?.trim() || "";
  const [open, setOpen] = useState(false);
  // Where the menu sits: its BOTTOM edge just above the button, its left
  // edge on the button's. ⚠️ Not AnchoredPanel, which places a flipped panel
  // by its top edge from its MAXIMUM height. A short menu then floated about
  // 200px above the button it belongs to.
  const [box, setBox] = useState<FootBox | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  // Plain functions, so `useDismiss` binds its two listeners again on each
  // render while the menu is open, which costs nothing here. A `useCallback`
  // tripped the lint rule react-hooks/preserve-manual-memoization.
  const close = () => setOpen(false);
  // Escape gives the focus back to the trigger, as a menu button does.
  const escape = () => {
    setOpen(false);
    rootRef.current?.querySelector<HTMLButtonElement>("button[data-account-trigger]")?.focus();
  };
  // The cover lives HERE, not in the menu, so closing the menu can never
  // take it down (review of 2026-10-12). While it is up, the menu ignores
  // Escape and an outside press.
  const [cover, setCover] = useState<Cover | null>(null);
  useDismiss(open && !cover, close, rootRef, escape);
  // A resize while the menu is open moves the room above the trigger, so
  // the place and the cap are measured again.
  useEffect(() => {
    if (!open) return;
    const onResize = () => {
      const r = rootRef.current?.getBoundingClientRect();
      if (r) setBox(footBox(r, window.innerHeight));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open]);
  const active = accounts.active;
  const block = `border-t border-sidebar-border ${collapsed ? "flex flex-col p-2" : "px-2 py-2"}`;
  // The row's place, held while nobody is known yet, so nothing moves.
  if (!active) {
    return (
      <div data-account-foot="" className={block}>
        <span aria-hidden className={collapsed ? "mx-auto block h-10 w-10" : "block h-10"} />
      </div>
    );
  }

  const name = active.name?.trim() || null;
  const tip = [name, active.email, orgName].filter(Boolean).join("\n");
  const toggle = () => {
    if (!open) {
      void reload(true);
      const r = rootRef.current?.getBoundingClientRect();
      if (r) setBox(footBox(r, window.innerHeight));
    }
    setOpen((o) => !o);
  };

  return (
    <div data-account-foot="" className={block}>
      <div ref={rootRef} className={collapsed ? "flex flex-col" : ""}>
        <button
          type="button"
          data-account-trigger=""
          onClick={toggle}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`Account and settings: ${active.email}`}
          title={tip}
          className={`cc-link group flex items-center rounded-lg text-left hover:bg-sidebar-accent tech-transition ${
            collapsed ? "justify-center p-1.5" : "w-full gap-2.5 px-2 py-1.5"
          }`}
        >
          <Avatar email={active.email} name={active.name} size="sm" />
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-sidebar-foreground/90">
                  {name ?? active.email}
                </span>
                {name && (
                  // On hover, the muted grey on `sidebar-accent` drops to 4.23:1 in
                  // light mode (measured 2026-10-14), so the email lifts to 70%
                  // foreground there. At rest it is 4.56:1, and 7.06:1 in dark.
                  <span data-account-email="" className="block truncate text-[11px] text-muted-foreground group-hover:text-foreground/70">
                    {active.email}
                  </span>
                )}
              </span>
              <Icon name="ChevronsUpDown" size={13} className="shrink-0 text-muted-foreground" />
            </>
          )}
        </button>
        {/* `position: fixed` inside the rail, so the rail's overflow does not
            clip it, and a click inside it is inside `rootRef`. */}
        {open && box && (
          <div
            role="dialog"
            aria-label="Account and settings"
            // The cap is the room above the trigger (`footBox`), never the
            // window. The header is not sticky: in a short window it would
            // take the room the rows need. The menu opens at its top, so the
            // header shows first, and the sign-out rows are a scroll away.
            style={{ left: box.left, bottom: box.bottom, maxHeight: box.maxHeight }}
            className="fixed z-[60] w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto overscroll-contain rounded-md border border-border bg-card shadow-md"
          >
            <AccountFootMenu
              accounts={accounts}
              orgName={orgName}
              sections={sections}
              layout={layout}
              onChanged={() => void reload(true)}
              onCover={setCover}
              onDone={close}
            />
          </div>
        )}
        {cover && <SwitchingCover email={cover.email} message={cover.message} />}
      </div>
    </div>
  );
}

/** What each `action` item sends. One line per action id. */
const ACTIONS: Record<Extract<AccountMenuItem, { kind: "action" }>["id"], () => void> = {
  "change-layout": () => window.dispatchEvent(new CustomEvent(CHANGE_LAYOUT)),
};

/** One rhythm for every row of the menu, a link or a button. */
const ROW = "w-full gap-3 px-3 py-2 text-[13px]";

function rowKey(item: AccountMenuItem): string {
  return item.kind === "link" ? item.href : item.kind === "action" ? item.id : item.kind;
}

/**
 * The foot menu's body, in the groups of `accountMenu`: the current account,
 * marked, then the other accounts and "Add another account", then You, then
 * Settings, then sign-out, then our signature.
 *
 * Sign-out follows the count. One account signs out with "Sign out". With
 * more, "Sign out" leaves this account and lands on the next one
 * (`signOutThis`), and "Sign out of all accounts" ends them all. With the
 * switcher flag off, the menu shows the current account, You, Settings and
 * "Sign out". Exported for `shellAccount.test.ts`. Only `AccountFoot` mounts it.
 */
export function AccountFootMenu({
  accounts,
  orgName,
  sections,
  layout,
  onChanged,
  onCover,
  onDone,
  pages = true,
}: {
  accounts: Accounts;
  orgName: string;
  sections: readonly NavSection[];
  layout: boolean;
  /** False drops You and Settings, for a member with no organization. */
  pages?: boolean;
  onChanged: () => void;
  /**
   * Raises and lowers the cover, which `AccountFoot` holds, outside this menu.
   * Absent (a test render), the menu holds its own.
   */
  onCover?: (cover: Cover | null) => void;
  /** Closes the menu after a link or an action. */
  onDone?: () => void;
}) {
  const [ownCover, setOwnCover] = useState<Cover | null>(null);
  const setCover = onCover ?? setOwnCover;
  const { error, pick } = useSwitchAccount(onChanged, setCover);
  const [leaving, setLeaving] = useState(false);
  const active = accounts.active;
  if (!active) return null;
  const others = accounts.enabled ? accounts.others.filter((o) => o.email.toLowerCase() !== active.email.toLowerCase()) : [];
  const done = () => onDone?.();

  // The cover stays up while the page leaves, so this tab cannot act as
  // either account in between. Each good path ends in a navigation, so the
  // cover stays. Only a throw keeps this tab here, and then it comes down.
  const signOut = async () => {
    setLeaving(true);
    setCover({ email: null, message: "Signing out…" });
    try {
      if (others.length === 0) await signOutAll();
      else if ((await signOutThis(active.email, others[0])) === "all") onChanged();
    } catch {
      setLeaving(false);
      setCover(null);
      onChanged();
    }
  };

  const groups = accountMenu(sections, { layout, switcher: accounts.enabled, others: others.length, pages });

  const row = (item: AccountMenuItem) => {
    switch (item.kind) {
      case "current":
        // The account that is loaded now. The check and `aria-current` say
        // so, for the eye and for a screen reader.
        return (
          <div data-current-account="" aria-current="true" className="flex items-center gap-3 px-4 pb-3 pt-4">
            <Avatar email={active.email} name={active.name} size="lg" />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium text-foreground">{active.name ?? active.email}</div>
              {active.name && <div className="truncate text-xs text-muted-foreground">{active.email}</div>}
              {orgName && (
                <div className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground">
                  <Icon name="Building2" size={11} className="shrink-0" />
                  {orgName}
                </div>
              )}
            </div>
            <span className="flex shrink-0 items-center gap-1 text-[11px] font-medium text-primary">
              <Icon name="Check" size={13} />
              Signed in
            </span>
          </div>
        );
      case "others":
        return (
          <>
            <div aria-hidden className="px-3 pb-1 pt-1.5 text-[11px] text-muted-foreground">
              Switch account
            </div>
            <div role="list" aria-label="Other accounts">
              {others.map((o) => (
                <div key={o.slot} role="listitem" className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => void pick(o)}
                    className="flex min-w-0 flex-1 items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-secondary tech-transition"
                  >
                    <Avatar email={o.email} name={o.name} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-foreground">{o.name ?? o.email}</span>
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
                    // Always shown. A hover-only control never appears on
                    // touch (`revealOnHover.test.ts`).
                    className="mr-1 text-muted-foreground"
                  />
                </div>
              ))}
            </div>
          </>
        );
      case "add-account":
        return (
          <Button variant="ghost" size="none" layout="flex items-center" icon="UserPlus" onClick={() => void addAccount()} className={ROW}>
            Add another account
          </Button>
        );
      case "link":
        return (
          <Link
            href={item.href}
            onClick={done}
            className={`flex items-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground tech-transition ${ROW}`}
          >
            <Icon name={item.icon} size={16} className="shrink-0" />
            {item.label}
          </Link>
        );
      case "colour-mode":
        // It keeps the menu open, so the change shows.
        return <ThemeToggleMenuItem className={ROW} />;
      case "action":
        return (
          <Button
            variant="ghost"
            size="none"
            layout="flex items-center"
            onClick={() => {
              done();
              ACTIONS[item.id]();
            }}
            className={ROW}
          >
            <Icon name={item.icon} size={16} className="shrink-0" />
            {item.label}
          </Button>
        );
      case "sign-out":
        return (
          <Button variant="ghost" size="none" layout="flex items-center" icon="LogOut" loading={leaving} onClick={() => void signOut()} className={ROW}>
            Sign out
          </Button>
        );
      case "sign-out-all":
        return (
          <Button variant="ghost" size="none" layout="flex items-center" icon="LogOut" onClick={() => void signOutAll()} className={ROW}>
            Sign out of all accounts
          </Button>
        );
      case "brand":
        // Our signature (owner, 2026-10-14), the way Notion, Slack and Linear
        // sign their app menus. It is text, not a control: no button, no link,
        // no hover fill and no focus stop. The comet is decorative, so a
        // screen reader reads "Metorite" once, from the word.
        return (
          <div data-account-brand="" className="flex items-center gap-2 px-3 py-1.5 text-[11px] text-muted-foreground">
            <MetoriteMark size={14} />
            <span>Metorite</span>
          </div>
        );
    }
  };

  return (
    <div className="text-sm">
      {groups.map((g, i) => (
        <div
          key={g.id}
          data-account-group={g.id}
          role={g.label ? "group" : undefined}
          aria-label={g.label ?? undefined}
          className={`${i > 0 ? "border-t border-border" : ""} ${g.id === "current" ? "" : "p-1"}`}
        >
          {g.label && (
            <div aria-hidden className="px-3 pb-1 pt-1.5 text-[11px] text-muted-foreground">
              {g.label}
            </div>
          )}
          {g.items.map((item) => (
            <Fragment key={rowKey(item)}>{row(item)}</Fragment>
          ))}
          {/* A switch that failed says so under the accounts. */}
          {g.id === "accounts" && error && <p className="px-3 pb-2 pt-1 text-xs text-destructive">{error}</p>}
        </div>
      ))}
      {/* Only a test render, with no `AccountFoot` above, draws it here. */}
      {!onCover && ownCover && <SwitchingCover email={ownCover.email} message={ownCover.message} />}
    </div>
  );
}

/**
 * The account acts for a member with NO organization (review of 2026-10-14,
 * P1). `AccessGate` shows them in its org-less card, on every frame.
 *
 * The sidebar does not mount for an org-less member (`Sidebar.tsx`, the
 * `canPoll` gate), so the foot cannot carry them. Before 2026-10-14 the bar's
 * account widget did. Without this block, an address with no organization,
 * reached by "Add another account" for one, had no way back to a held
 * account and no sign-out.
 *
 * It draws `AccountFootMenu` in place, with `pages: false`: the current
 * account, the other accounts, "Add another account" and sign-out. Every
 * rule of the foot holds. Sign-out fails closed, and the cover lives HERE,
 * outside the menu. Nothing dismisses an in-place menu, so it needs no
 * `useDismiss`. Fences: `shellAccount.test.ts` and `e2e/shell-account.spec.ts`
 * "no organization".
 */
export function OrglessAccounts() {
  const { accounts: switcher, reload } = useAccounts(true);
  const { data: session } = useSession();
  const { access } = useAccess();
  const fallback = session?.user?.email ? session : access.email ? { user: { email: access.email } } : null;
  const accounts = withSessionFallback(switcher, fallback);
  const [cover, setCover] = useState<Cover | null>(null);
  if (!accounts.active) return null;
  return (
    <section data-orgless-accounts="" aria-label="Your accounts" className="mt-6 overflow-hidden rounded-lg border border-border bg-card text-left">
      <AccountFootMenu
        accounts={accounts}
        orgName=""
        sections={[]}
        layout={false}
        pages={false}
        onChanged={() => void reload(true)}
        onCover={setCover}
      />
      {cover && <SwitchingCover email={cover.email} message={cover.message} />}
    </section>
  );
}

/**
 * The phone menu's header, and its ONE account surface (owner, 2026-10-09).
 *
 * Collapsed, it is the brand mark that was always here, the organization that
 * is open (the mark's caption), and under it the address that is signed in.
 * One line of account, so the search and the apps move up. A "+N" and a
 * chevron say that more accounts wait behind it.
 *
 * Tapped, it unfolds: each other account as a one-tap switch with its own ✕,
 * and "Add another account". Nothing else. The member's pages and sign-out
 * are rare acts, so they sit at the menu's foot (`DrawerAccountFoot`), where
 * Gmail and Slack put them. The apps, the frequent act, keep the space between.
 * (Owner review of the phone menu, 2026-10-09: "very crowded".)
 *
 * Why not a card. An earlier version put a large "Organization · Signed in
 * as" card and an open "Switch to" list under the header. It said the
 * organization twice, once in the mark and once in the card, and it pushed
 * the apps off the first screen. The owner asked for the switcher behind the
 * logo instead.
 *
 * The mark no longer links Home, because a tap now opens the accounts. Home
 * is the first item of the shell nav, below.
 */
export function DrawerAccountHeader({ onClose }: { onClose: () => void }) {
  // Its own hooks, never props: the drawer captures its content once.
  const { accounts, reload } = useAccounts(true);
  const { access } = useAccess();
  const { data: session } = useSession();
  const branding = useOrgBranding();
  const { switching, error, pick } = useSwitchAccount(() => void reload(true));
  const [open, setOpen] = useState(false);
  // The organization and the address from ONE answer, `/api/auth/me`, so they
  // can never belong to two different accounts (review of #765).
  const orgName = access.organization?.display_name?.trim() || access.organization?.slug?.trim() || "";
  const email = access.email || session?.user?.email || "";
  const others = accounts.enabled ? accounts.others.filter((o) => o.email !== email) : [];
  const label = [
    "Account",
    email && `signed in as ${email}`,
    orgName && `in ${orgName}`,
    others.length > 0 && `${others.length} more`,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="border-b border-border px-3 py-2" data-testid="drawer-org">
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls="drawer-accounts"
          aria-label={label}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1.5 text-left hover:bg-secondary tech-transition"
        >
          <span className="min-w-0 flex-1">
            <BrandMark branding={branding} fallbackCaption="Home" orgName={orgName} maxWidth={180} />
            {/* Under the organization's name. Our square comet puts the name
                beside it, so the address takes the same indent. A customer's
                logo stands alone with no name beside it, so the address
                stays at its left edge. */}
            {email && (
              <span className={`mt-1 block truncate text-xs text-muted-foreground ${branding?.logo ? "" : "pl-[38px]"}`}>
                {email}
              </span>
            )}
          </span>
          {others.length > 0 && (
            <span className="shrink-0 rounded-full border border-border px-1.5 text-[10px] font-medium text-muted-foreground">
              +{others.length}
            </span>
          )}
          <Icon
            name="ChevronDown"
            size={16}
            className={`shrink-0 text-muted-foreground tech-transition ${open ? "rotate-180" : ""}`}
          />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-secondary"
          aria-label="Close"
        >
          <Icon name="X" size={16} />
        </button>
      </div>

      {open && (
        <div id="drawer-accounts" className="mt-2 flex flex-col gap-1 pb-1">
          {others.length > 0 && (
            <div role="list" aria-label="Switch organization" className="flex flex-col gap-1">
              {others.map((o) => (
                <div key={o.slot} role="listitem" className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => void pick(o)}
                    className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-secondary tech-transition"
                  >
                    <Avatar email={o.email} name={o.name} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">
                        {o.organization ?? o.name ?? o.email}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">{o.email}</span>
                    </span>
                  </button>
                  {/* The one place on a phone to drop ONE account from this
                      browser. Always shown: a hover-only control never
                      appears on touch. */}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    icon="X"
                    aria-label={`Remove ${o.email} from this browser`}
                    title="Remove from this browser"
                    onClick={async () => {
                      await removeAccount(o.slot);
                      void reload(true);
                    }}
                    className="shrink-0 text-muted-foreground"
                  />
                </div>
              ))}
            </div>
          )}
          {error && <p className="px-2 text-xs text-destructive">{error}</p>}
          {accounts.enabled && (
            <button
              type="button"
              onClick={() => void addAccount()}
              className="flex items-center gap-3 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground hover:bg-secondary hover:text-foreground tech-transition"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-dashed border-border">
                <Icon name="Plus" size={14} />
              </span>
              Add another account
            </button>
          )}
        </div>
      )}
      {switching && <SwitchingCover email={switching} />}
    </div>
  );
}

/**
 * The phone menu's foot: the member's pages and the settings, then sign-out,
 * in one list under one heading. These are rare acts, so they come after the
 * apps (owner review, 2026-10-09). `children` is the shell's own rows, Dark
 * mode and Desktop view, so all of them share one place and one look.
 */
export function DrawerAccountFoot({
  you,
  onNavigate,
  children,
}: {
  /** The shell nav's account rows: My Profile, My access and Appearance. */
  you?: readonly AccountLink[];
  onNavigate?: () => void;
  children?: React.ReactNode;
}) {
  const { accounts } = useAccounts();
  const { data: session } = useSession();
  if (!session?.user) return <>{children}</>;
  const others = accounts.enabled ? accounts.others.length : 0;
  const row =
    "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-muted-foreground hover:bg-secondary hover:text-foreground tech-transition";
  return (
    <section aria-label="Account and settings" className="flex flex-col gap-0.5">
      <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Account and settings
      </div>
      {you?.map((l) => (
        <Link key={l.href} href={l.href} onClick={onNavigate} className={row}>
          <Icon name={l.icon} size={16} className="shrink-0" />
          {l.label}
        </Link>
      ))}
      {/* NS-7: the shell nav's rows carry "Change my layout" (§8.4 rule 3). */}
      {you && (
        <button
          type="button"
          onClick={() => {
            onNavigate?.();
            window.dispatchEvent(new CustomEvent(CHANGE_LAYOUT));
          }}
          className={row}
        >
          <Icon name="LayoutTemplate" size={16} className="shrink-0" />
          Change my layout
        </button>
      )}
      {children}
      <button type="button" onClick={() => void signOutAll()} className={row}>
        <Icon name="LogOut" size={16} className="shrink-0" />
        {others > 0 ? "Sign out of all accounts" : "Sign out"}
      </button>
    </section>
  );
}
