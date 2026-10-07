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
 *
 * **With the shell bar on (NS-1, `NEXT_PUBLIC_SHELL_BAR`), the desktop bar
 * draws no row of its own.** It portals the same contents into the shell
 * bar's slots: the rail toggle, the title and the actions on the left, the
 * tools on the right. The shell owns the middle, where the command bar is. So
 * an app keeps calling this component exactly as before, and loses no height
 * (`navigation_shell.md` §3.1). The phone bar (`compact`) is unchanged.
 */

import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

import Button from "@/components/ui/Button";
import { useShellSlots } from "@/lib/shell/ShellBar";
import { shellBarOn } from "@/lib/shell/registry";

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
  const slots = useShellSlots();
  const inShell = !!slots && !compact;
  useEffect(() => (inShell ? slots!.claim() : undefined), [inShell, slots]);

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

  // The desktop contents, ONE spelling, drawn either in this component's own
  // row or in the shell bar's left slot.
  const left = (
    <>
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
        <span className={`min-w-0 truncate text-xs text-muted-foreground ${inShell ? "hidden xl:inline" : ""}`}>
          {subtitle}
        </span>
      ) : null}
      {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
    </>
  );

  if (inShell) {
    return (
      <>
        {slots!.left ? createPortal(left, slots!.left) : null}
        {slots!.right && tools ? createPortal(tools, slots!.right) : null}
      </>
    );
  }

  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card px-2">
      {left}
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
