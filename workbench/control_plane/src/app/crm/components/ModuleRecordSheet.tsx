"use client";

/**
 * The record page of a generic Zoho module (WS-53 CRM-Q6b, `crm_platform.md`
 * section 18.7): a Product, a Quote, a record of a custom module.
 *
 * It is the CRM-X4 record page, not a second one. `RecordShell` gives the
 * drawer with "Open in Zoho", the facts of the header and the tabs, and
 * `FieldValue` draws each value by the CRM-Q4 rules of `fields.ts`.
 *
 * - The header: the name as the title, the owner and the modified date.
 * - The tabs: Overview, Timeline and Fields, and Related only when a lookup
 *   holds a value. Since CRM-Q23c the Timeline tab shows the activities that
 *   the sync links to the record (`crm_platform.md` section 18.10), read
 *   only: `Timeline` in its `readOnly` mode, with no composer.
 * - A lookup opens the record page of its target: a known record (a deal, a
 *   contact) or another generic record. The gateway resolves the link. A
 *   lookup with no link stays text.
 *
 * Read only, in every mode. The sync is the only writer of a generic record.
 * `?mrec=<Module>:<id>` opens it, and `?rtab=` holds the tab.
 *
 * The fill rank (WS-53 CRM-Q17a-2 items 5 and 6). While the field
 * definitions carry fill numbers, Overview draws the filled fields first, by
 * fill, and the Fields tab has the filter box and the fold of the known
 * record's Fields tab (`FoldedFields`). The Zoho system keys never show.
 */

import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { zohoLink } from "../lib/connect";
import { fillRankOn, filledFirst } from "../lib/fields";
import {
  MODULE_NOT_FOUND,
  OVERVIEW_FIELDS,
  linkText,
  moduleFacts,
  moduleFieldRows,
  moduleRelated,
  moduleSheetState,
  moduleTabs,
  moduleTimelineKey,
  moduleTimelineState,
  moduleTitle,
  sheetTitle,
  shownModuleTab,
  type ModuleFieldRow,
  type ModuleLink,
  type ModuleRecordPage,
  type ModuleTab,
  type ModuleTimelineState,
} from "../lib/modules";
import type { TimelineEntry } from "../lib/types";
import FieldValue from "./FieldValue";
import { FoldedFields } from "./FieldsPanel";
import { RecordFacts, RecordModal, RecordTabBar } from "./RecordShell";
import Timeline from "./Timeline";

export default function ModuleRecordSheet({
  module,
  id,
  tab,
  onTab,
  onClose,
  onOpenLink,
}: {
  module: string;
  id: string;
  /** The tab of `?rtab=`. A tab this page does not have opens Overview. */
  tab: string;
  onTab: (tab: ModuleTab) => void;
  onClose: () => void;
  /** A lookup link: a known record, or another generic record. */
  onOpenLink: (link: ModuleLink) => void;
}) {
  // A 404 is an answer ("not in the mirror"), so it becomes data. Any other
  // failure stays an error, and the sheet offers a retry.
  const res = useCachedResource<ModuleRecordPage | typeof MODULE_NOT_FOUND>(
    `crm:module:${module}:${id}`,
    () =>
      api.getModuleRecord(module, id).catch((err: unknown) => {
        if (err instanceof CrmError && err.status === 404) return MODULE_NOT_FOUND;
        throw err;
      }),
  );
  const state = moduleSheetState(res.data, res.error, id);
  const page = state.kind === "page" ? state.page : null;
  // CRM-Q23c: the timeline is read only when its tab is on screen.
  const wantsTimeline = page !== null && shownModuleTab(page, tab) === "timeline";
  const stream = useCachedResource<{ entries: TimelineEntry[] }>(
    wantsTimeline ? moduleTimelineKey(module, id) : null,
    () => api.getModuleTimeline(module, id),
  );
  const timeline = moduleTimelineState(stream.data, stream.error);
  const noun = page?.module.singular_label || page?.module.label || module;
  return (
    <RecordModal
      title={
        state.kind === "page"
          ? moduleTitle(state.page.record, state.page.module.singular_label)
          : sheetTitle(state)
      }
      noun={noun}
      zoho={page ? zohoLink(page.record) : null}
      onClose={onClose}
    >
      {state.kind === "page" ? (
        <ModuleRecordBody
          page={state.page}
          tab={tab}
          onTab={onTab}
          onOpenLink={onOpenLink}
          timeline={timeline}
          onRetryTimeline={stream.refresh}
        />
      ) : state.kind === "failed" ? (
        <div className="flex flex-col items-center gap-3 p-8 text-center">
          <p className="text-xs text-muted-foreground">This record did not load. Try again.</p>
          <Button variant="secondary" size="sm" icon="RefreshCw" onClick={res.refresh}>
            Retry
          </Button>
        </div>
      ) : (
        <p className="p-8 text-center text-xs text-muted-foreground">
          {state.kind === "missing" ? "This record is not in the mirror." : "Loading…"}
        </p>
      )}
    </RecordModal>
  );
}

/** The body of the page: the header, the tabs and the tab on screen. Props only. */
export function ModuleRecordBody({
  page,
  tab,
  onTab,
  onOpenLink,
  timeline = { kind: "loading" },
  onRetryTimeline = () => undefined,
}: {
  page: ModuleRecordPage;
  tab: string;
  onTab: (tab: ModuleTab) => void;
  onOpenLink: (link: ModuleLink) => void;
  /** CRM-Q23c: the read of the Timeline tab. */
  timeline?: ModuleTimelineState;
  onRetryTimeline?: () => void;
}) {
  const shown = shownModuleTab(page, tab);
  const rows = moduleFieldRows(page);
  const ranked = fillRankOn(page.field_defs);
  // CRM-Q17a-2 item 5: Overview draws the filled fields first, by fill.
  const defs = new Map(page.field_defs.map((d) => [d.key, d]));
  const overview = ranked
    ? filledFirst(rows, (row) => row.value.kind !== "empty", (row) => defs.get(row.key))
    : rows;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-3">
        <RecordFacts facts={moduleFacts(page.record)} />
      </div>

      <RecordTabBar tabs={moduleTabs(page)} active={shown} onTab={onTab} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        {shown === "timeline" ? (
          <ModuleTimeline state={timeline} onRetry={onRetryTimeline} />
        ) : shown === "related" ? (
          <FieldList rows={moduleRelated(page)} empty="No linked records." onOpenLink={onOpenLink} />
        ) : shown === "fields" ? (
          ranked ? (
            <FoldedFields
              rows={rows}
              emptyText="No fields on this record."
              renderRows={(shownRows) => (
                <FieldList rows={shownRows} empty="No fields on this record." onOpenLink={onOpenLink} />
              )}
            />
          ) : (
            <FieldList rows={rows} empty="No fields on this record." onOpenLink={onOpenLink} />
          )
        ) : (
          <>
            <FieldList
              rows={overview.slice(0, OVERVIEW_FIELDS)}
              empty="No fields on this record."
              onOpenLink={onOpenLink}
            />
            {rows.length > OVERVIEW_FIELDS && (
              <p className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
                {rows.length - OVERVIEW_FIELDS} more in{" "}
                <button
                  type="button"
                  onClick={() => onTab("fields")}
                  className="text-primary underline underline-offset-2"
                >
                  Fields
                </button>
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The Timeline tab of a module record (CRM-Q23c): the shared `Timeline` in
 * its read-only mode. A wait draws a skeleton, and a failure offers a retry.
 */
export function ModuleTimeline({
  state,
  onRetry,
}: {
  state: ModuleTimelineState;
  onRetry: () => void;
}) {
  if (state.kind === "entries") {
    return <Timeline entries={state.entries} readOnly parentMirrored />;
  }
  if (state.kind === "failed") {
    return (
      <div className="flex flex-col items-center gap-3 p-8 text-center">
        <p className="text-xs text-muted-foreground">The timeline did not load. Try again.</p>
        <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-2 p-4" aria-busy="true">
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-4 w-3/5" />
    </div>
  );
}

/** The rows of the Fields tab, in the look of the known record's Fields tab. */
function FieldList({
  rows,
  empty,
  onOpenLink,
}: {
  rows: ModuleFieldRow[];
  empty: string;
  onOpenLink: (link: ModuleLink) => void;
}) {
  if (rows.length === 0) {
    return <p className="px-4 py-8 text-center text-xs text-muted-foreground">{empty}</p>;
  }
  return (
    <dl className="divide-y divide-border/60">
      {rows.map((row) => (
        <div key={row.key} className="flex items-start gap-3 px-4 py-2">
          <dt className="w-28 shrink-0 pt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            {row.label}
          </dt>
          <dd className="min-w-0 flex-1 break-words text-xs text-foreground">
            {row.link ? (
              <button
                type="button"
                onClick={() => onOpenLink(row.link!)}
                className="text-left text-primary underline underline-offset-2"
              >
                {linkText(row)}
              </button>
            ) : (
              <FieldValue value={row.value} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
