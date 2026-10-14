/**
 * The account at the sidebar's foot, with one menu for the accounts, the
 * member's pages and the settings (owner, 2026-10-14).
 *
 * The owner moved the account back from the bar's top right to the bottom
 * left, folded the Metorite menu into it, and removed the Metorite logo.
 * These checks fail on the wrong builds that look right:
 *   1. An account control came back to the bar.
 *   2. Our comet came back to the bar or to the foot.
 *   3. The foot lost its name, its tip, its avatar or its quiet email.
 *   4. The menu lost the current account's mark, the switch list, "Add
 *      another account", You, Settings, or the sign-out that fits the count.
 *   5. Sign-out failed open, or the cover moved inside the menu.
 *   6. The classic frame (shell bar off) lost the foot it had.
 *   7. The menu grew a second list beside `accountMenu`.
 * The geometry in a browser is `e2e/shell-account.spec.ts`. The list's own
 * order is `shellNav.test.ts`.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";

vi.mock("@/components/AccessProvider", () => ({
  useAccess: () => ({
    access: {
      ...NO_ACCESS,
      authenticated: true,
      email: "asha@example.com",
      organization: { id: "org1", slug: "acme", display_name: "Acme" },
    },
    loading: false,
    stale: false,
    refresh: async () => {},
  }),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { name: "Asha Rao", email: "asha@example.com" } } }),
  signOut: vi.fn(async () => undefined),
}));

import { AccountFoot, AccountFootMenu } from "@/components/AccountSwitcher";
import { PANES, visibleSections } from "@/lib/nav";
import type { Accounts } from "@/lib/accountSwitch";
import { ShellBarRow } from "./ShellBar";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
/** Source without comments, so a comment cannot satisfy or break a check. */
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
const SWITCHER = () => code(read("components/AccountSwitcher.tsx"));
/** The source of the foot and its menu, and nothing of the phone's. */
const footSource = () => {
  const src = SWITCHER();
  return src.slice(src.indexOf("export function AccountFoot("), src.indexOf("export function DrawerAccountHeader"));
};
const SECTIONS = visibleSections(PANES.map((p) => p.feature).filter((f): f is string => !!f), false);

describe("the bar holds no account", () => {
  it("ShellBar names no account control", () => {
    expect(code(read("lib/shell/ShellBar.tsx"))).not.toMatch(
      /AccountSwitcher|AccountFoot|BarAccount|useAccounts|data-shell-account|account=\{/,
    );
  });

  it("the row ends with the activity control", () => {
    const html = renderToStaticMarkup(
      createElement(ShellBarRow, { here: null, onOpen: () => {}, activity: createElement("i", { "data-zone": "activity" }) }),
    );
    expect(html.endsWith('<i data-zone="activity"></i></div></header>')).toBe(true);
  });

  it("never spills over the command bar: the right group keeps its content's width", () => {
    // With `min-w-0` the group is a `basis-0` share that its content can
    // overflow, and a wide control put the bell over the command bar at 1024px.
    const row = renderToStaticMarkup(createElement(ShellBarRow, { here: null, onOpen: () => {} }));
    const group = /<div class="([^"]*justify-end[^"]*)">/.exec(row)?.[1] ?? "";
    expect(group.split(" ")).toEqual(expect.arrayContaining(["flex-1", "basis-0", "justify-end"]));
    expect(group.split(" ")).not.toContain("min-w-0");
  });

  it("our comet signs neither the bar nor the foot", () => {
    for (const rel of ["lib/shell/ShellBar.tsx", "components/AccountSwitcher.tsx", "components/Sidebar.tsx", "lib/orgBranding.ts"]) {
      expect(code(read(rel)), rel).not.toMatch(/BarSignature|data-shell-signature|MetoriteMark|MetoriteFoot|data-metorite|ourMarkLeads/);
    }
    // The Metorite foot is gone, and its file with it.
    expect(existsSync(join(SRC, "components/MetoriteFoot.tsx"))).toBe(false);
  });
});

describe("the foot is the account row", () => {
  const open = renderToStaticMarkup(createElement(AccountFoot, { collapsed: false, sections: SECTIONS, layout: true }));
  const folded = renderToStaticMarkup(createElement(AccountFoot, { collapsed: true, sections: SECTIONS, layout: true }));

  it("is one button, named by the email, with the name, email and organization as its tip", () => {
    for (const html of [open, folded]) {
      expect(html.match(/<button\b/g) ?? []).toHaveLength(1);
      expect(html).toContain('aria-label="Account and settings: asha@example.com"');
      expect(html).toContain('title="Asha Rao\nasha@example.com\nAcme"');
      expect(html).toContain('aria-haspopup="dialog"');
      // The house focus ring, as All apps wears it.
      expect(html).toMatch(/<button[^>]*class="cc-link /);
      // The avatar, never our comet.
      expect(html).toMatch(/rounded-full[^"]*">A<\/span>/);
      expect(html).not.toMatch(/<svg[^>]*data-metorite-mark/);
    }
    expect(read("app/globals.css")).toMatch(/\.cc-control:focus-visible,\s*\.cc-link:focus-visible\s*\{/);
  });

  it("open: the avatar, the name, and the email muted under it, both truncated", () => {
    expect(text(open)).toBe("A Asha Rao asha@example.com");
    expect(open).toMatch(/<span class="[^"]*\btruncate\b[^"]*text-\[13px\] font-medium[^"]*">Asha Rao<\/span>/);
    const email = /<span data-account-email="" class="([^"]*)">asha@example.com<\/span>/.exec(open)?.[1] ?? "";
    expect(email.split(" ")).toEqual(expect.arrayContaining(["block", "truncate", "text-muted-foreground"]));
  });

  it("folded: the avatar alone, in the geometry of a folded nav row", () => {
    expect(text(folded)).toBe("A");
    expect(folded).toMatch(/data-account-foot="" class="[^"]*\bp-2\b/);
    expect(folded).toMatch(/<button[^>]*class="[^"]*justify-center p-1\.5/);
  });
});

describe("the foot's menu: the accounts, You, Settings, then sign-out", () => {
  const menu = (accounts: Accounts, sections = SECTIONS, layout = true) =>
    renderToStaticMarkup(createElement(AccountFootMenu, { accounts, orgName: "Acme", sections, layout, onChanged: () => {} }));
  const ACTIVE = { email: "asha@example.com", name: "Asha Rao" };
  const MANY: Accounts = {
    enabled: true,
    active: ACTIVE,
    others: [
      { slot: 2, email: "asha@fracktal.in", name: "Asha R", organization: "Fracktal Works" },
      { slot: 0, email: "ops@acme.com", name: null, organization: null },
    ],
  };
  const groups = (html: string) => [...html.matchAll(/data-account-group="([^"]+)"/g)].map((m) => m[1]);

  it("marks the current account, with its organization", () => {
    const html = menu(MANY);
    expect(html).toMatch(/data-current-account="" aria-current="true"/);
    const head = html.slice(html.indexOf("data-current-account"), html.indexOf("Switch account"));
    expect(text(head)).toContain("Asha Rao asha@example.com Acme Signed in");
  });

  it("with three accounts: every group, in order, and both sign-outs last", () => {
    const html = menu(MANY);
    expect(groups(html)).toEqual(["current", "accounts", "you", "settings", "sign-out"]);
    expect(html.match(/role="listitem"/g) ?? []).toHaveLength(2);
    expect(text(html)).toContain("Fracktal Works · asha@fracktal.in");
    // Each other account keeps its own Remove.
    expect(html.match(/aria-label="Remove [^"]+ from this browser"/g) ?? []).toHaveLength(2);
    expect(text(html)).toMatch(
      /ops@acme\.com Add another account You My Profile My access Settings Appearance Colour mode Change my layout Sign out Sign out of all accounts$/,
    );
    expect(html).toMatch(/role="group" aria-label="You"/);
    expect(html).toMatch(/role="group" aria-label="Settings"/);
  });

  it("with one account: Add, You, Settings and one Sign out", () => {
    const t = text(menu({ enabled: true, active: ACTIVE, others: [] }));
    expect(t).not.toContain("Switch account");
    expect(t).toMatch(/Add another account You My Profile My access Settings .* Sign out$/);
    expect(t).not.toContain("Sign out of all accounts");
  });

  it("with the switcher flag off: the account, You, Settings and Sign out, and no account act", () => {
    const html = menu({ enabled: false, active: ACTIVE, others: [] });
    expect(groups(html)).toEqual(["current", "you", "settings", "sign-out"]);
    expect(text(html)).toBe(
      "A Asha Rao asha@example.com Acme Signed in You My Profile My access Settings Appearance Colour mode Change my layout Sign out",
    );
  });

  it("with the shell nav off: no You row, and Settings is the colour mode alone", () => {
    // The classic rail lists My Profile and Appearance itself, so the foot
    // gets no sections then (#918's rule, kept).
    const html = menu(MANY, [], false);
    expect(groups(html)).toEqual(["current", "accounts", "settings", "sign-out"]);
    expect(html).not.toContain("<a ");
    expect(text(html)).toMatch(/Settings Colour mode Sign out Sign out of all accounts$/);
    const sidebar = code(read("components/Sidebar.tsx"));
    expect(sidebar).toMatch(/<AccountFoot [^>]*sections=\{shellNav \? sections : \[\]\}[^>]*layout=\{shellNav\}/);
  });

  it("draws its rows from accountMenu alone", () => {
    const foot = footSource();
    expect(foot).toMatch(/accountMenu\(sections, \{ layout, switcher: accounts\.enabled, others: others\.length \}\)/);
    expect(foot).not.toMatch(/accountLinks|MY_ACCESS|"\/people\/me"|"\/settings\/appearance"|Change my layout/);
  });

  // ⚠️ Fails CLOSED (review of 2026-10-12, P1). Moved, not rewritten.
  it("signs this account out with signOutThis, and all of them with signOutAll", () => {
    const body = footSource();
    expect(body).toMatch(/if \(others\.length === 0\) await signOutAll\(\);/);
    // The cover goes up as the sign-out starts (review of 2026-10-12, P2).
    expect(body).toMatch(/setLeaving\(true\);\s*setCover\(\{ email: null, message: "Signing out…" \}\);/);
    expect(body).toMatch(/else if \(\(await signOutThis\(active\.email, others\[0\]\)\) === "all"\) onChanged\(\);/);
    expect(body).toMatch(/case "sign-out":[\s\S]*?onClick=\{\(\) => void signOut\(\)\}/);
    expect(body).toMatch(/case "sign-out-all":[\s\S]*?onClick=\{\(\) => void signOutAll\(\)\}/);
    expect(body).toMatch(/onClick=\{\(\) => void pick\(o\)\}/);
  });

  // Review of 2026-10-12, P2: Escape closed the menu, and the cover was its
  // child, so the cover came down mid-sign-out.
  it("the cover lives in AccountFoot, outside the menu, and the menu stays open under it", () => {
    const src = SWITCHER();
    const foot = src.slice(src.indexOf("export function AccountFoot("), src.indexOf("export function AccountFootMenu"));
    expect(foot).toMatch(/useDismiss\(open && !cover, close, rootRef, escape\);/);
    expect(foot).toMatch(/onCover=\{setCover\}/);
    // After the menu's closing `)}`, so the menu's unmount cannot take it.
    expect(foot).toMatch(/\)\}\s*\{cover && <SwitchingCover email=\{cover\.email\} message=\{cover\.message\} \/>\}\s*<\/div>/);
    // Escape gives the focus back to the trigger.
    expect(foot).toMatch(/setOpen\(false\);\s*rootRef\.current\?\.querySelector<HTMLButtonElement>\("button\[data-account-trigger\]"\)\?\.focus\(\);/);
  });

  it("the cover is a Modal that nothing dismisses, and every menu leaves it alone", () => {
    const src = SWITCHER();
    const cover = src.slice(src.indexOf("export function SwitchingCover"), src.indexOf("const NO_DISMISS"));
    // `Modal` traps and takes the focus, and marks the page under it.
    expect(cover).toMatch(/<Modal open onClose=\{NO_DISMISS\} label=\{text\} showClose=\{false\}/);
    expect(cover).toContain('data-switching-cover=""');
    expect(src).toMatch(/const NO_DISMISS = \(\) => \{\};/);
    expect(cover).not.toMatch(/fixed inset-0/);
    const dismiss = code(read("hooks/useDismiss.ts"));
    expect(dismiss).toMatch(/if \(coverUp\(\)\) return;/);
    expect(dismiss).toMatch(/e\.key === "Escape" && !coverUp\(\)/);
  });
});

describe("the frames", () => {
  const sidebar = code(read("components/Sidebar.tsx"));

  it("draws AccountFoot with the bar on, and the classic account foot only with it off", () => {
    expect(sidebar).toMatch(/\{barOn \? \(\s*<AccountFoot [^>]*\/>\s*\) : \(\s*<SidebarAccountFooter/);
    expect(sidebar.match(/<SidebarAccountFooter/g) ?? []).toHaveLength(1);
    expect(sidebar.match(/<AccountFoot\b/g) ?? []).toHaveLength(1);
    // The old session footer, with its own Sign out, is classic-only too.
    expect(sidebar).toMatch(/\{!barOn && !shellNav && !accounts\.enabled && !collapsed && \(/);
  });

  it("the phone drawer keeps its own account surfaces", () => {
    const shell = code(read("components/AppShell.tsx"));
    expect(shell).toMatch(/<DrawerAccountHeader onClose=\{close\} \/>/);
    expect(shell).toMatch(/<DrawerAccountFoot /);
    expect(shell).not.toMatch(/(?<!Drawer)AccountFoot\b/);
  });
});
