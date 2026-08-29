"use client";

// The console's top bar: brand, navigation and sign-out. Client-side only for
// the sign-out fetch — it calls the session BFF route (DELETE) and never holds
// any credential (both cookies are httpOnly; the browser carries them).
//
// ⚠️ **Every entry here is visible to every role.** The three surfaces are all
// readable by a `viewer`, and hiding a link from somebody who may follow it
// only teaches them to guess URLs. What a role may *do* is decided by the
// Console's §5 matrix, on the request, not by which links this file renders.

import { usePathname } from "next/navigation";

import Elevation from "./Elevation";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { href: "/", label: "Customers" },
  { href: "/activity", label: "Activity" },
  { href: "/models", label: "Models" },
  { href: "/providers", label: "Providers" },
  { href: "/operators", label: "Operators" },
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
    <header className="topbar">
      <a href="/" className="brand">
        Metorite <span>Operator Console</span>
      </a>
      <nav>
        {NAV.map((item) => (
          <a
            key={item.href}
            href={item.href}
            aria-current={isCurrent(item.href, pathname) ? "page" : undefined}
          >
            {item.label}
          </a>
        ))}
      </nav>
      <Elevation />
      <ThemeToggle />
      <button type="button" className="linklike" onClick={signOut}>
        Sign out
      </button>
    </header>
  );
}
