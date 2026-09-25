"use client";

/**
 * The APP BAR — the slim `h-10` strip an app with a rail opens with.
 *
 * `DESIGN_SYSTEM.md` §6a names two ways a surface opens: this bar, and
 * `PageHeader`. Until 2026-09-24 the bar had no component. Projects and My
 * Tasks each drew their own, and the two drifted: My Tasks had a hand-rolled
 * rail toggle, no divider, its name in a `<span>`, no search and no bell.
 * Projects had a `Button` toggle, a divider, an `<h1>` with a scope line, and
 * both tools. One product, two bars.
 *
 * So the bar is a component now, and each app fills its slots:
 *
 *   [rail toggle] | <h1>App</h1> subtitle  [actions]        [tools …]
 *
 * - `rail` — the toggle for the app's left rail. Omit it where there is none.
 * - `title` — the app's name. **This is the app's one `<h1>`.** A pane under
 *   the bar titles itself with an `<h2>`.
 * - `subtitle` — the scope line beside the name.
 * - `actions` — app-level verbs that sit next to the name (Capture).
 * - `tools` — pushed to the right end: search, the bell, the assistant.
 *
 * `compact` is the phone bar. It has no rail and no divider, and the title is
 * what you are looking at, so it reads at body size and takes the free width.
 *
 * Fences: `AppTopBar.test.ts` (the markup), `pageHeading.test.ts` (the one
 * `<h1>`) and `sharedTaskUi.test.ts` (both apps reach this file, and nobody
 * declares a second one).
 */

import type { ReactNode } from "react";

import Button from "@/components/ui/Button";

export interface AppTopBarRail {
  open: boolean;
  onToggle: () => void;
  /** What the rail holds, for the label: "the project tree", "your lists". */
  noun: string;
}

export interface AppTopBarProps {
  title: ReactNode;
  subtitle?: ReactNode;
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

export function AppTopBar({
  title,
  subtitle,
  rail,
  actions,
  tools,
  compact = false,
}: AppTopBarProps) {
  if (compact) {
    return (
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-border bg-card px-2">
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
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card px-2">
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
          <div className="h-4 w-px bg-border" aria-hidden />
        </>
      ) : null}
      <h1 className="shrink-0 text-xs font-medium text-muted-foreground">{title}</h1>
      {subtitle ? (
        <span className="min-w-0 truncate text-xs text-muted-foreground">{subtitle}</span>
      ) : null}
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
