// ── What the CRM shows when a view throws (WS-53 CRM-Q3, spec section 18.6) ─
//
// On 2026-10-11 the owner opened the settings view and the CRM stopped. A local
// production build did not reproduce it, and the gateway logged no error. The
// likely cause is a browser tab that stayed open across several deploys and
// held script files that no longer exist. Whatever the cause, a thrown render
// must end on a sentence and a button, not on a white page.
//
// The decision is pure so a test can hold it: which words, for which error.
// The stale-chunk test is the shell's own (`isChunkLoadError`). The CRM uses it
// and does not carry a second copy.

import { isChunkLoadError } from "@/lib/shell/chunkReload";

export const BOUNDARY_COPY = {
  failed: "Something went wrong on this page. Reload to get the newest version.",
  updated: "Metorite was just updated. Reload to get the newest version.",
  button: "Reload",
} as const;

/**
 * What resets the boundary. A switch of view, of list or board, of pipeline
 * or of settings section mounts a fresh subtree, so a failure on one does not
 * stick to the next.
 */
export function boundaryKey(
  view: { tab: string; layout: string; module?: string | null; section?: string | null },
  pipelineId: string | null,
): string {
  // Each generic module is a view of its own (CRM-Q6b), and so is each
  // settings section (CRM-C1). Only a module tab has a module, and only the
  // settings tab has a section.
  const tab = view.module
    ? `${view.tab}:${view.module}`
    : view.section
      ? `${view.tab}:${view.section}`
      : view.tab;
  return `${tab}:${view.layout}${pipelineId ? `:${pipelineId}` : ""}`;
}

/** The sentence for this error. A stale tab gets the "updated" words. */
export function boundaryMessage(error: unknown): string {
  return isChunkLoadError(error) ? BOUNDARY_COPY.updated : BOUNDARY_COPY.failed;
}
