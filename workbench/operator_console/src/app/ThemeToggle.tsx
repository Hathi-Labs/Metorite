"use client";

// Light/dark switch for the staff console.
//
// ⚠️ **The stored value is the authority, and `layout.tsx` applies it BEFORE
// paint.** A toggle that only runs after hydration flashes the old theme on
// every navigation, which is worse than having no toggle: it reads as the page
// breaking. The inline boot script there and this component must agree on the
// storage key, so both import it from `@/lib/theme`.
//
// ⚠️ **Every storage access is wrapped.** `localStorage` throws outright in a
// browser configured to block site data, not merely return null, and an
// uncaught throw here takes the whole top bar down with it — including sign-out
// and the elevation control.

import { useEffect, useState } from "react";

import { STORAGE_KEY, type Theme, applyTheme, readStoredTheme } from "@/lib/theme";

export default function ThemeToggle() {
  // Starts null so the first render matches what the server produced. Deciding
  // the label from storage during render would make the server and the client
  // disagree, and React would discard the markup.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(readStoredTheme() ?? "dark");
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // A viewer who cannot persist still gets the switch for this tab.
    }
  }

  const next: Theme = theme === "light" ? "dark" : "light";

  return (
    <button
      type="button"
      className="linklike"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => choose(next)}
    >
      {theme === null ? "Theme" : theme === "light" ? "Light" : "Dark"}
    </button>
  );
}
