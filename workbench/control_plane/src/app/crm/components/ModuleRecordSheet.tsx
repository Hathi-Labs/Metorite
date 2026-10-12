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
 * - The tabs: Overview and Fields, and Related only when a lookup holds a
 *   value. No timeline: a generic record has none.
 * - A lookup opens the record page of its target: a known record (a deal, a
 *   contact) or another generic record. The gateway resolves the link. A
 *   lookup with no link stays text.
 *
 * Read only, in every mode. The sync is the only writer of a generic record.
 * `?mrec=<Module>:<id>` opens it, and `?rtab=` holds the tab.
 */

import Button from "@/components/ui/Button";
import { useCachedResource } from "@/lib/useCachedResource";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { zohoLink } from "../lib/connect";
import {
  MODULE_NOT_FOUND,
  OVERVIEW_FIELDS,
  linkText,
  moduleFacts,
  moduleFieldRows,
  moduleRelated,
  moduleSheetState,
  moduleTabs,
  moduleTitle,
  sheetTitle,
  shownModuleTab,
  type ModuleFieldRow,
  type ModuleLink,
  type ModuleRecordPage,
  type ModuleTab,
} from "../lib/modules";
import FieldValue from "./FieldValue";
import { RecordFacts, RecordModal, RecordTabBar } from "./RecordShell";

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
        <ModuleRecordBody page={state.page} tab={tab} onTab={onTab} onOpenLink={onOpenLink} />
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
}: {
  page: ModuleRecordPage;
  tab: string;
  onTab: (tab: ModuleTab) => void;
  onOpenLink: (link: ModuleLink) => void;
}) {
  const shown = shownModuleTab(page, tab);
  const rows = moduleFieldRows(page);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-3">
        <RecordFacts facts={moduleFacts(page.record)} />
      </div>

      <RecordTabBar tabs={moduleTabs(page)} active={shown} onTab={onTab} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        {shown === "related" ? (
          <FieldList rows={moduleRelated(page)} empty="No linked records." onOpenLink={onOpenLink} />
        ) : shown === "fields" ? (
          <FieldList rows={rows} empty="No fields on this record." onOpenLink={onOpenLink} />
        ) : (
          <>
            <FieldList
              rows={rows.slice(0, OVERVIEW_FIELDS)}
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
