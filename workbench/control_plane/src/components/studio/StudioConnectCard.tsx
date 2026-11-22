"use client";

/**
 * StudioConnectCard — the Connect lines of a saved draft, in the chat (STU-I8d,
 * `project-docs/specs/ai_studio.md` §14 row I8d).
 *
 * Interaction: Mira saves a draft that names a connection the member has not made. The chat shows one
 * card with a line for each name. Connect opens the sheet of I8a, and its button opens the connect page
 * of the owning app, with the way back to the IDE of this app. The member never types a key here: the
 * card has no field, and Mira never asks for one.
 *
 * `StudioToolCards` mounts one card for each save (`connectCalls`). The read is the one of the IDE
 * (`getConnections`, in the same cache key). A 404 (a flag is off, or the member is not the owner)
 * draws nothing. A card with no unmet name draws nothing. Every rule lives in
 * `lib/studio/connectCard.ts` and `lib/studio/connect.ts`.
 */

import { ConnectLines } from "@/components/studio/ConnectSheet";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import { type ConnectCall } from "@/lib/studio/connectCard";
import { connectPanel, connectionsKey, getConnections, unmetCount, type ConnectionRow } from "@/lib/studio/connect";
import { useCachedResource } from "@/lib/useCachedResource";

const SLOT_CLASS = "mt-3 min-w-0";

/** The card of ONE saved package. */
export function StudioConnectCard({ call }: { call: ConnectCall }) {
  const conns = useCachedResource<ConnectionRow[]>(
    connectionsKey(call.packageId), () => getConnections(call.packageId), { revalidateOnFocus: true },
  );
  const rows = conns.data ?? [];
  // A read that worked and found nothing unmet: no card (a 404 gives no row).
  if (conns.error === null && unmetCount(rows) === 0) return null;
  const panel = connectPanel(conns.data, conns.error, call.packageId);
  const title = panel.missing ?? "Connections";
  return (
    <div className={SLOT_CLASS} data-studio-connect={call.packageId}>
      <RollupCard id={`studio-connect:${call.eventId}`} title={title} icon="Plug" header="own">
        <section aria-label={title} className={CARD_FRAME_CLASS}>
          <RollupHeader />
          <RollupBody className="pt-1.5">
            <ConnectLines lines={panel.lines} failure={panel.failure} onRetry={conns.refresh} retrying={conns.loading || conns.refreshing} />
          </RollupBody>
        </section>
      </RollupCard>
    </div>
  );
}
