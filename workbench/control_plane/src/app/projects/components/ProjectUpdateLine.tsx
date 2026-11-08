"use client";

/**
 * WS-39 LP-1a — the newest update of a project, as one line on its Overview
 * (`project_management_app.md` §14.2a).
 *
 * A health chip, the first line of the text, the author and the day. It draws
 * what `lib/projectUpdateLine.ts` returns and decides nothing.
 *
 * The update comes with the node summary (`summary.latest_update`), so the
 * line draws with the roll-up and nothing below it moves. `NodeDashboard`
 * draws this only for a project or a subproject that has an update. With the
 * flag off the summary holds no key, and the Overview is unchanged.
 *
 * The text is a button (a row-like raw button with `.cc-link`: the house focus
 * ring, and not the label weight of a control, so the text keeps its look).
 * Enter, a click or a tap opens the whole update in place, below the line,
 * and again closes it. `aria-expanded` and
 * `aria-controls` tell a screen reader what it opens.
 *
 * ⚠️ The hue comes from `projectHealthChip` in `src/lib/statusAccent.ts`, the
 * one status vocabulary. This file names no colour. The chip carries its words
 * and a glyph, so the health never depends on colour alone.
 */

import { useId, useState } from "react";

import Icon from "@/components/Icon";
import { PersonName } from "@/components/PersonName";
import { BADGE_SHAPE } from "@/components/ui/Badge";
import { projectHealthChip } from "@/lib/statusAccent";

import type { ProjectUpdate } from "../lib/api";
import { projectUpdateLine } from "../lib/projectUpdateLine";

export function ProjectUpdateLine({
  update,
  defaultOpen = false,
}: {
  update: ProjectUpdate;
  /** Start open. For a test of the open state; the Overview starts closed. */
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const line = projectUpdateLine(update);
  if (!line) return null;
  return (
    <div
      data-project-update={line.health}
      className="mb-3 rounded-lg border border-border bg-card px-3 py-2 text-xs"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={`${BADGE_SHAPE} shrink-0 whitespace-nowrap px-1.5 py-0.5 text-[11px] font-medium ${projectHealthChip(line.health)}`}
        >
          <Icon name={line.icon} size={11} className="shrink-0" aria-hidden />
          {line.label}
        </span>
        <button
          type="button"
          className="cc-link flex min-w-0 flex-1 items-center gap-1 rounded text-left text-foreground"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((was) => !was)}
        >
          <span className="min-w-0 truncate">{line.text}</span>
          <Icon
            name={open ? "ChevronUp" : "ChevronDown"}
            size={12}
            className="shrink-0 text-muted-foreground"
            aria-hidden
          />
          <span className="sr-only">
            {open ? "Hide the whole update" : "Show the whole update"}
          </span>
        </button>
        <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
          <PersonName email={line.author} className="max-w-40" />
          <span aria-hidden>·</span>
          <span>{line.date}</span>
        </span>
      </div>
      {open ? (
        <div
          id={panelId}
          className="mt-2 whitespace-pre-wrap break-words border-t border-border pt-2 text-xs text-foreground"
        >
          {line.full}
        </div>
      ) : null}
    </div>
  );
}
