/**
 * Which file cards a chat message draws (spec `projects_ai_chat.md` §14.10).
 *
 * Each `artifact_created` or `artifact_updated` event of a message is one file
 * card, one per path (last write wins). ONE exception: an event that the
 * server marked `"quiet": true`. Its file has a card of its own already, for
 * example the PDF and the `.report.json` behind a report card
 * (`write_artifact._notify(announce=False)`). The message draws no second
 * card for it. The chat's Files tree still lists it, through `onArtifact` and
 * `mergeFileEntry`, which read every file event.
 *
 * Fence: `artifactCards.test.ts`.
 */
import type { ArtifactMeta } from "@/components/ArtifactCard";

/** The shape of one stored custom event, as a message keeps it. */
export interface ArtifactEventLike {
  name: string;
  value?: unknown;
}

/** True only for a file event that the server marked quiet. */
export function isQuietArtifact(value: unknown): boolean {
  return (
    value !== null &&
    typeof value === "object" &&
    (value as Record<string, unknown>).quiet === true
  );
}

/** The file cards of one message: one per path, and none for a quiet file. */
export function fileCardsOf(events: readonly ArtifactEventLike[] | undefined): ArtifactMeta[] {
  const byPath = new Map<string, ArtifactMeta>();
  for (const e of events ?? []) {
    if (e.name !== "artifact_created" && e.name !== "artifact_updated") continue;
    if (!e.value || typeof e.value !== "object" || isQuietArtifact(e.value)) continue;
    const v = e.value as Record<string, unknown>;
    const path = String(v.path ?? "");
    byPath.set(path, {
      path,
      name: path.split("/").pop() ?? path,
      size: typeof v.size === "number" ? v.size : undefined,
      mimeType: typeof v.mime_type === "string" ? v.mime_type : undefined,
      sha256: typeof v.sha256 === "string" ? v.sha256 : undefined,
    } satisfies ArtifactMeta);
  }
  return [...byPath.values()];
}
