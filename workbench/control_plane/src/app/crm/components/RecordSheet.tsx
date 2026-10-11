"use client";

/**
 * The record sheet — opened OVER the list, never as its own route
 * (specs/crm_app.md §5 surface 3).
 *
 * The URL carries `?deal=<id>`, so Back closes the sheet instead of leaving
 * the app and a link to a record carries the list the sender was looking at.
 * That decision lives in ../lib/urlState.ts; this is its surface.
 *
 * Left: the timeline. Right: the fields. Both halves are their own component
 * because the sheet serves four entities and the difference between them is
 * entirely in the field list.
 */

import Modal from "@/components/ui/Modal";
import { useViewMode } from "@/components/ViewModeProvider";
import { isMirrored, zohoLink } from "../lib/connect";
import { NO_RELATED, type RelatedLists } from "../lib/related";
import FieldsPanel from "./FieldsPanel";
import Timeline from "./Timeline";
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

export default function RecordSheet({
  entity,
  record,
  statuses,
  timeline,
  dealContacts,
  saving,
  onClose,
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
}: {
  entity: EntitySlug;
  record: Row | null;
  statuses: Status[];
  timeline: TimelineEntry[];
  dealContacts: DealContact[];
  saving: boolean;
  onClose: () => void;
  onPatch: (body: Record<string, unknown>) => void;
  onMoveStatus: (statusId: string) => void;
  onLog: (body: {
    type: "note" | "call" | "meeting" | "task";
    subject?: string;
    body?: string;
    due_at?: string;
  }) => void;
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
}) {
  const { isMobile } = useViewMode();

  const title = record
    ? TITLE_KEY[entity]
        .map((key) => record[key])
        .filter(Boolean)
        .join(" ") || "Untitled"
    : "Loading…";

  const zoho = zohoLink(record);

  return (
    // On `Modal` (CRM-U2), as a drawer at the right edge on a desktop and as
    // a sheet from the bottom on a phone. The URL still owns whether it is
    // open: the page mounts it from `?deal=` and the rest, and every way out
    // (Escape, the scrim, the close button) runs `onClose`, which is
    // `closeRecord`. Back closes it too, because Back drops the parameter.
    <Modal
      open
      onClose={onClose}
      title={title}
      description={
        <>
          {ENTITY_NOUN[entity]}
          {zoho && (
            // The record header carries the way back to Zoho (CRM-Q1).
            <>
              {" · "}
              <a
                href={zoho}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline underline-offset-2"
              >
                Open in Zoho
              </a>
            </>
          )}
        </>
      }
      size="4xl"
      placement={isMobile ? "sheet" : "end"}
      closeLabel="Close record"
      className="h-full"
    >
      {record === null ? (
        <p className="p-8 text-center text-xs text-muted-foreground">Loading…</p>
      ) : (
        <div
          className={`flex min-h-0 flex-1 ${isMobile ? "flex-col overflow-y-auto" : "flex-row"}`}
        >
          <div
            className={`min-h-0 ${isMobile ? "" : "flex-1 border-r border-border"}`}
          >
            <Timeline
              entries={timeline}
              onLog={onLog}
              onToggleTask={onToggleTask}
              saving={saving}
              parentMirrored={isMirrored(record)}
            />
          </div>
          <div className={isMobile ? "" : "w-[340px] shrink-0"}>
            <FieldsPanel
              entity={entity}
              record={record}
              statuses={statuses}
              dealContacts={dealContacts}
              saving={saving}
              onPatch={onPatch}
              onMoveStatus={onMoveStatus}
              onConvert={onConvert}
              onSetPrimary={onSetPrimary}
              onDetach={onDetach}
              onOpenContact={onOpenContact}
              related={related}
              onOpenRelated={onOpenRelated}
              onRetryRelated={onRetryRelated}
            />
          </div>
        </div>
      )}
    </Modal>
  );
}
