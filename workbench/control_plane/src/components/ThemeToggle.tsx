"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

/**
 * ThemeToggle — light/dark COLOUR MODE switch (Sun/Moon icons).
 *
 * Uses next-themes. Mounted-only render prevents hydration mismatch.
 * Add to sidebar footer and mobile overflow menu.
 *
 * The name is a fossil: `next-themes` calls the light/dark class a "theme", and
 * this component is named after its hook. It has never switched a theme, and
 * since the engine retired (2026-08-31) there is only one to switch to. Every
 * string it shows says "mode", which is what a member actually changes.
 */

export default function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <button className="rounded-lg p-1.5 text-muted-foreground" aria-label="Toggle colour mode">
        <div className="w-4 h-4" />
      </button>
    );
  }

  const isDark = theme === "dark";

  return (
    <Button variant="ghost" size="icon-sm" layout="" onClick={() => setTheme(isDark ? "light" : "dark")} aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"} title={isDark ? "Light mode" : "Dark mode"}>
      {isDark ? <Icon name="Sun" size={15} /> : <Icon name="Moon" size={15} />}
    </Button>
  );
}

/**
 * ThemeToggleMenuItem — same toggle but styled as a full-width menu item
 * (for use in dropdowns and drawer menus). Accepts an optional onClick callback
 * to close the parent menu/drawer after toggling.
 */
export function ThemeToggleMenuItem({ onClick }: { onClick?: () => void }) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return <div className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-muted-foreground">Colour mode</div>;
  }

  const isDark = theme === "dark";

  return (
    <Button variant="ghost" size="none" layout="flex items-center" onClick={() => {
        setTheme(isDark ? "light" : "dark");
        onClick?.();
      }} className="w-full gap-3 px-3 py-2.5 text-sm">
      {isDark ? <Icon name="Sun" size={16} className="shrink-0" /> : <Icon name="Moon" size={16} className="shrink-0" />}
      {isDark ? "Light mode" : "Dark mode"}
    </Button>
  );
}
