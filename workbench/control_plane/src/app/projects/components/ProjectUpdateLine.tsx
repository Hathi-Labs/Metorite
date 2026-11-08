"use client";

/**
 * WS-39 LP-1a — the newest update of a project, as one line on its Overview
 * (`project_management_app.md` §14.2a).
 *
 * A health chip, the first line of the text, the author and the date. It
 * draws what `lib/projectUpdateLine.ts` returns and decides nothing.
 *
 * It reads through the one cache (`useCachedResource`), so a revisit paints at
 * once. It draws NOTHING while the read waits, when the read fails, when
 * `PROJECTS_UPDATES_ORGS` leaves the org out (the route answers 404), and when
 * the project has no update. So with the flag off the Overview is unchanged.
 *
 * ⚠️ The hue comes from `projectHealthChip` in `src/lib/statusAccent.ts`, the
 * one status vocabulary. This file names no colour. The chip carries its words
 * and a glyph, so the health never depends on colour alone.
 */

import Icon from "@/components/Icon";
import { PersonName } from "@/components/PersonName";
import { BADGE_SHAPE } from "@/components/ui/Badge";
import { projectHealthChip } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import { ProjectsApiError, type ProjectUpdatesPage, projectsApi, projectsKey } from "../lib/api";
import { projectUpdateLine } from "../lib/projectUpdateLine";

/** The page that stands for "no line": the flag is off, or the project hides. */
const NO_UPDATES: ProjectUpdatesPage = { rows: [], next_before: null };

export function ProjectUpdateLine({ projectId }: { projectId: string }) {
  const latest = useCachedResource(
    projectsKey(`nodes/${projectId}/updates`, { limit: 1 }),
    () =>
      projectsApi.projectUpdates(projectId, 1).catch((err: unknown) => {
        // The 404 of the flag, or of a project this member cannot see. Both
        // mean "no line", and neither is an error the member can act on.
        if (err instanceof ProjectsApiError && err.status === 404) return NO_UPDATES;
        throw err;
      })
  );
  const line = projectUpdateLine(latest.data);
  if (!line) return null;
  return (
    <div
      data-project-update={line.health}
      className="mb-3 flex min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs"
    >
      <span
        className={`${BADGE_SHAPE} shrink-0 whitespace-nowrap px-1.5 py-0.5 text-[11px] font-medium ${projectHealthChip(line.health)}`}
      >
        <Icon name={line.icon} size={11} className="shrink-0" aria-hidden />
        {line.label}
      </span>
      <span className="min-w-0 flex-1 truncate text-foreground" title={line.full}>
        {line.text}
      </span>
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
        <PersonName email={line.author} className="max-w-40" />
        <span aria-hidden>·</span>
        <span>{line.date}</span>
      </span>
    </div>
  );
}
