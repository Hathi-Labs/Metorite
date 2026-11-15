"use client";

/**
 * The Activity tab of a Studio record (STU-C4b, `ai_studio.md` §14): the changes of one record, with
 * the actor, the time and the old and new value of each key. It draws what the model
 * (`lib/studio/historyFeed.ts`) gives, and it holds no rule of its own.
 *
 * ⚠️ THE READ. The page reads through the door only: `door.read` with `object:<name>.history`
 * (C4a-2). The door applies the checks of a get, the row rules of the viewer and the hide of C8, and
 * it drops each key that the viewer may not read. This file never reads the record, the change
 * table or a run to fill a key. A line exists only for a key that the door sent.
 *
 * The door lists the changes oldest first. "Load more" sends the cursor `next` and adds the later
 * changes. The four states (loading, empty, error with Retry) come from `StudioBlockState`.
 * Fence: `StudioActivity.test.ts`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import StudioBlockState from "@/components/studio/StudioBlockState";
import Button from "@/components/ui/Button";
import { type StudioDataDoor, sourceKey } from "@/lib/studio/dataDoor";
import { fieldSpecs } from "@/lib/studio/packagePreview";
import {
  type HistoryEntry, type HistoryPage, historySource, joinPages, parseHistory,
} from "@/lib/studio/historyFeed";
import type { ObjectRow } from "@/lib/studio/realDoor";
import { useCachedResource } from "@/lib/useCachedResource";

export interface StudioActivityProps {
  object: ObjectRow;
  recordId: string;
  door: StudioDataDoor;
  /** False when the app declares no `object:<name>.history`. The tab is then not drawn. */
  declared: boolean;
}

const NOTE = "text-xs text-muted-foreground";
const NOUN = "changes";

/** One change: who, what, when, and a line for each key. */
export function ActivityEntry({ entry }: { entry: HistoryEntry }) {
  return (
    <li data-change={entry.id} data-op={entry.op} className="rounded-lg border border-border bg-card px-3 py-2">
      <p className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs">
        <span className="min-w-0 break-words font-medium text-foreground">
          {entry.opWord} <span className="font-normal text-muted-foreground">by</span> {entry.who}
        </span>
        {entry.at ? (
          <time dateTime={entry.at} className="text-muted-foreground">{entry.atText}</time>
        ) : null}
      </p>
      {entry.lines.length ? (
        <dl className="mt-1.5 space-y-1 text-xs">
          {entry.lines.map((l) => (
            <div key={l.key} data-key={l.key} className="grid grid-cols-1 gap-x-3 sm:grid-cols-[max-content_1fr]">
              <dt className="font-medium text-muted-foreground">{l.label}</dt>
              <dd className="min-w-0 break-words text-foreground">
                {l.old !== null ? (
                  <>
                    <span className="text-muted-foreground">{l.old}</span>
                    <span aria-hidden="true"> → </span>
                    <span className="sr-only"> to </span>
                  </>
                ) : null}
                {l.next}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

export default function StudioActivity({ object, recordId, door, declared }: StudioActivityProps) {
  const fields = useMemo(() => fieldSpecs(object), [object]);
  const first = useMemo(() => historySource(object.name, recordId, null), [object.name, recordId]);
  const read = useCachedResource<Record<string, unknown>[]>(
    declared ? sourceKey(door, first) : null, () => door.read(first));
  const [later, setLater] = useState<HistoryPage[]>([]);
  const [busy, setBusy] = useState(false);
  const [moreError, setMoreError] = useState(false);

  // A new read of the first page (a write, a refresh) makes the later pages stale. Start over.
  useEffect(() => { setLater([]); setMoreError(false); }, [read.data, recordId]);

  const firstPage = useMemo(() => (read.data?.[0] ? parseHistory(read.data[0], fields) : null), [read.data, fields]);
  const page = useMemo(() => (firstPage ? joinPages(firstPage, later) : null), [firstPage, later]);

  const loadMore = useCallback(async () => {
    if (!page || page.next === null || busy) return;
    setBusy(true);
    setMoreError(false);
    try {
      const rows = await door.read(historySource(object.name, recordId, page.next));
      const more = rows[0] ? parseHistory(rows[0], fields) : null;
      if (more) setLater((p) => [...p, more]); else setMoreError(true);
    } catch {
      setMoreError(true);
    } finally {
      setBusy(false);
    }
  }, [page, busy, door, object.name, recordId, fields]);

  if (!declared) {
    return <p role="status" className={NOTE}>This app does not declare the history of {object.label ?? object.name}, so it cannot show the changes.</p>;
  }
  const failed = read.error !== null || (read.data !== undefined && firstPage === null);
  return (
    <section aria-label="Activity" className="min-w-0 space-y-2" data-activity={recordId}>
      <StudioBlockState
        loading={!page && !failed} error={failed ? read.error ?? "bad answer" : null} forbidden={false}
        rows={page?.entries.length ?? 0} filtersActive={false} noun={NOUN} onRetry={read.refresh} />
      {page && page.entries.length ? (
        <>
          <p className={NOTE}>Oldest first</p>
          <ol aria-label="Changes of this record" className="space-y-2">
            {page.entries.map((e) => <ActivityEntry key={e.id} entry={e} />)}
          </ol>
          {moreError ? (
            <p role="alert" className="text-xs text-destructive">The next changes did not load. Try again.</p>
          ) : null}
          {page.next !== null ? (
            <Button variant="secondary" size="sm" loading={busy} onClick={loadMore}>Load more</Button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
