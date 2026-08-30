"use client";

// The console's LEFT SIDEBAR — brand, navigation, and the account controls.
//
// ⚠️ **Still exported as `Header`, and still rendered inside `main.wrap`.**
// Eight pages call `<Header />` as their first child. Renaming it, or moving it
// out to `layout.tsx`, would mean editing all eight and would put the sidebar on
// `/login` as well — a sign-in page must not show navigation to somebody who
// cannot use it. The sidebar is `position: fixed` instead, and `.wrap` carries
// the matching padding, so the DOM stays exactly where every page already puts
// it. That is a deliberate trade: one CSS coupling in place of eight edits and
// an auth branch in the layout.
//
// 🔴 **Grouped, because a flat list of seven stopped teaching anything.** The
// sections are three different jobs: looking after customers, setting up the AI
// we sell them, and running the console itself. An operator with a customer
// waiting is in the first group and never needs the third. A flat list makes
// them read all seven every time.
//
// ⚠️ **Every entry here is visible to every role.** The surfaces are all
// readable by a `viewer`, and hiding a link from somebody who may follow it only
// teaches them to guess URLs. What a role may *do* is decided by the Console's
// §5 matrix, on the request, not by which links this file renders.
//
// ⚠️ **Icons are inline SVG on `currentColor`, never an icon package.** This app
// has no icon system and must not grow a dependency to get one — `stroke:
// currentColor` means the active, hover and theme states all come from the token
// that already colours the label.

import { usePathname } from "next/navigation";

import CommandJump from "./CommandJump";
import Elevation from "./Elevation";
import Identity from "./Identity";
import ThemeToggle from "./ThemeToggle";

type NavItem = {
  href: string;
  label: string;
  icon: React.ReactNode;
  /** Extra paths this entry stays lit for — a tab of this section that
   *  keeps its own URL (SectionTabs.tsx). */
  covers?: string[];
};
type NavGroup = { title: string; items: NavItem[] };

const I = (d: string) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

export const NAV: NavGroup[] = [
  {
    title: "Customers",
    items: [
      { href: "/", label: "Organizations", icon: I("M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75") },
      { href: "/usage", label: "AI usage", icon: I("M3 3v18h18M7 15l4-5 3 3 5-7") },
    ],
  },
  {
    title: "The AI we sell",
    items: [
      // One entry for the whole Models section — /providers is its second
      // TAB (owner directive 2026-08-30), so `covers` keeps this entry lit
      // while the operator is on the Providers tab.
      { href: "/models", label: "Models", covers: ["/providers"], icon: I("M12 2 2 7l10 5 10-5-10-5ZM2 17l10 5 10-5M2 12l10 5 10-5") },
      { href: "/tiers", label: "Tiers & backups", icon: I("M4 20h4V10H4v10ZM10 20h4V4h-4v16ZM16 20h4v-7h-4v7Z") },
      // Money got its own page: what a customer pays, and the margin left.
      { href: "/pricing", label: "Pricing", icon: I("M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.83ZM7 7h.01") },
    ],
  },
  {
    title: "This console",
    items: [
      { href: "/operators", label: "Operators", icon: I("M9 12l2 2 4-4M12 3l7 4v5c0 4.4-3 8.4-7 9.5C8 20.4 5 16.4 5 12V7l7-4Z") },
      { href: "/activity", label: "Activity", icon: I("M22 12h-4l-3 9L9 3l-3 9H2") },
    ],
  },
];

/** Which nav entry the current URL belongs to.
 *
 * ⚠️ "/" is matched EXACTLY. A `startsWith` test would mark Organizations as
 * the current page on every route in the app, since every path starts with "/". */
export function isCurrent(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export default function Header() {
  const pathname = usePathname() ?? "/";

  async function signOut() {
    // On the session path this REVOKES the row server-side before the cookie
    // is cleared, so the token is dead everywhere rather than just forgotten
    // by this browser.
    await fetch("/api/operator/session", { method: "DELETE" });
    window.location.href = "/login";
  }

  return (
    <aside className="sidebar">
      <a href="/" className="brand">
        <span className="mark" aria-hidden="true">M</span>
        <span className="brandname">
          Metorite <span>Operator</span>
        </span>
      </a>

      <CommandJump />

      <nav aria-label="Sections">
        {NAV.map((group) => (
          <div className="navgroup" key={group.title}>
            <h2>{group.title}</h2>
            {group.items.map((item) => (
              <a
                key={item.href}
                href={item.href}
                aria-current={
                  [item.href, ...(item.covers ?? [])].some((h) =>
                    isCurrent(h, pathname))
                    ? "page"
                    : undefined
                }
              >
                {item.icon}
                <span>{item.label}</span>
              </a>
            ))}
          </div>
        ))}
      </nav>

      {/* Pinned to the bottom. The elevation window is a standing destructive
          privilege and must be visible from every page — not on a settings
          screen somebody has to go looking for. */}
      <div className="sidebar-foot">
        <Identity />
        <Elevation />
        <div className="sidebar-actions">
          <ThemeToggle />
          <button type="button" className="linklike" onClick={signOut}>
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
