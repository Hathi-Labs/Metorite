"use client";

/**
 * StudioRecordsCard — the live records of a Studio read, in the chat (STU-F5a,
 * `project-docs/specs/ai_studio.md` §14 row F5a).
 *
 * Mira reads the records of a package with `studio_record_list`. This card
 * draws the same read as a live table, after the other tool cards of the turn.
 * `StudioToolCards` mounts one card for each read (`recordsCalls`), and
 * `MessageBubble` mounts `StudioToolCards` once, so the card shows in `/chat`,
 * in the dock and on a phone.
 *
 * - **The reads.** The package through `GET /api/studio/packages/<id>`, then
 *   the list through the door of the card, both in the one read cache. A 404
 *   draws nothing.
 * - ⚠️ **The door is read-only for every member, the owner too** (`cardDoor`).
 *   The BFF signs every same-origin POST to the action route, so a full door
 *   in the chat would write with no approval card.
 * - **The table.** The `dataGrid` template on the bind path, with no
 *   `editable`. No Board, no Form, no button and no delete.
 *
 * Every rule lives in `lib/studio/recordsCard.ts`, and this file only wires it.
 * Fences: `StudioRecordsCard.test.ts` and `lib/chatCardConformance.test.ts`.
 */

import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import GenerativeUINode, { BoundStatus } from "@/components/GenerativeUINode";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Button from "@/components/ui/Button";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import { Skeleton } from "@/components/ui/Skeleton";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type BoundView, type Row, type StudioDataDoor, boundView, moreLine, sourceKey } from "@/lib/studio/dataDoor";
import { createDoorKeeper } from "@/lib/studio/realDoor";
import {
  cardDoor, emptyLine, packageFailure, packageKey, readCardPackage, recordsNode, recordsTitle, shownObject,
  type RecordsCall,
} from "@/lib/studio/recordsCard";
import { useCachedResource } from "@/lib/useCachedResource";

/** The space of one card in the turn, after the other tool cards. */
const SLOT_CLASS = "mt-3 min-w-0";
/** The icon of the card's own title row, the icon of the table template. */
const ICON = "Table2";

/**
 * The frame of a card that shows no rows yet: its title row, then the
 * loading bars, the sentence of a failed read once, or the empty state.
 */
export function RecordsFrame({
  id, title, view, error, empty, onRetry,
}: {
  id: string;
  title: string;
  view: Exclude<BoundView, "rows">;
  error: string | null;
  empty: string;
  onRetry: () => void;
}) {
  return (
    <RollupCard id={id} title={title} icon={ICON} header="own">
      <section aria-label={title} data-studio-records-state={view} className={CARD_FRAME_CLASS}>
        <RollupHeader />
        <RollupBody className="pt-1.5">
          {view === "empty"
            ? <EmptyState icon="Inbox" message={empty} />
            : <BoundStatus view={view} onRetry={onRetry} error={error} />}
        </RollupBody>
      </section>
    </RollupCard>
  );
}

/**
 * The frame of a package read that failed: one sentence, and "Try again" only
 * where a retry can help. A 401 says to sign in (`packageFailure`).
 */
export function PackageFailedFrame({
  id, title, error, onRetry,
}: { id: string; title: string; error: string | null; onRetry: () => void }) {
  const failed = packageFailure(error);
  return (
    <RollupCard id={id} title={title} icon={ICON} header="own">
      <section aria-label={title} data-studio-records-state="error" className={CARD_FRAME_CLASS}>
        <RollupHeader />
        <RollupBody className="flex flex-wrap items-center gap-2 pt-1.5">
          <p role="alert" className="text-xs text-destructive">{failed.sentence}</p>
          {failed.retry ? (
            <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
              Try again
            </Button>
          ) : null}
        </RollupBody>
      </section>
    </RollupCard>
  );
}

/** The card of ONE read of the records of a package. */
export function StudioRecordsCard({ call }: { call: RecordsCall }) {
  const pkg = useCachedResource(packageKey(call.packageId), () => readCardPackage(call.packageId));
  // One door while the actions stay the same: a focus read of the package
  // gives a new object, and the door keys the cache of the rows.
  const [keeper] = useState(() => createDoorKeeper((o) => cardDoor({ id: o.packageId, actions: o.actions })));
  const detail = pkg.data ?? null;
  const object = shownObject(detail, call.object);
  const door: StudioDataDoor | null = detail && object ? keeper(detail.id, detail.actions) : null;
  const rows = useCachedResource<Row[]>(
    door ? sourceKey(door, call.source) : null,
    () => (door as StudioDataDoor).read(call.source),
  );
  const title = detail && object ? recordsTitle(detail, object) : call.object;
  const node = object ? recordsNode(object, call.source, title) : null;
  const id = `studio-records:${call.eventId}`;

  // 404 or 403: Studio is off, the package is not the member's, or the member
  // may not use Studio. No card, and no empty space in the turn.
  if (pkg.data === null) return null;
  if (pkg.data === undefined) {
    if (!pkg.error) {
      return (
        <div className={SLOT_CLASS}>
          <Skeleton className="h-40 w-full" />
        </div>
      );
    }
    return (
      <div className={SLOT_CLASS} data-studio-records={call.key}>
        <PackageFailedFrame id={id} title={title} error={pkg.error} onRetry={pkg.refresh} />
      </div>
    );
  }
  // No such object for this member, or no list of it: no card.
  if (!object || !door || !node) return null;
  const view = boundView({ data: rows.data, loading: rows.loading, error: rows.error });
  if (view !== "rows") {
    return (
      <div className={SLOT_CLASS} data-studio-records={call.key}>
        <RecordsFrame id={id} title={title} view={view} error={rows.error} empty={emptyLine(call)}
          onRetry={rows.refresh} />
      </div>
    );
  }
  const more = moreLine(rows.data);
  return (
    <div className={SLOT_CLASS} data-studio-records={call.key}>
      <RollupCard id={id} title={title} header="own" clamp>
        <StudioDataContext.Provider value={door}>
          <GenerativeUINode spec={node} />
        </StudioDataContext.Provider>
      </RollupCard>
      {more ? <p role="status" className="mt-1 text-xs text-muted-foreground">{more}</p> : null}
    </div>
  );
}
