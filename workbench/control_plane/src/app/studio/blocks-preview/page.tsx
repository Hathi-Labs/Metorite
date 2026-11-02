"use client";

/**
 * /studio/blocks-preview — the Table, Form and Detail blocks on sample
 * records (`specs/ai_studio.md` §14 D2a).
 *
 * A member sees each block as an app will draw it. The data is a fake door
 * (`lib/studio/sampleDoor.ts`): nothing is read and nothing is stored, and
 * the page says so. It is no pane, so `nav.ts` does not list it. The access
 * gate of `/studio` covers it, because `featureForPath` matches the longest
 * prefix (`blocks-preview/page.test.ts` holds that).
 */
import { useMemo, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import GenerativeUINode from "@/components/GenerativeUINode";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioForm from "@/components/studio/StudioForm";
import Badge from "@/components/ui/Badge";
import SelectButton from "@/components/ui/SelectButton";
import { tableBlock } from "@/lib/studio/blocks";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, sourceKey } from "@/lib/studio/dataDoor";
import {
  SAMPLE_FIELDS, SAMPLE_TABLE_KEYS, makeSampleDoor,
} from "@/lib/studio/sampleDoor";
import { useCachedResource } from "@/lib/useCachedResource";

const PANEL = "rounded-xl border border-border bg-card p-3 sm:p-4";
const LIST = { action: "object:visit.list" };
const TABLE_FIELDS = SAMPLE_FIELDS.filter((f) => SAMPLE_TABLE_KEYS.includes(f.key));

export default function BlocksPreviewPage() {
  const door = useMemo(() => makeSampleDoor(), []);
  const [picked, setPicked] = useState("");
  const { data } = useCachedResource<Row[]>(sourceKey(door, LIST), () => door.read(LIST));
  const rows = data ?? [];
  const record = rows.find((r) => r.id === picked) ?? rows[0];
  const node = useMemo(() => tableBlock(TABLE_FIELDS, LIST, "Visits"), []);

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title="Block preview" subtitle="Table, Form and Detail on sample records"
        back={{ href: "/studio", label: "Back to Studio" }} />
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3 sm:gap-4 sm:p-4">
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

          <div className="grid grid-cols-1 gap-3 sm:gap-4 xl:grid-cols-2">
            <section aria-label="Form" className={PANEL}>
              <StudioForm fields={SAMPLE_FIELDS} door={door} action="object:visit.create"
                title="New visit" submitLabel="Add visit" />
              <p className="mt-3 text-xs text-muted-foreground">
                To see a refusal of the server, save with the title Duplicate.
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
