"use client";

/**
 * /studio/package-preview — the Table, Form and Detail on the real records of a
 * package (`specs/ai_studio.md` §14 D2b-1, slice STU-D2b).
 *
 * `?pkg=<id>&object=<name>` picks the package and the object. The page reads
 * the packages of the member (`GET /api/studio/packages`), then the object rows
 * of the package, and draws the three blocks on the real door
 * (`lib/studio/realDoor.ts`). The Table lists the records and edits a cell in
 * place. The Form creates a record, and edits the one picked in the Detail
 * sheet. The Detail sheet holds the Delete button, which asks first.
 *
 * The Board (D3b) groups the records by the first select field of the object. A drop runs one
 * `object:<name>.update` on the same door.
 *
 * It builds no view-to-node mapper and reads no page of the manifest: PKG-8
 * owns that. It is no pane, so `nav.ts` does not list it, and the access gate of
 * `/studio` covers it (`page.test.ts` holds both). An org outside `STUDIO_ORGS`
 * gets 404 from the gateway, and the page shows an empty state with no error.
 */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import GenerativeUINode from "@/components/GenerativeUINode";
import { EmptyState } from "@/components/EmptyState";
import DeleteRecordButton from "@/components/studio/DeleteRecordButton";
import RecordsStatus from "@/components/studio/RecordsStatus";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioForm from "@/components/studio/StudioForm";
import DeletePackageButton from "@/components/studio/DeletePackageButton";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { tableBlock } from "@/lib/studio/blocks";
import { noListLine } from "@/lib/studio/packageLibrary";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, moreLine, sourceKey } from "@/lib/studio/dataDoor";
import { computedKeys } from "@/lib/studio/inlineEdit";
import {
  detailFields, fieldSpecs, formFields, missingObjectNote, noBoardReason, objectBoardBlock, pickObject, previewHref, recordTitle, tableFields,
} from "@/lib/studio/packagePreview";
import { buildHref } from "@/lib/studio/packageDraft";
import { STUDIO_OFF, createDoorKeeper, getPackage, listPackages, verbMap } from "@/lib/studio/realDoor";
import { useCachedResource } from "@/lib/useCachedResource";

const PAGE_LIMIT = 100;
const PANEL = "rounded-xl border border-border bg-card p-3 sm:p-4";

/** The app page. The title is the name of the package, and a page with no package yet is "App". */
function Shell({ children, note, title, actions }: {
  children: React.ReactNode; note?: React.ReactNode; title?: string; actions?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col">
      <AppTopBar title={title?.trim() || "App"} back={{ href: "/studio", label: "Back to Studio" }} actions={actions} />
      <div className="pb-askbar flex flex-1 flex-col gap-3 overflow-y-auto p-3 sm:gap-4 sm:p-4">
        {note ? <div className="flex flex-wrap items-center gap-2">{note}</div> : null}
        {children}
      </div>
    </div>
  );
}

function Off() {
  return (
    <Shell>
      <EmptyState icon="Inbox" message="AI Studio is not on for your workspace." />
    </Shell>
  );
}

function Records({ pkg, objectName }: { pkg: string; objectName: string | null }) {
  const router = useRouter();
  const detail = useCachedResource(`studio:package-preview:pkg:${pkg}`, () => getPackage(pkg));
  // One door while the actions stay the same: a focus read of the package gives a new object,
  // and a new door would reset the edit store and the delete flow.
  const [keeper] = useState(() => createDoorKeeper());
  const door = detail.data ? keeper(pkg, detail.data.actions) : null;
  const object = detail.data ? pickObject(detail.data.objects, objectName) : undefined;
  const fields = useMemo(() => (object ? fieldSpecs(object) : []), [object]);
  const computed = useMemo(() => computedKeys(object?.rules), [object?.rules]);
  // One page holds at most 100 records, the most that the store gives. The Table and the Board read it.
  const list = useMemo(() => ({ action: `object:${object?.name ?? "none"}.list`, args: { limit: PAGE_LIMIT } }), [object?.name]);
  const canUpdate = !!object && verbMap(detail.data?.actions)[`object:${object.name}.update`] !== undefined;
  const node = useMemo(
    () => tableBlock(tableFields(fields), list, object?.label ?? object?.name,
      { editable: canUpdate, computed: [...computed] }),
    [fields, list, object?.label, object?.name, computed, canUpdate],
  );
  const board = useMemo(() => objectBoardBlock(object ?? { name: "none" }, fields, computed, list, canUpdate),
    [object, fields, computed, list, canUpdate]);
  const canList = !!object && verbMap(detail.data?.actions)[`object:${object.name}.list`] !== undefined;
  const rows = useCachedResource<Row[]>(
    door && object && canList ? sourceKey(door, list) : null,
    () => (door as NonNullable<typeof door>).read(list),
  );
  const [picked, setPicked] = useState("");
  // After a delete the page leaves. It says so, and never "not available", while it leaves.
  const [deleted, setDeleted] = useState(false);
  const onDeleted = () => { setDeleted(true); router.push("/studio"); };
  const [editing, setEditing] = useState(false);
  const ideLink = <Button variant="secondary" size="sm" icon="Code" href={buildHref(pkg)}>Open in Studio IDE</Button>;

  if (deleted) return <Shell><EmptyState icon="PackageOpen" message="This app was deleted." /></Shell>;
  // A 404 over cached data is a package that is gone: never draw the old rows (BETA-1).
  if (detail.error === STUDIO_OFF || (detail.error && detail.data === undefined)) {
    if (detail.error === STUDIO_OFF) {
      return <Shell><EmptyState icon="PackageOpen" message="This package is not available to you." /></Shell>;
    }
    return <Shell><RecordsStatus loading={false} error={detail.error} onRetry={detail.refresh} /></Shell>;
  }
  if (!detail.data || !door) {
    return <Shell><RecordsStatus loading error={null} onRetry={detail.refresh} /></Shell>;
  }
  if (!object) {
    return (
      <Shell title={detail.data.name} actions={<>{ideLink}<DeletePackageButton id={pkg} onDeleted={onDeleted} /></>}>
        <EmptyState icon="PackageOpen" message="This app has no object yet. Ask Mira to add one, then publish it." />
      </Shell>
    );
  }

  const all = rows.data ?? [];
  const record = all.find((r) => r.id === picked) ?? all[0];
  const note = missingObjectNote(detail.data.objects, objectName);
  const objectPicks = detail.data.objects.map((o) => ({ value: o.name, label: o.label ?? o.name }));
  const title = detail.data.name;
  const deleteApp = <>{ideLink}<DeletePackageButton id={pkg} onDeleted={onDeleted} /></>;

  return (
    <Shell title={title} actions={deleteApp} note={
      <>
        <SelectButton label="Object" value={object.name} widthClass="w-48" options={objectPicks}
          onChange={(v) => router.replace(previewHref(pkg, v))} />
        {note ? <span role="status" className="text-xs text-muted-foreground">{note}</span> : null}
      </>
    }>
      {!canList ? (
        <p role="status" className={`${PANEL} text-sm text-muted-foreground`}>{noListLine(object.label ?? object.name)}</p>
      ) : (
      <StudioDataContext.Provider value={door}>
        <section aria-label="Table" className={PANEL}>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">Table</h2>
            <RecordsStatus loading={rows.loading} error={null} onRetry={rows.refresh} />
          </div>
          <GenerativeUINode spec={node} />
          {moreLine(rows.data) ? <p role="status" className="mt-2 text-xs text-muted-foreground">{moreLine(rows.data)}</p> : null}
          <p className="mt-3 text-xs text-muted-foreground">
            Click a cell to edit it. A text cell saves on Enter. Escape cancels. A refused edit puts the old value back.
          </p>
        </section>

        <section aria-label="Board" className={PANEL}>
          <h2 className="mb-2 text-sm font-semibold text-foreground">Board</h2>
          {board ? (
            <>
              {rows.error && rows.data === undefined ? (
                <p className="text-xs text-muted-foreground">The Table above says why the records did not load.</p>
              ) : <GenerativeUINode spec={board} />}
              <p className="mt-3 text-xs text-muted-foreground">
                Drag a card to another lane, or press Space on a card, then the arrow keys, then Space.
                A drop saves at once. A refused move puts the card back.
              </p>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              {noBoardReason(fields)} Add one to see a Board.
            </p>
          )}
        </section>

        <div className="grid grid-cols-1 gap-3 sm:gap-4 xl:grid-cols-2">
          <section aria-label="Form" className={PANEL}>
            <StudioForm fields={formFields(fields, computed)} door={door} action={`object:${object.name}.create`}
              title={`New ${object.label ?? object.name}`} submitLabel="Add record" savedMessage="Record added" />
          </section>

          <section aria-label="Detail" className={PANEL}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">Detail</h2>
              <SelectButton label="Record" value={record ? String(record.id) : ""} widthClass="w-48"
                options={all.map((r) => ({ value: String(r.id), label: recordTitle(fields, r) }))}
                onChange={(v) => { setPicked(v); setEditing(false); }} />
            </div>
            {record ? (
              <div className="space-y-3">
                {editing ? (
                  <StudioForm key={String(record.id)} fields={formFields(fields, computed)} door={door}
                    action={`object:${object.name}.update`} initial={record} extraArgs={{ id: record.id }}
                    title="Edit record" submitLabel="Save changes" savedMessage="Changes saved"
                    onSaved={() => setEditing(false)} />
                ) : (
                  <StudioDetail fields={detailFields(fields)} record={record} />
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant="secondary" size="sm" icon={editing ? "X" : "Pencil"}
                    onClick={() => setEditing((e) => !e)}>
                    {editing ? "Stop editing" : "Edit in form"}
                  </Button>
                  <DeleteRecordButton door={door} object={object.name} id={String(record.id)}
                    title={recordTitle(fields, record)} onDeleted={() => { setPicked(""); setEditing(false); }} />
                </div>
              </div>
            ) : (
              <EmptyState icon="Inbox" message="No record to show yet" />
            )}
          </section>
        </div>
      </StudioDataContext.Provider>
      )}
    </Shell>
  );
}

function Picker() {
  const router = useRouter();
  const found = useCachedResource("studio:package-preview:packages", () => listPackages());
  if (found.error && found.data === undefined) {
    return <Shell><RecordsStatus loading={false} error={found.error} onRetry={found.refresh} /></Shell>;
  }
  if (!found.data) return <Shell><RecordsStatus loading error={null} onRetry={found.refresh} /></Shell>;
  if (found.data.length === 0) {
    return (
      <Shell>
        <EmptyState icon="PackageOpen" message="You have no package yet. Make and publish one, then reload." />
      </Shell>
    );
  }
  return (
    <Shell>
      <section aria-label="Packages" className={PANEL}>
        <h2 className="mb-2 text-sm font-semibold text-foreground">Pick a package</h2>
        <SelectButton label="Package" value="" prompt="Choose a package" widthClass="w-64"
          options={found.data.map((p) => ({
            value: p.id, label: p.name || p.id, hint: p.live_version ? `v${p.live_version}` : "not published",
          }))}
          onChange={(id) => router.replace(previewHref(id))} />
      </section>
    </Shell>
  );
}

function PreviewBody() {
  const params = useSearchParams();
  const pkg = params.get("pkg");
  const objectName = params.get("object");
  const packages = useCachedResource("studio:package-preview:packages", () => listPackages());
  // The switch is off for this org: the list answers 404 and the page says so.
  if (packages.error === STUDIO_OFF) return <Off />;
  return pkg ? <Records pkg={pkg} objectName={objectName} /> : <Picker />;
}

export default function PackagePreviewPage() {
  return (
    <Suspense fallback={null}>
      <PreviewBody />
    </Suspense>
  );
}
