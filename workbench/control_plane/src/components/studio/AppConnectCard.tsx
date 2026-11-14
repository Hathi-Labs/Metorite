"use client";

/**
 * The setup card of an app page (WS-STU I8c, `ai_studio.md` §14 I8c).
 *
 * Interaction: the owner opens an app whose draft names a connection that is not met. The card at the
 * top of the app lists each unmet name with its sentence and Connect. Connect opens the I8a sheet, and
 * its button sends the owner to the connect page of the owning app. After the connect, the owner
 * returns to this page and the line reads connected. A member who may not connect reads the one
 * sentence of the row, with no button.
 *
 * The rows come from the I8a route only (`getConnections`). That route answers the owner of the draft
 * and nobody else, and a 404 (a flag off, or not the owner) draws nothing. The card reads no claim of
 * the package, and it holds no input: a key is never typed here (§8.1).
 */
import { useRef } from "react";

import { ConnectLines } from "@/components/studio/ConnectSheet";
import {
  CONNECTIONS_READ_FAILED, connectLines, connectionsKey, getConnections, unmetCount, type ConnectionRow,
} from "@/lib/studio/connect";
import { previewHref } from "@/lib/studio/packagePreview";
import { useCachedResource } from "@/lib/useCachedResource";

export default function AppConnectCard({ pkg }: { pkg: string }) {
  const conns = useCachedResource<ConnectionRow[]>(connectionsKey(pkg), () => getConnections(pkg), { revalidateOnFocus: true });
  const rows = conns.data ?? [];
  // Once the card showed an unmet name, it stays while the owner is on the page, so a focus read can say "connected".
  const seenUnmet = useRef(false);
  if (unmetCount(rows) > 0) seenUnmet.current = true;
  const failed = conns.error !== null && conns.error !== undefined;
  if (!failed && !seenUnmet.current) return null;
  const lines = connectLines(rows, pkg, previewHref(pkg));
  return (
    <section aria-label="Connections of this app"
      className="shrink-0 rounded-xl border border-border bg-card p-3 sm:p-4">
      <h2 className="mb-2 text-sm font-semibold text-foreground">This app needs connections</h2>
      <ConnectLines lines={lines} failure={failed ? CONNECTIONS_READ_FAILED : null}
        onRetry={conns.refresh} retrying={conns.loading || conns.refreshing} />
    </section>
  );
}
