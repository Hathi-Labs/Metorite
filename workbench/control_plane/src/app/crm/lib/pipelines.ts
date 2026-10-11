// ── The deal pipelines (WS-53 CRM-Q3) ─────────────────────────────────────
//
// A pipeline is a property of a Deal, not a third object beside Leads and
// Deals (spec crm_platform.md section 18.1). The Deals view holds the board
// and the list, and a picker chooses the pipeline when there are two or more.
//
// ⚠️ `GET /crm/pipelines` comes from CRM-Q2. Until a gateway serves it, the
// read answers 404 and the CRM has ONE implicit pipeline: no picker, and no
// `pipeline_id` on any request. `usePipelines` turns every failure into that
// state, because a board that fails to open over a picker is worse than a
// board with no picker.
//
// Pure, so the rules have tests without a browser.

import type { DealPipeline } from "./types";

/** Where the last chosen pipeline is kept, per browser profile. */
export const REMEMBER_KEY = "metorite:crm:pipeline";

/** The picker shows for two pipelines or more. One pipeline needs no choice. */
export function showPicker(pipelines: readonly DealPipeline[] | null | undefined): boolean {
  return !!pipelines && pipelines.length >= 2;
}

/** Pipelines in their stated order. The gateway sorts them, and so do we. */
export function orderedPipelines(pipelines: readonly DealPipeline[]): DealPipeline[] {
  return [...pipelines].sort((a, b) => a.position - b.position);
}

/**
 * The pipeline a request filters by, or null for "no filter".
 *
 * Null with fewer than two pipelines: the gateway then behaves as it did
 * before, and a stale remembered id cannot filter a board to nothing. With
 * two or more it is the URL's id, else the remembered id, else the default.
 * An id that names no pipeline falls through to the next source.
 */
export function resolvePipeline(
  pipelines: readonly DealPipeline[] | null | undefined,
  urlId: string | null,
  rememberedId: string | null,
): string | null {
  if (!showPicker(pipelines) || !pipelines) return null;
  const known = (id: string | null) => (id ? pipelines.find((p) => p.id === id) : undefined);
  const chosen = known(urlId) ?? known(rememberedId);
  if (chosen) return chosen.id;
  const fallback = pipelines.find((p) => p.is_default) ?? orderedPipelines(pipelines)[0];
  return fallback ? fallback.id : null;
}

type Store = Pick<Storage, "getItem" | "setItem">;

/** The browser's local store, or null where reading it throws. */
export function localStore(): Store | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** The pipeline this browser chose last, or null. Never throws. */
export function readRemembered(store: Store | null = localStore()): string | null {
  try {
    return store?.getItem(REMEMBER_KEY) || null;
  } catch {
    return null;
  }
}

/** Keep the choice. A blocked store loses it and nothing else. */
export function remember(id: string, store: Store | null = localStore()): void {
  try {
    store?.setItem(REMEMBER_KEY, id);
  } catch {
    // Private window or blocked site data: the choice lasts until the next visit.
  }
}

/** The stages of one pipeline, in order. */
export function stagesOf(pipeline: DealPipeline) {
  return [...pipeline.stages].sort((a, b) => a.position - b.position);
}
