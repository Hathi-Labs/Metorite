/**
 * The account at the shell bar's right end, and the Metorite menu at the
 * sidebar's foot (owner, 2026-10-12).
 *
 * The owner moved the account selector from the sidebar's foot to the bar's
 * top-right corner, and put our comet and the word "Metorite" in its old
 * place. These checks fail on the wrong builds that look right:
 *   1. The account widget is not the last item of the bar's right group.
 *   2. Our comet signature of 2026-10-11 came back to the bar.
 *   3. The trigger lost its name, its tip, its avatar or its quiet email.
 *   4. The account menu lost the current account's mark, the switch list,
 *      "Add another account", or the sign-out that fits the count.
 *   5. The sidebar foot kept an account act, with the shell bar on.
 *   6. The classic frame (shell bar off) lost the foot it had.
 *   7. The Metorite menu grew a second list beside `metoriteMenu`.
 * The geometry in a browser is `e2e/shell-account.spec.ts`.
 */

import { readFileSync } from "node:fs";
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

import { BarAccount, BarAccountMenu } from "@/components/AccountSwitcher";
import MetoriteFoot from "@/components/MetoriteFoot";
import { PANES, visibleSections } from "@/lib/nav";
import type { Accounts } from "@/lib/accountSwitch";
import { ShellBarRow } from "./ShellBar";
import { CHANGE_LAYOUT_ITEM, metoriteMenu } from "./shellNav";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
/** Source without comments, so a comment cannot satisfy or break a check. */
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

describe("the account is the bar's last item", () => {
  it("the row draws it after the activity control, at the end of the right group", () => {
    const html = renderToStaticMarkup(
      createElement(ShellBarRow, {
        here: null,
        onOpen: () => {},
        activity: createElement("i", { "data-zone": "activity" }),
        account: createElement("i", { "data-zone": "account" }),
      }),
    );
    expect(html.endsWith('<i data-zone="activity"></i><i data-zone="account"></i></div></header>')).toBe(true);
  });

  it("ShellFrame passes BarAccount to the row, always", () => {
    expect(code(read("lib/shell/ShellBar.tsx"))).toMatch(/account=\{<BarAccount \/>\}/);
  });

  it("our comet no longer signs the bar", () => {
    for (const rel of ["lib/shell/ShellBar.tsx", "components/AccountSwitcher.tsx", "lib/orgBranding.ts"]) {
      expect(code(read(rel)), rel).not.toMatch(/BarSignature|data-shell-signature|MetoriteMark|ourMarkLeads/);
    }
  });
});

describe("the trigger is quiet, named and tipped", () => {
  const html = renderToStaticMarkup(createElement(BarAccount));

  it("is a Button named by the email, with the name, email and organization as its tip", () => {
    expect(html).toContain('aria-label="Account: asha@example.com"');
    expect(html).toContain('title="Asha Rao\nasha@example.com\nAcme"');
    expect(html).toContain('aria-haspopup="dialog"');
    // The primitive, so it wears the bar's focus ring (`cc-control`).
    expect(html).toMatch(/<button[^>]*class="cc-control /);
  });

  it("draws the avatar, then the email that shows from 1400px up only", () => {
    expect(html).toMatch(/rounded-full[^"]*">A<\/span>/);
    const email = /<span data-account-email="" class="([^"]*)">asha@example.com<\/span>/.exec(html)?.[1] ?? "";
    expect(email.split(" ")).toEqual(expect.arrayContaining(["hidden", "min-[1400px]:block", "truncate", "min-w-0"]));
    expect(email.split(" ")).not.toContain("block");
  });

  it("shrinks before it spills over the command bar", () => {
    const root = /<div data-shell-account="" class="([^"]*)"/.exec(html)?.[1] ?? "";
    expect(root.split(" ")).toEqual(expect.arrayContaining(["min-w-0", "shrink"]));
    expect(root.split(" ")).not.toContain("shrink-0");
  });
});

describe("the account menu holds account acts only", () => {
  const menu = (accounts: Accounts) =>
    renderToStaticMarkup(createElement(BarAccountMenu, { accounts, orgName: "Acme", onChanged: () => {} }));
  const ACTIVE = { email: "asha@example.com", name: "Asha Rao" };
  const MANY: Accounts = {
    enabled: true,
    active: ACTIVE,
    others: [
      { slot: 2, email: "asha@fracktal.in", name: "Asha R", organization: "Fracktal Works" },
      { slot: 0, email: "ops@acme.com", name: null, organization: null },
    ],
  };

  it("marks the current account, with its organization", () => {
    const html = menu(MANY);
    expect(html).toMatch(/data-current-account="" aria-current="true"/);
    const head = html.slice(html.indexOf("data-current-account"), html.indexOf("Switch account"));
    expect(text(head)).toContain("Asha Rao asha@example.com Acme Signed in");
  });

  it("lists the other accounts, each with its organization, then Add and both sign-outs", () => {
    const html = menu(MANY);
    expect(html.match(/role="listitem"/g) ?? []).toHaveLength(2);
    expect(text(html)).toContain("Fracktal Works · asha@fracktal.in");
    const t = text(html);
    expect(t.indexOf("Add another account")).toBeGreaterThan(t.indexOf("ops@acme.com"));
    expect(t).toMatch(/Add another account Sign out Sign out of all accounts$/);
  });

  it("with one account: Add, and one Sign out", () => {
    const t = text(menu({ enabled: true, active: ACTIVE, others: [] }));
    expect(t).toMatch(/Add another account Sign out$/);
    expect(t).not.toContain("Switch account");
  });

  it("with the switcher flag off: the current account and Sign out, nothing else", () => {
    const html = menu({ enabled: false, active: ACTIVE, others: [] });
    expect(text(html)).toBe("A Asha Rao asha@example.com Acme Signed in Sign out");
  });

  it("holds none of the member's pages or settings", () => {
    const t = text(menu(MANY));
    for (const word of ["My Profile", "My access", "Appearance", "Change my layout", "mode"]) expect(t).not.toContain(word);
  });

  it("signs this account out with signOutThis, and all of them with signOutAll", () => {
    const src = code(read("components/AccountSwitcher.tsx"));
    const body = src.slice(src.indexOf("export function BarAccountMenu"), src.indexOf("export function DrawerAccountHeader"));
    expect(body).toMatch(/others\.length === 0\)\s*\{\s*await signOutAll\(\)/);
    expect(body).toMatch(/signOutThis\(active\.email, others\[0\]\)/);
    expect(body).toMatch(/onClick=\{\(\) => void pick\(o\)\}/);
  });
});

describe("the sidebar foot is the Metorite menu, with the shell bar on", () => {
  const sidebar = code(read("components/Sidebar.tsx"));

  it("draws MetoriteFoot with the bar on, and the account foot only in the classic frame", () => {
    expect(sidebar).toMatch(/\{barOn \? \(\s*<MetoriteFoot [^>]*\/>\s*\) : \(\s*<SidebarAccountFooter/);
    expect(sidebar.match(/<SidebarAccountFooter/g) ?? []).toHaveLength(1);
    // The old session footer, with its own Sign out, is classic-only too.
    expect(sidebar).toMatch(/\{!barOn && !shellNav && !accounts\.enabled && !collapsed && \(/);
  });

  it("the foot names no account act", () => {
    const foot = code(read("components/MetoriteFoot.tsx"));
    expect(foot).not.toMatch(/accountSwitch|AccountSwitcher|switchTo|addAccount|signOut|removeAccount|useAccounts/);
  });

  it("open: the comet and the word; folded: the comet alone; both one named button", () => {
    const sections = visibleSections(PANES.map((p) => p.feature).filter((f): f is string => !!f), true);
    const open = renderToStaticMarkup(createElement(MetoriteFoot, { collapsed: false, sections, layout: true }));
    const folded = renderToStaticMarkup(createElement(MetoriteFoot, { collapsed: true, sections, layout: true }));
    for (const html of [open, folded]) {
      expect(html.match(/<button\b/g) ?? []).toHaveLength(1);
      expect(html).toContain('aria-label="Metorite settings"');
      expect(html).toContain('title="Metorite"');
      expect(html).toMatch(/<svg[^>]*width="20" height="20"[^>]*data-metorite-mark=""/);
    }
    expect(text(open)).toBe("Metorite");
    expect(text(folded)).toBe("");
    // The folded geometry of a nav row: a `p-2` block, a centred `p-2.5` button.
    expect(folded).toMatch(/data-metorite-foot="" class="[^"]*\bp-2\b/);
    expect(folded).toMatch(/<button[^>]*class="[^"]*justify-center p-2\.5/);
  });

  it("draws its rows from metoriteMenu alone", () => {
    const foot = code(read("components/MetoriteFoot.tsx"));
    expect(foot).toMatch(/metoriteMenu\(sections, \{ layout \}\)/);
    expect(foot).not.toMatch(/accountLinks|MY_ACCESS|"\/people\/me"|"\/settings\/appearance"|Change my layout/);
  });
});

describe("metoriteMenu: You, then Settings, from one list", () => {
  const rows = (g: ReturnType<typeof metoriteMenu>) =>
    g.map((x) => [x.id, x.items.map((i) => (i.kind === "link" ? i.href : i.kind === "action" ? i.id : i.kind))]);
  const ALL = PANES.map((p) => p.feature).filter((f): f is string => !!f);

  it("for a member: My Profile and My access, then Appearance, the colour mode and Change my layout", () => {
    expect(rows(metoriteMenu(visibleSections(ALL, false), { layout: true }))).toEqual([
      ["you", ["/people/me", "/people/access"]],
      ["settings", ["/settings/appearance", "colour-mode", "change-layout"]],
    ]);
  });

  it("drops Change my layout with the shell nav off", () => {
    const g = metoriteMenu(visibleSections(ALL, false), { layout: false });
    expect(g.flatMap((x) => x.items)).not.toContainEqual(CHANGE_LAYOUT_ITEM);
  });

  it("an unresolved viewer gets the colour mode only, and no empty group", () => {
    expect(rows(metoriteMenu(visibleSections(null, false), { layout: false }))).toEqual([["settings", ["colour-mode"]]]);
  });
});
