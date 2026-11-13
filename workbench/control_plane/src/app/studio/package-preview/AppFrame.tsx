"use client";

/**
 * The app frame of `/studio/package-preview` (STU-AF1, `ai_studio.md` §5.5), behind
 * `NEXT_PUBLIC_STUDIO_APP_FRAME`. It draws the pages of the manifest through `StudioPage`, on
 * live data through the door of the page. The page id lives in the URL (`?page=<id>`).
 *
 * The frame composes parts that exist: `StudioPage`, the mapper `lib/studio/viewBlocks.ts`,
 * `GenerativeUINode`, `StudioForm` and `StudioDetail`. It adds no rail, no tab bar and no dialog.
 * The rail is AF-2. A viewer (J2a) reads only: the door holds the reads, and the form view says so.
 * This is the one file of the route that holds the page model. `page.tsx` imports none.
 */
import { useMemo } from "react";
import { useSearchParams } from "next/navigation";

import GenerativeUINode from "@/components/GenerativeUINode";
import { EmptyState } from "@/components/EmptyState";
import DeletePackageButton from "@/components/studio/DeletePackageButton";
import PackageLifetimeBar from "@/components/studio/PackageLifetimeBar";
import RecordsStatus from "@/components/studio/RecordsStatus";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioForm from "@/components/studio/StudioForm";
import StudioPage, { PANEL } from "@/components/studio/StudioPage";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, type StudioDataDoor, moreLine, sourceKey } from "@/lib/studio/dataDoor";
import { buildHref } from "@/lib/studio/packageDraft";
import { buildPage } from "@/lib/studio/pageModel";
import { type PreviewControls, recordTitle } from "@/lib/studio/packagePreview";
import { READ_ONLY_SENTENCE, type PackageDetail } from "@/lib/studio/realDoor";
import { type ViewDetail, type ViewMapContext, mapView } from "@/lib/studio/viewBlocks";
import { usePageState } from "@/lib/studio/usePageState";
import { useCachedResource } from "@/lib/useCachedResource";

export interface AppFrameProps {
  pkg: string;
  detail: PackageDetail;
  door: StudioDataDoor;
  controls: PreviewControls;
  /** Called after the owner deleted the app. */
  onDeleted: () => void;
}

/** A detail view: the sheet of the picked record, or of the first one. */
function DetailCell({ model, door, selected, onPick }: {
  model: ViewDetail; door: StudioDataDoor; selected: string | null; onPick: (id: string) => void;
}) {
  const rows = useCachedResource<Row[]>(sourceKey(door, model.source), () => door.read(model.source));
  const all = rows.data ?? [];
  const record = all.find((r) => String(r.id) === selected) ?? all[0];
  return (
    <div className={PANEL}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">Detail</h2>
        <SelectButton label="Record" value={record ? String(record.id) : ""} widthClass="w-48"
          options={all.map((r) => ({ value: String(r.id), label: recordTitle(model.fields, r) }))} onChange={onPick} />
      </div>
      {record ? <StudioDetail fields={model.fields} record={record} />
        : rows.error && rows.data === undefined
          ? <RecordsStatus loading={false} error={rows.error} onRetry={rows.refresh} />
          : <EmptyState icon="Inbox" message="No record to show yet" />}
      {moreLine(rows.data) ? <p role="status" className="mt-2 text-xs text-muted-foreground">{moreLine(rows.data)}</p> : null}
    </div>
  );
}

export default function AppFrame({ pkg, detail, door, controls, onDeleted }: AppFrameProps) {
  const pageParam = useSearchParams().get("page");
  const manifest = useMemo(() => ({
    views: detail.views, pages: detail.pages, actions: detail.actions, objects: detail.objects,
  }), [detail.views, detail.pages, detail.actions, detail.objects]);
  // An unknown page id gives the first page, and never an error (`buildPage`).
  const built = useMemo(() => buildPage(manifest, pageParam), [manifest, pageParam]);
  const { state, setTab, setPage, setSelected } = usePageState(built.page);
  const ctx: ViewMapContext = useMemo(
    () => ({ actions: controls.actions, objects: detail.objects, owner: controls.owner }),
    [controls.actions, controls.owner, detail.objects]);

  const renderView = (id: string) => {
    const view = detail.views.find((v) => v.id === id);
    const model = view ? mapView(view, ctx) : null;
    if (!model) return null;
    switch (model.kind) {
      case "node":
        return <GenerativeUINode spec={model.node} />;
      case "problem":
        return <p role="status" className={`${PANEL} text-sm text-muted-foreground`}>{model.reason}</p>;
      case "form":
        return (
          <div className={PANEL}>
            <StudioForm fields={model.fields} door={door} action={model.action} title={model.title}
              submitLabel="Add record" savedMessage="Record added" />
          </div>
        );
      case "detail":
        return <DetailCell model={model} door={door} selected={state.selected} onPick={setSelected} />;
    }
  };

  const pagePicks = built.pages.map((p) => ({ value: p.id, label: p.title || p.id }));
  const toolbar = (
    <>
      <PackageLifetimeBar pkg={pkg} />
      {pagePicks.length > 1 ? (
        <SelectButton label="Page" value={built.page.id} widthClass="w-48" options={pagePicks} onChange={setPage} />
      ) : null}
      {controls.owner ? (
        <>
          <Button variant="secondary" size="sm" icon="Code" href={buildHref(pkg)}>Open in Studio IDE</Button>
          <DeletePackageButton id={pkg} onDeleted={onDeleted} />
        </>
      ) : <span role="status" className="text-xs text-muted-foreground">{READ_ONLY_SENTENCE}</span>}
    </>
  );

  return (
    <StudioDataContext.Provider value={door}>
      <StudioPage title={detail.name.trim() || "App"} back={{ href: "/studio", label: "Back to Studio" }}
        manifest={manifest} pageId={built.page.id} state={state} onTab={setTab}
        onCloseSide={() => setSelected(null)} renderView={renderView} toolbar={toolbar} />
    </StudioDataContext.Provider>
  );
}
