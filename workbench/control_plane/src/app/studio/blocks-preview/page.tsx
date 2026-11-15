"use client";

/**
 * /studio/blocks-preview — the Table, Board, Gallery, Steps, Form and Detail
 * blocks on sample records (`specs/ai_studio.md` §14 D2a, D3, D19a and D14a).
 *
 * A member sees each block as an app will draw it. The data is a fake door
 * (`lib/studio/sampleDoor.ts`): nothing is read and nothing is stored, and
 * the page says so. It is no pane, so `nav.ts` does not list it. The access
 * gate of `/studio` covers it, because `featureForPath` matches the longest
 * prefix (`blocks-preview/page.test.ts` holds that).
 *
 * The Gallery has its own sample door with 200 assets. A live door reads at
 * most 100 records (`VIEW_LIMIT`), and `moreLine` then names the rest. This
 * page has no record panel, so it plays the app frame: a click on a card
 * shows which record the link opens (`parseRecordLink`).
 *
 * The Steps section is the guided step player (D14a). It has its own sample door
 * (`sampleSteps.ts`) that stamps a sign-off with a member and a time. It is a preview only:
 * no catalog token, no route and no flag yet.
 */
import { useMemo, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import GenerativeUINode from "@/components/GenerativeUINode";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioForm from "@/components/studio/StudioForm";
import StudioStepPlayer from "@/components/studio/StudioStepPlayer";
import Badge from "@/components/ui/Badge";
import SelectButton from "@/components/ui/SelectButton";
import { tableBlock } from "@/lib/studio/blocks";
import { boardBlock } from "@/lib/studio/boardModel";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, sourceKey } from "@/lib/studio/dataDoor";
import { galleryBlock } from "@/lib/studio/galleryModel";
import { parseRecordLink, recordLinkBase } from "@/lib/studio/recordPageModel";
import {
  NO_PHOTO_EVERY, SAMPLE_ASSET_FIELDS, SAMPLE_FIELDS, SAMPLE_TABLE_KEYS, makeSampleDoor, sampleAssetRows,
} from "@/lib/studio/sampleDoor";
import { makeSampleStepDoor, sampleStepRows } from "@/lib/studio/sampleSteps";
import { useCachedResource } from "@/lib/useCachedResource";

const PANEL = "rounded-xl border border-border bg-card p-3 sm:p-4";
const LIST = { action: "object:visit.list" };
const ASSETS = { action: "object:asset.list" };
const STEPS = { action: "object:step.list" };
const VIEWER_DONE = 4;
/** More than a live door reads, so the preview shows the lazy load over a long list. */
const GALLERY_COUNT = 200;
const BOARD_FIELDS = SAMPLE_FIELDS.filter((f) =>
  ["title", "status", "owner", "visit_date", "tags", "done"].includes(f.key));
const TABLE_FIELDS = SAMPLE_FIELDS.filter((f) => SAMPLE_TABLE_KEYS.includes(f.key));

export default function BlocksPreviewPage() {
  const door = useMemo(() => makeSampleDoor(), []);
  const assetRows = useMemo(() => sampleAssetRows(GALLERY_COUNT), []);
  const assetDoor = useMemo(() => makeSampleDoor(SAMPLE_ASSET_FIELDS, assetRows), [assetRows]);
  const [member, setMember] = useState("editor");
  const stepDoors = useMemo(() => ({
    editor: makeSampleStepDoor(),
    viewer: makeSampleStepDoor(sampleStepRows(VIEWER_DONE), undefined, undefined, "sample:steps-viewer"),
  }), []);
  const stepDoor = member === "viewer" ? stepDoors.viewer : stepDoors.editor;
  const { data: stepRows } = useCachedResource<Row[]>(sourceKey(stepDoor, STEPS), () => stepDoor.read(STEPS));
  const [opened, setOpened] = useState<string | null>(null);
  const [picked, setPicked] = useState("");
  const { data } = useCachedResource<Row[]>(sourceKey(door, LIST), () => door.read(LIST));
  const rows = data ?? [];
  const record = rows.find((r) => r.id === picked) ?? rows[0];
  const node = useMemo(() => tableBlock(TABLE_FIELDS, LIST, "Visits"), []);
  const board = useMemo(() => boardBlock({
    fields: BOARD_FIELDS, groupBy: "status", titleField: "title", cardFields: ["owner", "visit_date", "tags"],
    update: "object:visit.update", title: "Visits",
  }, LIST), []);
  const gallery = useMemo(() => galleryBlock({
    fields: SAMPLE_ASSET_FIELDS, titleField: "name", coverField: "photo",
    cardFields: ["status", "owner", "bought_on"], openBase: recordLinkBase("", "asset"), title: "Assets",
  }, ASSETS), []);
  const openedName = opened ? String(assetRows.find((r) => r.id === opened)?.name ?? opened) : null;

  // The app frame opens a record link in its panel. This page has no panel, so it names the record.
  const onGalleryClick = (e: React.MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target instanceof Element ? e.target.closest("a[href]") : null;
    const link = parseRecordLink(a?.getAttribute("href"));
    if (!link) return;
    e.preventDefault();
    setOpened(link.id);
  };

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title="Block preview" subtitle="Table, Board, Gallery, Steps, Form and Detail on sample records"
        back={{ href: "/studio", label: "Back to Studio" }} />
      <div className="pb-askbar flex flex-1 flex-col gap-3 overflow-y-auto p-3 sm:gap-4 sm:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="warning" icon="FlaskConical">Sample data</Badge>
          <span className="text-xs text-muted-foreground">
            A fake door answers here. Nothing is saved, and a reload restores the first rows.
          </span>
        </div>

        <StudioDataContext.Provider value={door}>
          <section aria-label="Table" className={PANEL}>
            <h2 className="mb-2 text-sm font-semibold text-foreground">Table</h2>
            <GenerativeUINode spec={node} />
          </section>

          <section aria-label="Board" className={PANEL}>
            <h2 className="mb-2 text-sm font-semibold text-foreground">Board</h2>
            <GenerativeUINode spec={board} />
            <p className="mt-3 text-xs text-muted-foreground">
              Drag a card to another lane, or press Space on a card, then the arrow keys, then Space.
              To see a refusal of the server, move a visit that is not done into Closed.
            </p>
          </section>
        </StudioDataContext.Provider>

        <StudioDataContext.Provider value={assetDoor}>
          <section aria-label="Gallery" className={PANEL} onClick={onGalleryClick}>
            <h2 className="mb-2 text-sm font-semibold text-foreground">Gallery</h2>
            <p role="status" className="mb-2 text-xs text-muted-foreground">
              {openedName
                ? `The card opens the record ${openedName}. In an app, the record panel shows it.`
                : `${GALLERY_COUNT} sample assets. Each card loads its photo when it comes into view. Click a card to open its record.`}
            </p>
            <GenerativeUINode spec={gallery} />
            <p className="mt-3 text-xs text-muted-foreground">
              One asset in {NO_PHOTO_EVERY} has no photo, so its card shows the placeholder. A live app reads at most
              100 records and says &quot;Showing the first 100 of&quot; the total. Until Studio can serve a stored
              file, each live card shows the placeholder.
            </p>
          </section>
        </StudioDataContext.Provider>

        <section aria-label="Steps" className={PANEL}>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">Steps</h2>
            <SelectButton label="Member" value={member} widthClass="w-40"
              options={[{ value: "editor", label: "Can sign off" }, { value: "viewer", label: "Read only" }]}
              onChange={setMember} />
          </div>
          <p className="mb-2 text-xs text-muted-foreground">
            A run of 10 steps. Each step shows its photo and its instruction. Step 4 has a required check. Press Sign off
            without the tick to see the alert. To see a refusal of the server, sign off step 6 before step 5.
          </p>
          {stepRows ? (
            <StudioStepPlayer key={member} rows={stepRows} door={stepDoor} object="step" canSign={member !== "viewer"} />
          ) : null}
          <p className="mt-3 text-xs text-muted-foreground">
            The sample door stamps each sign-off with the member and the time. A reload restores the first rows, and the
            sample steps are not stored. So &quot;a reload opens the same step&quot; is shown by tests of the first step
            that is not done, with 0, 4 and 10 steps done. The Read only view has 4 steps done and opens on step 5.
          </p>
        </section>

        <StudioDataContext.Provider value={door}>
          <div className="grid grid-cols-1 gap-3 sm:gap-4 xl:grid-cols-2">
            <section aria-label="Form" className={PANEL}>
              <StudioForm fields={SAMPLE_FIELDS} door={door} action="object:visit.create"
                title="New visit" submitLabel="Add visit" savedMessage="Visit added" />
              <p className="mt-3 text-xs text-muted-foreground">
                To see a refusal of the server, save with the title Duplicate (a field) or Over limit (a rule).
              </p>
            </section>

            <section aria-label="Detail" className={PANEL}>
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-foreground">Detail</h2>
                <SelectButton label="Record" value={record ? String(record.id) : ""} widthClass="w-48"
                  options={rows.map((r) => ({ value: String(r.id), label: String(r.title) }))}
                  onChange={setPicked} />
              </div>
              {record && <StudioDetail fields={SAMPLE_FIELDS} record={record} />}
            </section>
          </div>
        </StudioDataContext.Provider>
      </div>
    </div>
  );
}
