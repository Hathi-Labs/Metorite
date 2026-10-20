"use client";

/**
 * Row 1 of the CRM toolbar: which view (WS-53 CRM-U8).
 *
 * The view chips come from the shared `ViewChips`. This file adds the CRM's
 * own controls at the right end: the pipeline picker, the List and Board
 * switch, "Board options" (a board), "Columns" and "Export" (a list). They
 * used to sit in the app bar. The app bar keeps only Refresh and New.
 *
 * `Board options` holds the two board toggles in one panel: the stages that
 * hold no deal, and the order of the cards. Each toggle is a pressed button.
 */

import { useRef, useState, type ReactNode } from "react";

import { ModeSwitch } from "@/components/ModeSwitch";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import ViewChips from "@/components/ViewChips";
import { useViewMode } from "@/components/ViewModeProvider";
import { useDismiss } from "@/hooks/useDismiss";

import type { ViewActions } from "../lib/useViewActions";
import type { CrmLayout } from "../lib/urlState";

export type BoardOptions = {
  showEmpty: boolean;
  onToggleEmpty: () => void;
  nextActivity: boolean;
  onToggleNextActivity: () => void;
};

export type CrmViewBarProps = {
  /** "All deals". */
  allLabel: string;
  actions: ViewActions;
  isAdmin: boolean;
  me?: string | null;
  /** The pipeline picker, when the org has two or more pipelines. */
  picker?: ReactNode;
  /** The List and Board switch, for a list that has a board. */
  layout?: { mode: CrmLayout; label: string; onPick: (next: CrmLayout) => void } | null;
  /** The Deals board toggles. */
  board?: BoardOptions | null;
  /** Open the columns dialog. A list only. */
  onColumns?: (() => void) | null;
  /** Export what is on screen. A list only. */
  onExport?: (() => void) | null;
  exporting?: boolean;
};

export default function CrmViewBar({
  allLabel,
  actions,
  isAdmin,
  me = null,
  picker = null,
  layout = null,
  board = null,
  onColumns = null,
  onExport = null,
  exporting = false,
}: CrmViewBarProps) {
  const { isMobile } = useViewMode();
  const size = isMobile ? "icon-sm" : "sm";
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  useDismiss(optionsOpen, () => setOptionsOpen(false), root);

  const trailing = (
    <div ref={root} className="flex items-center gap-1">
      {picker}
      {layout ? (
        // The shared control that Projects and My Tasks draw (CRM-Q3).
        <ModeSwitch<CrmLayout>
          modes={[
            { id: "list", icon: "List" },
            { id: "board", icon: "Kanban" },
          ]}
          mode={layout.mode}
          onPick={layout.onPick}
          label={layout.label}
        />
      ) : null}
      {board ? (
        <>
          <Button
            variant="ghost"
            size={size}
            icon="SlidersHorizontal"
            aria-haspopup="dialog"
            aria-expanded={optionsOpen}
            selected={optionsOpen}
            aria-label="Board options"
            onClick={(e) => {
              setAnchor(e.currentTarget);
              setOptionsOpen((open) => !open);
            }}
          >
            {isMobile ? null : "Board options"}
          </Button>
          <AnchoredPanel
            anchor={anchor}
            open={optionsOpen}
            align="end"
            className="w-56"
            panelProps={{ role: "dialog", "aria-label": "Board options" }}
          >
            <div className="flex flex-col p-1">
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                icon={board.showEmpty ? "Eye" : "EyeOff"}
                selected={board.showEmpty}
                title={
                  board.showEmpty
                    ? "Hide the stages that hold no deal"
                    : "Show the stages that hold no deal"
                }
                onClick={board.onToggleEmpty}
              >
                Empty stages
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                icon="CalendarClock"
                selected={board.nextActivity}
                title={
                  board.nextActivity
                    ? "Cards are in order of next activity. Click to order them by time in stage"
                    : "Cards are in order of time in stage. Click to order them by next activity"
                }
                onClick={board.onToggleNextActivity}
              >
                Next activity
              </Button>
            </div>
          </AnchoredPanel>
        </>
      ) : null}
      {onColumns ? (
        <Button
          variant="ghost"
          size={size}
          icon="Columns3"
          aria-label="Columns"
          title="Choose the columns"
          onClick={onColumns}
        >
          {isMobile ? null : "Columns"}
        </Button>
      ) : null}
      {onExport ? (
        // WS-26i-export — the filter that is on screen, as a CSV. It is on
        // the lists only: the board is a page of each lane, so an export
        // could not honestly claim to be what the member was looking at.
        <Button
          variant="ghost"
          size={size}
          icon="Download"
          loading={exporting}
          aria-label="Export CSV"
          title="Export what is on screen as a CSV"
          onClick={onExport}
        >
          {isMobile ? null : "Export"}
        </Button>
      ) : null}
    </div>
  );

  return (
    <ViewChips
      allLabel={allLabel}
      views={actions.views}
      activeId={actions.active?.id ?? null}
      dirty={actions.dirty}
      isAdmin={isAdmin}
      me={me}
      busy={actions.busy}
      onSelect={actions.select}
      onSave={actions.save}
      onUpdate={actions.update}
      onReset={actions.reset}
      onRename={actions.rename}
      onShare={actions.toggleShare}
      onDelete={actions.remove}
      deleteCopy={actions.deleteCopy}
      trailing={trailing}
    />
  );
}
