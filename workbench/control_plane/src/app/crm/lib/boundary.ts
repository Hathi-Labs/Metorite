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

/** The sentence for this error. A stale tab gets the "updated" words. */
export function boundaryMessage(error: unknown): string {
  return isChunkLoadError(error) ? BOUNDARY_COPY.updated : BOUNDARY_COPY.failed;
}
