"use client";

/**
 * Settings → Change history (WS-CRM CRM-M1, crm_conversational.md §8).
 *
 * One card for an admin, in Native and Mirror mode. It lists each change of
 * the CRM set-up, newest first: when, who, through what, a one-line summary,
 * and each field before → after. The read is `GET /crm/change-history`
 * through `lib/api.ts`, cached by `useCachedResource`, so a revisit paints at
 * once. Loading, failed and empty each have their own words, in the one
 * state row of the CRM settings (`STATE_ROW`, CRM-U17a).
 *
 * ⚠️ No Undo button in PR 1. `lib/changeHistory.ts` says why.
 *
 * The page draws this card for the section `change-history`. It is not a
 * card of `PipelineSettings.tsx`.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { useCachedResource } from "@/lib/useCachedResource";
import { useState } from "react";
import * as api from "../lib/api";
import { CrmError, TRY_AGAIN } from "../lib/api";
import {
  CHANGE_HISTORY_EMPTY,
  CHANGE_HISTORY_LOADING,
  CHANGE_HISTORY_NOTE,
  CHANGE_HISTORY_TITLE,
  CHANGE_STATE_WORDS,
  changeLines,
  changeState,
  listState,
  moreLine,
  viaLabel,
  whoLabel,
  type ChangeList,
  type ChangeRow,
} from "../lib/changeHistory";
import { dateTime } from "../lib/format";
import { Card, STATE_ROW } from "./SettingsCards";

/** The cache key of the first page. */
const KEY = "crm:change-history";

const STATE_TONES = { undone: "neutral", undo: "info", final: "warning" } as const;

/** One change: the summary, then when, who and through what, then the fields. */
export function ChangeItem({ row }: { row: ChangeRow }) {
  const state = changeState(row);
  const lines = changeLines(row);
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-start gap-2">
        <p className="min-w-0 flex-1 text-sm text-foreground">{row.summary}</p>
        {state && (
          <Badge tone={STATE_TONES[state]} size="sm">
            {CHANGE_STATE_WORDS[state]}
          </Badge>
        )}
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {dateTime(row.at)} · {whoLabel(row)} · {viaLabel(row.via)} · {row.area_label}
      </p>
      {lines.length > 0 && (
        <dl className="mt-1.5 space-y-0.5 text-xs">
          {lines.map((line) => (
            <div key={line.key} className="flex flex-wrap gap-x-1.5">
              <dt className="text-muted-foreground">{line.label}:</dt>
              <dd className="min-w-0 text-foreground">
                {line.before !== null && (
                  <>
                    <span className="text-muted-foreground">{line.before}</span>
                    <span aria-hidden="true"> → </span>
                    <span className="sr-only"> changed to </span>
                  </>
                )}
                <span>{line.after}</span>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

/** The card, drawn from what the caller already holds. A test renders it. */
export function ChangeHistoryView({
  data,
  loading,
  error,
  onRetry,
  extra = [],
  onMore,
  loadingMore = false,
  moreError = null,
}: {
  data?: ChangeList | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** The rows of the pages after the first, in order. */
  extra?: ChangeRow[];
  onMore?: () => void;
  loadingMore?: boolean;
  moreError?: string | null;
}) {
  const state = listState({ data, loading, error });
  const rows = [...(data?.rows ?? []), ...extra];
  const more = data ? moreLine(rows.length, data.total) : null;
  return (
    <Card title={CHANGE_HISTORY_TITLE} note={CHANGE_HISTORY_NOTE}>
      {state === "loading" && (
        <p className={STATE_ROW} role="status">
          {CHANGE_HISTORY_LOADING}
        </p>
      )}
      {state === "failed" && (
        <div className={STATE_ROW} role="alert">
          <span className="text-destructive">{error || TRY_AGAIN}</span>
          <Button size="sm" variant="secondary" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
      {state === "empty" && <p className={STATE_ROW}>{CHANGE_HISTORY_EMPTY}</p>}
      {state === "rows" && (
        <>
          <ol className="divide-y divide-border/60" aria-label="Changes, newest first">
            {rows.map((row) => (
              <ChangeItem key={row.id} row={row} />
            ))}
          </ol>
          {more && (
            <div className="flex flex-wrap items-center gap-2 border-t border-border/60 px-4 py-3 text-xs text-muted-foreground">
              <span>{more}</span>
              {onMore && (
                <Button size="sm" variant="secondary" loading={loadingMore} onClick={onMore}>
                  Show more
                </Button>
              )}
              {moreError && (
                <span role="alert" className="basis-full text-destructive">
                  {moreError}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

/** Settings → Change history. Admin only: the page draws it for an admin. */
export default function ChangeHistoryCard() {
  const res = useCachedResource<ChangeList>(KEY, () => api.listChanges(1));
  const [extra, setExtra] = useState<ChangeRow[]>([]);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  const showMore = async () => {
    setLoadingMore(true);
    setMoreError(null);
    try {
      const next = await api.listChanges(page + 1);
      const seen = new Set([...(res.data?.rows ?? []), ...extra].map((r) => r.id));
      setExtra([...extra, ...next.rows.filter((r) => !seen.has(r.id))]);
      setPage(page + 1);
    } catch (err) {
      setMoreError(err instanceof CrmError ? err.message : TRY_AGAIN);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
      <ChangeHistoryView
        data={res.data}
        loading={res.loading}
        error={res.error}
        onRetry={res.refresh}
        extra={extra}
        onMore={() => void showMore()}
        loadingMore={loadingMore}
        moreError={moreError}
      />
    </div>
  );
}
