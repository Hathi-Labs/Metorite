"use client";

/**
 * My Tasks · the company tree, read once (my_tasks_cutover.md §5 S6g).
 *
 * Three places in My Tasks choose a company project: the Move dialog, Clarify's
 * Where step and the capture chip. Before S6g the dialog read the TREE
 * (folders disabled) and Clarify read a FLAT list (folders pickable, and then
 * refused). They now read the same tree through `destinations`, so the three
 * cannot disagree about which rows are pickable.
 *
 * The tree is read once per page and shared. `refresh` reads it again, for a
 * dialog that opens after a project was created elsewhere.
 */

import { useEffect, useState } from "react";

import { projectsApi } from "@/app/projects/lib/api";
import { type DestinationRow, destinations } from "@/app/projects/lib/destinations";
import type { ProjectNode } from "@/app/projects/lib/tree";

import type { LensArea } from "./api";
import type { CaptureDestination } from "./quickAdd";
import type { MyTasksProject } from "./types";

let cached: Promise<ProjectNode[]> | null = null;

/** The tree, from the one read. A failed read is not cached. */
export function loadCompanyTree(refresh = false): Promise<ProjectNode[]> {
  if (!cached || refresh) {
    const next = projectsApi.tree().then((res) => res.rows as ProjectNode[]);
    cached = next;
    next.catch(() => {
      if (cached === next) cached = null;
    });
  }
  return cached;
}

/** The tree as React state. `enabled: false` reads nothing (the demo backend). */
export function useCompanyTree(enabled = true, refresh = false): {
  roots: ProjectNode[];
  error: string | null;
} {
  const [roots, setRoots] = useState<ProjectNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    loadCompanyTree(refresh).then(
      (rows) => {
        if (live) setRoots(rows);
      },
      (err: unknown) => {
        if (live) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      live = false;
    };
  }, [enabled, refresh]);
  return { roots, error };
}

/**
 * Every place a capture can land, Areas first (they stay private), then the
 * company tree's pickable rows. With no tree (the demo backend, or a failed
 * read) the store's flat company list stands in.
 */
export function captureDestinations(input: {
  areas: readonly LensArea[];
  tree: readonly DestinationRow[];
  projects: readonly MyTasksProject[];
}): CaptureDestination[] {
  const areas = input.areas
    .filter((a) => !a.archived)
    .map((a) => ({ id: a.id, name: a.name, kind: "area" as const }));
  const company = input.tree.length
    ? input.tree
        .filter((r) => r.legal)
        .map((r) => ({ id: r.node.id, name: r.node.name, kind: "project" as const }))
    : input.projects
        .filter((p) => p.status === undefined || p.status === "ACTIVE")
        .map((p) => ({ id: p.id, name: p.outcome, kind: "project" as const }));
  return [...areas, ...company];
}

export { destinations };
export type { DestinationRow };
