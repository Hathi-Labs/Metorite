"use client";

/**
 * The APP BAR — the title bar every app opens with.
 *
 * **The rule (owner, 2026-10-10):** the shell bar at the top of the window is
 * constant across Metorite, and an app never renders into it. Every app opens
 * with THIS bar, under the shell bar, at the top of its own content area. It
 * holds, left to right:
 *
 *   [rail toggle] | [icon] <h1>App</h1> subtitle  [actions]        [tools …]
 *
 * - `rail` — the toggle for the app's left rail. Omit it where there is none.
 *   The Email app's in-app toggle is the model the owner named.
 * - `icon` — the app's icon. By default it is the icon of the nav pane that
 *   owns this route, so the bar and the sidebar cannot disagree. Pass a name
 *   for a route no pane owns (My Day at `/`), or `null` for none.
 * - `title` — the app's name. **This is the app's one `<h1>`.** A pane or a
 *   page under the bar titles itself with an `<h2>` (`PageHeader`,
 *   `SettingsHeader`).
 * - `subtitle` — the scope line beside the name.
 * - `actions` — app-level verbs that sit next to the name (Capture).
 * - `tools` — pushed to the right end: the bell, the assistant, refresh,
 *   settings.
 *
 * One height (`h-10`), one order and one look in every app. That sameness is
 * the point: the eye finds the name at the left and the tools at the right,
 * whichever app is open.
 *
 * `compact` is the phone bar. It has no rail and no divider, and the title is
 * what you are looking at, so it reads at body size and takes the free width.
 * On a phone the bar is ALWAYS the compact one, so an app that passes no
 * `compact` still gets the phone's shape there, not the desktop row.
 *
 * History. Until 2026-09-24 the bar had no component, and Projects and My
 * Tasks each drew their own. From 2026-10-08 (NS-1) the desktop bar portalled
 * its contents into slots in the shell bar. The owner reversed that on
 * 2026-10-10: the shell bar is the product's frame, and an app's name and
 * tools belong to the app, where the eye looks for them.
 *
 * Fences: `AppTopBar.test.ts` (the markup), `lib/shell/appBar.test.ts` (every
 * live pane renders this bar, and nothing renders into the shell bar),
 * `pageHeading.test.ts` (the one `<h1>`) and `sharedTaskUi.test.ts` (nobody
 * declares a second bar).
 */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import { PANES, type NavPane } from "@/lib/nav";
import { contextPane, shellBarOn } from "@/lib/shell/registry";

export interface AppTopBarRail {
  open: boolean;
  onToggle: () => void;
  /** What the rail holds, for the label: "the project tree", "your lists". */
  noun: string;
}

export interface AppTopBarProps {
  title: ReactNode;
  subtitle?: ReactNode;
  /**
   * A Lucide name. Omitted, the bar takes the icon of the nav pane that owns
   * the route (`appBarIcon`). `null` draws no icon.
   */
  icon?: string | null;
  rail?: AppTopBarRail;
  actions?: ReactNode;
  tools?: ReactNode;
  /** The phone bar: no rail, no divider, the title at body size. */
  compact?: boolean;
}

/** The rail toggle's label. Says what a press will do. */
export function railLabel(rail: Pick<AppTopBarRail, "open" | "noun">): string {
  return `${rail.open ? "Hide" : "Show"} ${rail.noun}`;
}

/**
 * The app that owns a route: the nav pane with the longest matching `href`.
 * So `/people/me` is My Profile, and `/people/chart` is the People app. Null
 * when no pane owns the route. A layout that serves two panes (People and My
 * Profile) names its bar from this.
 */
export function appBarPane(pathname: string | null): NavPane | null {
  if (!pathname) return null;
  return contextPane(pathname, PANES);
}

/** The icon of the app that owns a route, or null. */
export function appBarIcon(pathname: string | null): string | null {
  return appBarPane(pathname)?.icon ?? null;
}

export function AppTopBar({
  title,
  subtitle,
  icon,
  rail,
  actions,
  tools,
  compact = false,
}: AppTopBarProps) {
  const pathname = usePathname();
  const { isMobile } = useViewMode();
  const glyph = icon === undefined ? appBarIcon(pathname) : icon;

  if (compact || isMobile) {
    return (
      <div data-app-bar="compact" className="flex h-10 shrink-0 items-center gap-1 border-b border-border bg-card px-2">
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          {title}
        </h1>
        {actions || tools ? (
          <div className="flex shrink-0 items-center gap-0.5">
            {actions}
            {tools}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      data-app-bar="desktop"
      className="flex h-10 min-w-0 shrink-0 items-center gap-2 border-b border-border bg-card px-2"
    >
      {rail ? (
        <>
          <Button
            variant="ghost"
            size="icon-sm"
            selected={rail.open}
            icon={rail.open ? "PanelLeftClose" : "PanelLeftOpen"}
            aria-label={railLabel(rail)}
            title={railLabel(rail)}
            onClick={rail.onToggle}
          />
          <div className="h-4 w-px shrink-0 bg-border" aria-hidden />
        </>
      ) : null}
      {/* The name, then the scope line. The scope line gives way first, so
          the name and the tools always show. */}
      <div className="flex min-w-0 items-center gap-2 pl-1">
        {glyph ? (
          <Icon name={glyph} size={15} className="shrink-0 text-muted-foreground" />
        ) : null}
        <h1 className="shrink-0 whitespace-nowrap text-sm font-medium text-foreground">{title}</h1>
        {subtitle ? (
          <span className="min-w-0 truncate text-xs text-muted-foreground">{subtitle}</span>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
      {tools ? (
        <div className="ml-auto flex shrink-0 items-center gap-1">{tools}</div>
      ) : null}
    </div>
  );
}

/**
 * The search trigger for the bar. It opens the same palette ⌘K opens.
 *
 * One component, so the label, the glyph and the shortcut hint are one
 * spelling in both apps.
 */
export function AppSearchButton({
  onOpen,
  title = "Search every project (⌘K)",
  disabled,
}: {
  onOpen: () => void;
  title?: string;
  disabled?: boolean;
}) {
  // The command bar is the one search (§6.7 rule 1). With the shell bar on,
  // an app's own search button would be a second box that says "Search".
  if (shellBarOn()) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      icon="Search"
      onClick={onOpen}
      title={title}
      disabled={disabled}
    >
      Search
    </Button>
  );
}

export default AppTopBar;
