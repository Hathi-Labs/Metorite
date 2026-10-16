"use client";

/**
 * The record sheet — opened OVER the list, never as its own route
 * (specs/crm_app.md §5 surface 3).
 *
 * The URL carries `?deal=<id>`, so Back closes the sheet instead of leaving
 * the app and a link to a record carries the list the sender was looking at.
 * That decision lives in ../lib/urlState.ts; this is its surface.
 *
 * Record page v2 (WS-53 CRM-X4, `crm_platform.md` section 19.8):
 *
 * - A summary header under the title: the stage or the status as the one
 *   `StatusPill`, and the facts of `summaryFacts`.
 * - Four tabs, kept in `?rtab=`: Overview, Timeline, Related and Fields. A
 *   lead has no Related tab.
 * - `j` and `k` step to the next and the previous record of the list the
 *   member came from. `page.tsx` passes that order, or null when no list is
 *   behind the record, and then the keys do nothing and the arrows hide.
 *
 * `RecordSheet` holds the hooks, the reads and the keys. `RecordPage` draws
 * the body from props only, so a test renders it without a DOM.
 */

import Button from "@/components/ui/Button";
import { useCachedResource } from "@/lib/useCachedResource";
import { useEffect } from "react";
import * as api from "../lib/api";
import { isMirrored, zohoLink } from "../lib/connect";
import { summaryFacts, type FieldDef } from "../lib/fields";
import { NO_RELATED, type RelatedLists } from "../lib/related";
import { navLabel, navStep, neighbour, RECORD_PAGE_ATTR } from "../lib/recordNav";
import { nextOpenActivities } from "../lib/timeline";
import type { FieldLayout } from "../lib/fieldLayout";
import { useCrmStore, type CrmRefusal } from "../lib/store";
import { recordTabsFor, shownRecordTab, type RecordTab } from "../lib/urlState";
import { useFieldDefs, useFieldLayout } from "../lib/useCrmMode";
import { sourceSingular, type SourceLabels } from "../lib/views";
import FieldsPanel from "./FieldsPanel";
import { RecordFacts, RecordModal, RecordTabBar } from "./RecordShell";
import StatusPill from "./StatusPill";
import Timeline, { TimelineItem } from "./Timeline";
import type { DealContact, EntitySlug, Status, TimelineEntry } from "../lib/types";

type Row = Record<string, unknown>;

/** What the sheet holds, said under its title. */
const ENTITY_NOUN: Record<EntitySlug, string> = {
  deals: "Deal",
  leads: "Lead",
  contacts: "Contact",
  organizations: "Company",
};

const TITLE_KEY: Record<EntitySlug, string[]> = {
  deals: ["name"],
  leads: ["lead_name"],
  contacts: ["first_name", "last_name"],
  organizations: ["name"],
};

type LogBody = {
  type: "note" | "call" | "meeting" | "task";
  subject?: string;
  body?: string;
  due_at?: string;
};

/** The verbs of the record page. The sheet and the body share them. */
type RecordActions = {
  /**
   * Save an edit. `store.patchRecord` gives `false` for a refusal, and the
   * Fields tab then keeps its edit open (CRM-C4b, the 422 rule).
   */
  onPatch: (body: Record<string, unknown>) => void | Promise<boolean>;
  onMoveStatus: (statusId: string) => void;
  onLog: (body: LogBody) => void;
  onToggleTask: (activityId: string, completed: boolean) => void;
  onConvert: () => void;
  onSetPrimary: (contactId: string) => void;
  onDetach: (contactId: string) => void;
  onOpenContact: (contactId: string) => void;
  /** The related lists of a company or a contact (CRM-Q3). */
  related?: RelatedLists;
  /** A related row opens that record. */
  onOpenRelated?: (entity: EntitySlug, id: string) => void;
  onRetryRelated?: () => void;
  /** The `log-call` job (CRM-X2): not 0 sets the Timeline composer to Call. */
  callRequest?: number;
};

/** The place of the record in the list behind it (CRM-X4 item 5). */
type RecordOrder = {
  /** The ids of the list the member came from, in its order. Null: no list. */
  order: string[] | null;
  /** Open the record with this id in place of this one. */
  onStep: (id: string) => void;
};

export default function RecordSheet({
  entity,
  record,
  statuses,
  timeline,
  dealContacts,
  saving,
  onClose,
  tab,
  onTab,
  order = null,
  onStep,
  related = NO_RELATED,
  labels = null,
  ...actions
}: {
  entity: EntitySlug;
  record: Row | null;
  statuses: Status[];
  timeline: TimelineEntry[];
  dealContacts: DealContact[];
  saving: boolean;
  onClose: () => void;
  /** The tab of `?rtab=`. */
  tab: RecordTab;
  onTab: (tab: RecordTab) => void;
  /**
   * The source's names of the core entities (CRM-Q17a-2 item 7). In Mirror
   * mode a contact can read "Existing Site". `null` keeps the CRM's noun.
   */
  labels?: SourceLabels | null;
} & RecordActions &
  RecordOrder) {
  const { defs } = useFieldDefs(entity);
  // CRM-C4b: the layout draws the Fields tab when it says `studio`.
  const { layout } = useFieldLayout(entity);
  const refusal = useCrmStore((s) => s.refusal);

  // A contact row names its company by id only. The name is one read of the
  // company record, through the one read cache.
  const companyId =
    entity === "contacts" && typeof record?.company_id === "string" ? record.company_id : null;
  const company = useCachedResource<Row>(
    companyId ? `crm:organizations:${companyId}` : null,
    () => api.getRecord<Row>("organizations", companyId!),
  );
  const companyName =
    typeof company.data?.name === "string" && company.data.id === companyId ? company.data.name : null;

  // `j` and `k` (CRM-X4 item 5). `navStep` refuses a key with a modifier down,
  // so Cmd-K stays the shell's, and a key typed into a field.
  const recordId = typeof record?.id === "string" ? record.id : null;
  useEffect(() => {
    if (!order || !recordId) return;
    const onKey = (event: KeyboardEvent) => {
      const step = navStep(event);
      if (step === null) return;
      const next = neighbour(order, recordId, step);
      if (!next) return;
      event.preventDefault();
      onStep(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order, recordId, onStep]);

  const title = record
    ? TITLE_KEY[entity]
        .map((key) => record[key])
        .filter(Boolean)
        .join(" ") || "Untitled"
    : "Loading…";

  const zoho = zohoLink(record);

  return (
    // The page mounts it from `?deal=` and the rest, and every way out runs
    // `onClose`, which is `closeRecord`. Back closes it too, because Back
    // drops the parameter. The drawer is the shared shell (`RecordShell`).
    <RecordModal
      title={title}
      noun={sourceSingular(entity, labels) ?? ENTITY_NOUN[entity]}
      zoho={zoho}
      onClose={onClose}
    >
      {record === null ? (
        <p className="p-8 text-center text-xs text-muted-foreground">Loading…</p>
      ) : (
        <RecordPage
          entity={entity}
          record={record}
          statuses={statuses}
          timeline={timeline}
          dealContacts={dealContacts}
          saving={saving}
          fieldDefs={defs}
          layout={layout}
          refusal={refusal}
          tab={tab}
          onTab={onTab}
          order={order}
          onStep={onStep}
          companyName={companyName}
          related={related}
          {...actions}
        />
      )}
    </RecordModal>
  );
}

/**
 * The body of the record page: the summary header, the tabs and the tab on
 * screen. Props only, no read and no listener.
 */
export function RecordPage({
  entity,
  record,
  statuses,
  timeline,
  dealContacts,
  saving,
  fieldDefs,
  layout = null,
  refusal = null,
  tab,
  onTab,
  order,
  onStep,
  companyName,
  now,
  onPatch,
  onMoveStatus,
  onLog,
  onToggleTask,
  onConvert,
  onSetPrimary,
  onDetach,
  onOpenContact,
  related = NO_RELATED,
  onOpenRelated,
  onRetryRelated,
  callRequest = 0,
}: {
  entity: EntitySlug;
  record: Row;
  statuses: Status[];
  timeline: TimelineEntry[];
  dealContacts: DealContact[];
  saving: boolean;
  fieldDefs: FieldDef[] | null;
  /** The layout of the entity (CRM-C4b). `null` draws the Fields tab of today. */
  layout?: FieldLayout | null;
  /** The refusal of the last edit (the 422 rule). */
  refusal?: CrmRefusal | null;
  tab: RecordTab;
  onTab: (tab: RecordTab) => void;
  /** The name of a contact's company, which the contact row does not carry. */
  companyName: string | null;
  /** Today, for the due dates and the next activities. A test passes a fixed one. */
  now?: Date;
} & RecordActions &
  RecordOrder) {
  const mirrored = isMirrored(record);
  const shown = shownRecordTab(entity, tab);
  const status = statuses.find((s) => s.id === record.status_id) ?? null;
  const facts = summaryFacts(entity, record, companyName);
  const id = String(record.id ?? "");
  const place = order ? navLabel(order, id) : null;
  const previous = order ? neighbour(order, id, -1) : null;
  const next = order ? neighbour(order, id, 1) : null;
  const upcoming = shown === "overview" ? nextOpenActivities(timeline, now) : [];

  const panel = {
    entity,
    record,
    statuses,
    dealContacts,
    saving,
    onPatch,
    onConvert,
    onSetPrimary,
    onDetach,
    onOpenContact,
    related,
    onOpenRelated,
    onRetryRelated,
    fieldDefs,
    layout,
    refusal,
    companyName,
  };

  return (
    // The mark of the record page (`RECORD_PAGE_ATTR`): `navStep` reads it to
    // tell the sheet's own dialog from a dialog over it.
    <div className="flex min-h-0 flex-1 flex-col" {...{ [RECORD_PAGE_ATTR]: "" }}>
      {/* The summary header (CRM-X4 item 1). It wraps on a phone. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-3">
        {statuses.length > 0 && (
          // The one stage control. In Mirror mode Zoho owns the stage, so the
          // pill is a badge (spec section 3.3).
          <StatusPill
            status={status}
            statuses={statuses}
            onChange={onMoveStatus}
            disabled={saving || mirrored}
          />
        )}
        <RecordFacts facts={facts} />
        {order && (
          // The place in the list, and the two steps for a mouse or a touch.
          // `j` and `k` do the same from the keyboard.
          <div className="ml-auto flex shrink-0 items-center gap-1">
            {place && (
              <span className="text-[11px] tabular-nums text-muted-foreground">{place}</span>
            )}
            <Button
              variant="ghost"
              size="icon-xs"
              icon="ChevronUp"
              aria-label="Previous record"
              title="Previous record (k)"
              disabled={!previous}
              onClick={() => previous && onStep(previous)}
            />
            <Button
              variant="ghost"
              size="icon-xs"
              icon="ChevronDown"
              aria-label="Next record"
              title="Next record (j)"
              disabled={!next}
              onClick={() => next && onStep(next)}
            />
          </div>
        )}
      </div>

      <RecordTabBar tabs={recordTabsFor(entity)} active={shown} onTab={onTab} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        {shown === "timeline" ? (
          <Timeline
            entries={timeline}
            onLog={onLog}
            onToggleTask={onToggleTask}
            saving={saving}
            parentMirrored={mirrored}
            now={now}
            callRequest={callRequest}
          />
        ) : shown === "related" ? (
          <FieldsPanel part="related" {...panel} />
        ) : shown === "fields" ? (
          <FieldsPanel part="fields" {...panel} />
        ) : (
          <>
            <FieldsPanel part="overview" {...panel} />
            {/* The next three open activities (CRM-X4 item 2), by the rule
                of section 19.7. The Timeline tab holds the rest. */}
            <section className="border-t border-border px-4 py-3">
              <h3 className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                Next up
              </h3>
              {upcoming.length === 0 ? (
                <p className="text-xs text-muted-foreground">No open activity with a date.</p>
              ) : (
                <ol className="space-y-3">
                  {upcoming.map((entry) => (
                    <li key={entry.activity!.id}>
                      <TimelineItem
                        entry={entry}
                        onToggleTask={onToggleTask}
                        parentMirrored={mirrored}
                        now={now}
                      />
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
