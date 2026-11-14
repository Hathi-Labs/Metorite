"use client";

/**
 * The record page of a Studio app (STU-AF5, `ai_studio.md` §5.5): the body of a record, with its
 * tabs. The app frame shows it in the side panel or as the full page (`StudioPage`).
 *
 * Details draws the Detail block of the record, read through the door with `object:<name>.get`.
 * Related draws a table for each relation field that points here, read through the door with a
 * filter on the link, so the rows are what the viewer reads. "New" opens the Form block over the
 * create action of the child, with the link filled by `linkedDoor`. Files lists the file fields.
 * It reuses `Tabs`, `StudioDetail`, `StudioForm` and `GenerativeUINode`, and holds no control of
 * its own. No CRM file is imported: `RecordShell.tsx` and `RelatedLists.tsx` are the model only.
 */
import { useEffect, useMemo, useState } from "react";

import GenerativeUINode from "@/components/GenerativeUINode";
import Tabs from "@/components/Tabs";
import RecordsStatus from "@/components/studio/RecordsStatus";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioForm from "@/components/studio/StudioForm";
import Button from "@/components/ui/Button";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, type StudioDataDoor, moreLine, sourceKey } from "@/lib/studio/dataDoor";
import { labelOf, tableBlock } from "@/lib/studio/blocks";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { detailFields, fieldSpecs, formFields, recordTitle, tableFields } from "@/lib/studio/packagePreview";
import {
  type RelatedList, fileFields, fileLabel, linkedDoor, newButtonState, pickRecordTab, recordTabs, relatedArgs, relatedLists,
} from "@/lib/studio/recordPageModel";
import { type DeclaredAction, type ObjectRow, verbMap } from "@/lib/studio/realDoor";
import { useCachedResource } from "@/lib/useCachedResource";

export interface StudioRecordProps {
  object: ObjectRow;
  objects: readonly ObjectRow[];
  recordId: string;
  door: StudioDataDoor;
  actions: readonly DeclaredAction[];
  /** The owner may write. A viewer reads only (J2a) and sees no "New". */
  owner: boolean;
  /** The wanted tab. An unknown tab shows the first one. */
  tab: string | null;
  onTab: (tab: string) => void;
  /** The start of the record link of a child row, or null. */
  recordBase: (object: string) => string | null;
  /** The title of the record, once it is read. */
  onTitle?: (title: string) => void;
}

const NOTE = "text-xs text-muted-foreground";

/** One read of the record, shared by Details and Files (the same cache key). */
function useRecordRead(object: ObjectRow, recordId: string, door: StudioDataDoor, actions: readonly DeclaredAction[]) {
  const declared = verbMap(actions)[`object:${object.name}.get`] !== undefined;
  const source = { action: `object:${object.name}.get`, args: { id: recordId } };
  const read = useCachedResource<Row[]>(declared ? sourceKey(door, source) : null, () => door.read(source));
  return { declared, read, record: read.data?.[0] };
}

function Details({ object, recordId, door, actions }: Pick<StudioRecordProps, "object" | "recordId" | "door" | "actions">) {
  const fields = useMemo(() => detailFields(fieldSpecs(object)), [object]);
  const { declared, read, record } = useRecordRead(object, recordId, door, actions);
  if (!declared) {
    return <p role="status" className={NOTE}>This app does not declare a read of one {object.label ?? object.name}, so it cannot open one.</p>;
  }
  if (record) return <StudioDetail fields={fields} record={record} />;
  if (read.error) {
    // A record that is gone, or that the viewer may not read, says so in plain words.
    return /gone|not found/i.test(read.error)
      ? <p role="status" className={NOTE}>{read.error}</p>
      : <RecordsStatus loading={false} error={read.error} onRetry={read.refresh} />;
  }
  return <RecordsStatus loading error={null} onRetry={read.refresh} />;
}

function RelatedTable({ list, recordId, door, actions, owner, recordBase, child }: {
  list: RelatedList; recordId: string; door: StudioDataDoor; actions: readonly DeclaredAction[];
  owner: boolean; recordBase: (object: string) => string | null; child: ObjectRow | undefined;
}) {
  const [adding, setAdding] = useState(false);
  const verbs = verbMap(actions);
  const listAction = `object:${list.object}.list`;
  const createAction = `object:${list.object}.create`;
  const canList = verbs[listAction] !== undefined;
  const canCreate = verbs[createAction] !== undefined;
  const specs = useMemo(() => (child ? fieldSpecs(child) : []), [child]);
  const others = useMemo(() => specs.filter((f) => f.key !== list.field), [specs, list.field]);
  const source = useMemo(() => ({ action: listAction, args: relatedArgs(list, recordId) }), [listAction, list, recordId]);
  const base = recordBase(list.object);
  const node = useMemo(
    // No title on the table: the heading above it names it.
    () => tableBlock(tableFields(others), source, undefined, { openBase: base ?? undefined }),
    [others, source, base]);
  const formDoor = useMemo(() => linkedDoor(door, list.field, recordId), [door, list.field, recordId]);
  const form = useMemo(() => formFields(others, computedKeys(child?.rules)), [others, child]);
  const rows = useCachedResource<Row[]>(canList ? sourceKey(door, source) : null, () => door.read(source));
  const more = moreLine(rows.data);
  return (
    <section aria-label={list.title} className="space-y-2" data-related={list.object}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{list.title}</h3>
        {newButtonState(owner, canCreate) !== "hidden" ? (
          <Button variant="secondary" size="sm" icon="Plus" aria-expanded={adding}
            disabled={!canCreate}
            onClick={() => setAdding((v) => !v)}>
            New
          </Button>
        ) : null}
      </div>
      {newButtonState(owner, canCreate) === "off" ? (
        <p role="status" className={NOTE}>This app does not declare a create action for {list.objectLabel}, so New is off.</p>
      ) : null}
      {adding && canCreate ? (
        <div className="rounded-xl border border-border bg-card p-3">
          <StudioForm fields={form} door={formDoor} action={createAction} title={`New ${list.objectLabel}`}
            submitLabel="Add record" savedMessage="Record added" onSaved={() => setAdding(false)} />
        </div>
      ) : null}
      {canList ? <GenerativeUINode spec={node} /> : (
        <p role="status" className={NOTE}>This app does not declare a list of {list.objectLabel}, so it cannot show them.</p>
      )}
      {more ? <p role="status" className={NOTE}>{more}</p> : null}
    </section>
  );
}

function Files({ object, recordId, door, actions }: Pick<StudioRecordProps, "object" | "recordId" | "door" | "actions">) {
  const { declared, read, record } = useRecordRead(object, recordId, door, actions);
  const specs = useMemo(() => fieldSpecs(object), [object]);
  if (!record) {
    return read.error
      ? <RecordsStatus loading={false} error={read.error} onRetry={read.refresh} />
      : declared ? <RecordsStatus loading error={null} onRetry={read.refresh} />
        : <p role="status" className={NOTE}>This app does not declare a read of one {object.label ?? object.name}, so it cannot open one.</p>;
  }
  const files = fileFields(object).flatMap((f) => specs.filter((s) => s.key === f.key));
  return (
    <ul className="space-y-2" aria-label="Files">
      {files.map((f) => {
        const raw = record[f.key];
        const label = fileLabel(raw);
        return (
          <li key={f.key} data-field={f.key} className="rounded-lg border border-border bg-card px-3 py-2">
            <p className="text-xs font-medium text-muted-foreground">{labelOf(f)}</p>
            {label ? <p className="mt-1 text-xs text-foreground">{label}</p> : <p className={`mt-1 ${NOTE}`}>No file yet</p>}
          </li>
        );
      })}
    </ul>
  );
}

export default function StudioRecord({ object, objects, recordId, door, actions, owner, tab, onTab, recordBase, onTitle }: StudioRecordProps) {
  const tabs = useMemo(() => recordTabs(object, objects), [object, objects]);
  const active = pickRecordTab(tabs, tab);
  const lists = useMemo(() => relatedLists(object.name, objects), [object.name, objects]);
  // The title of the record shows on every tab, so the shell reads the record, not only Details.
  const { record } = useRecordRead(object, recordId, door, actions);
  const title = record ? recordTitle(detailFields(fieldSpecs(object)), record) : null;
  useEffect(() => { if (title !== null) onTitle?.(title); }, [title, onTitle]);
  return (
    <StudioDataContext.Provider value={door}>
      <div className="min-w-0 space-y-3" data-record={recordId} data-record-tab={active}>
        {tabs.length > 1 ? (
          <Tabs variant="underline" activeTab={active} onTabChange={onTab} tabs={tabs.map((t) => ({ id: t.id, label: t.title }))} />
        ) : null}
        {active === "details" ? <Details object={object} recordId={recordId} door={door} actions={actions} /> : null}
        {active === "related" ? (
          lists.length ? (
            <div className="space-y-5">
              {lists.map((l) => (
                <RelatedTable key={`${l.object}.${l.field}`} list={l} recordId={recordId} door={door} actions={actions}
                  owner={owner} recordBase={recordBase} child={objects.find((o) => o.name === l.object)} />
              ))}
            </div>
          ) : <p role="status" className={NOTE}>No other object of this app links to {object.label ?? object.name}.</p>
        ) : null}
        {active === "files" ? <Files object={object} recordId={recordId} door={door} actions={actions} /> : null}
      </div>
    </StudioDataContext.Provider>
  );
}
