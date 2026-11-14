"use client";

/**
 * The layout shell of a Studio app (STU-AF2b, `ai_studio.md` §5.5): the app bar, a left rail of
 * pages, and the page beside it.
 *
 * It copies the split of the CRM. The model is pure (`lib/studio/railModel.ts`). This file draws
 * the rows with `RailRow`. The frame owns the toggle and the phone drawer. A phone has no column:
 * the title opens the pages in the shell's drawer, and a row choice closes it.
 *
 * This file knows no page model and no view. The caller gives it the sections and the children.
 * Labels and titles come from a manifest, so they draw as text and nothing else.
 */
import type { ReactNode } from "react";

import Icon from "@/components/Icon";
import { AppTopBar, type AppTopBarBack } from "@/components/AppTopBar";
import { useMobileDrawer } from "@/components/AppShell";
import Button from "@/components/ui/Button";
import RailRow from "@/components/ui/RailRow";
import { useViewMode } from "@/components/ViewModeProvider";
import { railClass, useRailFold } from "@/lib/railFold";
import type { RailSectionModel } from "@/lib/studio/railModel";

export interface StudioAppRail {
  sections: readonly RailSectionModel[];
  /** The id of the page on screen. */
  activePage: string;
  onPick: (page: string) => void;
  /** The ready text of a count, by page id (STU-AF3b). A page with no entry shows no count. */
  counts?: ReadonlyMap<string, string>;
}

export interface StudioAppFrameProps {
  title: string;
  back?: AppTopBarBack;
  /** App-level verbs in the app bar, next to the name. */
  actions?: ReactNode;
  /** Absent: the frame draws no rail and no toggle. */
  rail?: StudioAppRail;
  children: ReactNode;
}

/** The rows of the rail. Presentational: the frame owns where it sits. */
function RailRows({ rail, onChoose }: { rail: StudioAppRail; onChoose: (page: string) => void }) {
  return (
    <div className="flex flex-col gap-0.5">
      {rail.sections.map((section, at) => (
        <div key={`${section.title}:${at}`} role="group" aria-label={section.title || "Pages"} className="flex flex-col gap-0.5">
          {section.title ? (
            <p className="truncate px-2 pb-0.5 pt-2 text-xs font-medium text-muted-foreground">{section.title}</p>
          ) : null}
          {section.rows.map((row) => (
            <RailRow
              key={row.page}
              label={row.label}
              icon={<Icon name={row.icon} size={15} />}
              selected={row.page === rail.activePage}
              meta={rail.counts?.get(row.page)}
              onSelect={() => onChoose(row.page)}
              buttonProps={row.page === rail.activePage ? { "aria-current": "page" } : undefined}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export default function StudioAppFrame({ title, back, actions, rail, children }: StudioAppFrameProps) {
  const { isMobile } = useViewMode();
  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();
  const fold = useRailFold();

  const choose = (page: string) => {
    rail?.onPick(page);
    if (isMobile) closeDrawer();
  };

  const openPages = () => {
    if (!rail) return;
    openDrawer(
      <nav aria-label="Pages" className="p-2">
        <RailRows rail={rail} onChoose={choose} />
      </nav>,
    );
  };

  // A phone bar has no rail toggle. The title opens the pages instead, as the CRM title does.
  const barTitle = isMobile && rail ? (
    <span className="inline-flex max-w-full items-center gap-1 align-middle">
      <Button variant="ghost" size="icon-sm" icon="PanelLeftOpen" aria-label="Show pages" title="Show pages" onClick={openPages} />
      <span className="truncate">{title}</span>
    </span>
  ) : title;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <AppTopBar
        title={barTitle}
        back={back}
        actions={actions}
        rail={rail ? { open: fold.open, onToggle: fold.toggle, noun: "the pages" } : undefined}
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {rail && !isMobile && fold.open ? (
          <nav aria-label="Pages" className={`w-56 shrink-0 overflow-y-auto border-r border-border bg-card p-2 ${railClass(fold.settled)}`}>
            <RailRows rail={rail} onChoose={choose} />
          </nav>
        ) : null}
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
