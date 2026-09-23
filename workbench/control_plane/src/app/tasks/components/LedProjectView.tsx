"use client";

/**
 * My Tasks · a project I lead (WS-39 S6e, my_tasks_cutover.md §4.8 point 1).
 *
 * Owner directive, 2026-09-23: *"When a project is set up for a particular
 * person and I am that person, it should show up in my tasks in an
 * appropriate way."* The lead is `pm_projects.lead`. A project I lead with
 * no task assigned to me is invisible through the one membership fragment,
 * and it is the project I answer for — so `/my/led` lists it, and this is
 * where it shows.
 *
 * What it draws, in this order: the project and how much open work it holds
 * (everybody's), then MY open tasks in it first — the same rows the store
 * holds, drawn through the same `TaskCard`, so completing one here completes
 * it everywhere — then the way to the board. Project management stays in
 * Projects; this surface is the personal lens on a project, not a second
 * board.
 *
 * The board link is `/projects`. There is no `?project=` deep link to copy
 * (`projectMenu.ts` records that), and inventing one here would be a second
 * spelling; a task row's own project label opens the board WITH that task
 * (`ProjectLabel`), which is the sharper link where one exists.
 */

import Link from "next/link";
import { useMemo } from "react";

import AppIcon from "@/components/Icon";

import { useTaskStore } from "../lib/taskStore";
import { TaskCard } from "./TaskCard";

export function LedProjectView() {
  const items = useTaskStore((s) => s.items);
  const ledProjects = useTaskStore((s) => s.ledProjects);
  const selectedLedProjectId = useTaskStore((s) => s.selectedLedProjectId);
  const project = ledProjects.find((p) => p.id === selectedLedProjectId) ?? ledProjects[0];

  // My open tasks in this project, from the STORE where they exist (so a
  // completion here is the completion everywhere), and from the led read's
  // own rows for the ones the store has not loaded.
  const mine = useMemo(() => {
    if (!project) return [];
    const live = items.filter(
      (i) =>
        i.projectId === project.id &&
        i.isMine &&
        !i.archivedAt &&
        i.disposition !== "DONE" &&
        i.disposition !== "TRASH",
    );
    const seen = new Set(live.map((i) => i.id));
    return [...live, ...project.myTasks.filter((t) => !seen.has(t.id))];
  }, [items, project]);

  if (!project) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <AppIcon name="FolderKanban" className="h-8 w-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          You lead no project yet. When somebody names you lead of one, it lists here.
        </p>
      </div>
    );
  }

  const others = Math.max(0, project.openTasks - mine.length);

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="shrink-0 border-b border-border bg-card px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <AppIcon name="FolderKanban" className="h-4 w-4 shrink-0 text-primary" />
          <h2 className="text-base font-bold text-foreground">{project.name}</h2>
          <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-semibold text-primary">
            You lead this project
          </span>
          <Link
            href="/projects"
            className="tech-transition ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
          >
            Open the board
            <AppIcon name="ArrowRight" className="h-3 w-3" />
          </Link>
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {project.openTasks} open {project.openTasks === 1 ? "task" : "tasks"} ·{" "}
          {mine.length} assigned to you
        </p>
      </div>

      <div className="flex-1 overflow-y-auto">
        <section aria-label="Assigned to me">
          <div className="flex items-center gap-2 px-4 pb-1 pt-3">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Assigned to me
            </p>
            <span className="text-[10px] text-muted-foreground">{mine.length}</span>
          </div>
          {mine.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted-foreground">
              Nothing in this project is on your plate. The board holds the rest.
            </p>
          ) : (
            mine.map((item) => (
              <div key={item.id} className="border-b border-border">
                <TaskCard item={item} variant="row" />
              </div>
            ))
          )}
        </section>

        <section aria-label="Everybody's open work" className="px-4 py-4">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            The rest of the project
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {others === 0
              ? "Nothing else is open."
              : `${others} more open ${others === 1 ? "task" : "tasks"} on the board, assigned to others or to nobody.`}{" "}
            <Link
              href="/projects"
              className="tech-transition font-medium text-primary hover:underline"
            >
              Manage them in Projects
            </Link>
            .
          </p>
        </section>
      </div>
    </div>
  );
}
