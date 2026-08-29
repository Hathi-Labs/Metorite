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

import Elevation from "./Elevation";
import ThemeToggle from "./ThemeToggle";

type NavItem = { href: string; label: string; icon: React.ReactNode };

const I = (d: string) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

const NAV: NavItem[] = [
  { href: "/", label: "Customers", icon: I("M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75") },
  { href: "/activity", label: "Activity", icon: I("M22 12h-4l-3 9L9 3l-3 9H2") },
  { href: "/models", label: "Models", icon: I("M12 2 2 7l10 5 10-5-10-5ZM2 17l10 5 10-5M2 12l10 5 10-5") },
  { href: "/providers", label: "Providers", icon: I("M15 7h3a5 5 0 0 1 0 10h-3m-6 0H6A5 5 0 0 1 6 7h3M8 12h8") },
  { href: "/operators", label: "Operators", icon: I("M9 12l2 2 4-4M12 3l7 4v5c0 4.4-3 8.4-7 9.5C8 20.4 5 16.4 5 12V7l7-4Z") },
];

/** Which nav entry the current URL belongs to.
 *
 * ⚠️ "/" is matched EXACTLY. A `startsWith` test would mark Customers as the
 * current page on every route in the app, since every path starts with "/". */
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
        Metorite <span>Operator</span>
      </a>

      <nav aria-label="Sections">
        {NAV.map((item) => (
          <a
            key={item.href}
            href={item.href}
            aria-current={isCurrent(item.href, pathname) ? "page" : undefined}
          >
            {item.icon}
            <span>{item.label}</span>
          </a>
        ))}
      </nav>

      {/* Pinned to the bottom. The elevation window is a standing destructive
          privilege and must be visible from every page — not on a settings
          screen somebody has to go looking for. */}
      <div className="sidebar-foot">
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
