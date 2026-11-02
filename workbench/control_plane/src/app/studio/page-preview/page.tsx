"use client";

/**
 * /studio/page-preview — one Studio page on sample records (`specs/ai_studio.md` §14 D11b).
 *
 * The page is `lib/studio/samplePage.json`, which `tests/unit/test_studio_sample_page.py`
 * runs through the A2 validator. The data is the fake door of the block preview. Nothing is
 * read from a server and nothing is stored. The tab, the filter and the picked record live
 * in the URL (`lib/studio/usePageState.ts`). The route is no pane, so `nav.ts` does not list
 * it, and the access gate of `/studio` covers it (`page.test.ts` holds that).
 *
 * `renderView` here is a stand-in for the mapper from a view to a block (A5, H1). It knows
 * the five sample views by id. The calendar view has no block yet, so it shows the
 * "not built yet" card.
 */
import { Suspense, useMemo, useState } from "react";

import GenerativeUINode from "@/components/GenerativeUINode";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioForm from "@/components/studio/StudioForm";
import RecordsStatus from "@/components/studio/RecordsStatus";
import StudioPage, { PANEL } from "@/components/studio/StudioPage";
import Badge from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { tableBlock } from "@/lib/studio/blocks";
import { boardBlock } from "@/lib/studio/boardModel";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, type StudioDataDoor, sourceKey } from "@/lib/studio/dataDoor";
import type { StudioManifest } from "@/lib/studio/manifest";
import { buildPage } from "@/lib/studio/pageModel";
import { type FilterDraft, filterInputValue } from "@/lib/studio/pageState";
import samplePage from "@/lib/studio/samplePage.json";
import { SAMPLE_FIELDS, SAMPLE_TABLE_KEYS, makeSampleDoor } from "@/lib/studio/sampleDoor";
import { usePageState } from "@/lib/studio/usePageState";
import { useCachedResource } from "@/lib/useCachedResource";

const MANIFEST = samplePage as unknown as StudioManifest;
const LIST = { action: "object:visit.list" };
const BOARD_FIELDS = SAMPLE_FIELDS.filter((f) =>
  ["title", "status", "owner", "visit_date", "tags", "done"].includes(f.key));
const TABLE_FIELDS = SAMPLE_FIELDS.filter((f) => SAMPLE_TABLE_KEYS.includes(f.key));
const FILTER = "q";

/** The base door with the rows cut to those whose title holds `q`. The scope keeps the caches apart. */
function filteredDoor(base: StudioDataDoor, q: string): StudioDataDoor {
  if (!q) return base;
  const needle = q.toLowerCase();
  return {
    ...base,
    scope: `${base.scope}:q=${q}`,
    async read(source) {
      const rows = await base.read(source);
      return rows.filter((r) => String(r.title ?? "").toLowerCase().includes(needle));
    },
  };
}

function PreviewBody() {
  const built = useMemo(() => buildPage(MANIFEST), []);
  const { state, setTab, setSelected, setFilter } = usePageState(built.page);
  const base = useMemo(() => makeSampleDoor(), []);
  const q = state.filters[FILTER] ?? "";
  const door = useMemo(() => filteredDoor(base, q), [base, q]);
  const [draft, setDraft] = useState<FilterDraft | null>(null);
  const { data, loading, error, refresh } = useCachedResource<Row[]>(sourceKey(base, LIST), () => base.read(LIST));
  const allRows = data ?? [];
  const record = allRows.find((r) => String(r.id) === state.selected);

  const table = useMemo(() => tableBlock(TABLE_FIELDS, LIST, "Visits"), []);
  const board = useMemo(() => boardBlock({
    fields: BOARD_FIELDS, groupBy: "status", titleField: "title", cardFields: ["owner", "visit_date", "tags"],
    update: "object:visit.update", title: "Visits",
  }, LIST), []);

  const renderView = (id: string) => {
    switch (id) {
      case "visit-table": return <GenerativeUINode spec={table} />;
      case "visit-board": return <GenerativeUINode spec={board} />;
      case "visit-form":
        return (
          <div className={PANEL}>
            <StudioForm fields={SAMPLE_FIELDS} door={door} action="object:visit.create"
              title="New visit" submitLabel="Add visit" savedMessage="Visit added" />
          </div>
        );
      case "visit-detail":
        return (
          <div className={PANEL}>
            {record
              ? <StudioDetail fields={SAMPLE_FIELDS} record={record} />
              : <p className="text-sm text-muted-foreground">That record is not in the sample list.</p>}
          </div>
        );
      default: return null;
    }
  };

  return (
    <StudioDataContext.Provider value={door}>
      <StudioPage
        title="Page preview"
        subtitle="One page on sample records"
        back={{ href: "/studio", label: "Back to Studio" }}
        manifest={MANIFEST}
        state={state}
        onTab={setTab}
        onCloseSide={() => setSelected(null)}
        renderView={renderView}
        toolbar={
          <>
            <Badge tone="warning" icon="FlaskConical">Sample data</Badge>
            <Input icon="Search" inputSize="sm" placeholder="Filter by title" aria-label="Filter by title"
              className="w-48" value={filterInputValue(draft, q)}
              onChange={(e) => { setDraft({ base: q, text: e.target.value }); setFilter(FILTER, e.target.value); }}
              onBlur={() => setDraft(null)} />
            <SelectButton label="Open record" value={state.selected ?? ""} widthClass="w-48"
              options={allRows.map((r) => ({ value: String(r.id), label: String(r.title) }))}
              onChange={(v) => setSelected(v || null)} />
            <RecordsStatus loading={loading} error={error} onRetry={refresh} />
          </>
        }
      />
    </StudioDataContext.Provider>
  );
}

export default function PagePreviewPage() {
  // `useSearchParams` needs a Suspense boundary, or a static build fails.
  return (
    <Suspense fallback={null}>
      <PreviewBody />
    </Suspense>
  );
}
