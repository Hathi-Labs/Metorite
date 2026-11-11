"use client";

/**
 * The Preview tab of the Studio IDE (`ai_studio.md` §6.3, G3a). It draws each object of the DRAFT
 * as a Table, and as a Board when the object has a select field to group by. The rows are
 * SAMPLE rows from a door that refuses every write, because an object has rows only after a
 * publish. The phone-width toggle sets the frame to 390 px.
 */
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import GenerativeUINode from "@/components/GenerativeUINode";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { tableBlock } from "@/lib/studio/blocks";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { draftObjects, type DraftObject } from "@/lib/studio/draftObjects";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { noBoardReason, objectBoardBlock, tableFields } from "@/lib/studio/packagePreview";
import { SAMPLE_LABEL, makeDraftDoor } from "@/lib/studio/sampleRows";

const PANEL = "rounded-xl border border-border bg-card p-3";
const PHONE_WIDTH = 390;

export interface PreviewTabProps {
  manifest: unknown;
  /** The draft hash. A new hash is a new door, so the sample rows read again. */
  hash: string;
  pkg: string;
  object: string | null;
  onObject: (name: string) => void;
}

function Drawn({ o }: { o: DraftObject }) {
  const list = useMemo(() => ({ action: `object:${o.name}.list`, args: { limit: 100 } }), [o.name]);
  const computed = useMemo(() => computedKeys(o.rules), [o.rules]);
  const table = useMemo(
    () => tableBlock(tableFields(o.fields), list, o.label ?? o.name, { editable: false, computed: [...computed] }),
    [o, list, computed],
  );
  const board = useMemo(
    () => objectBoardBlock({ name: o.name, label: o.label }, o.fields, computed, list, false),
    [o, computed, list],
  );
  return (
    <>
      <section aria-label="Table" className={PANEL}>
        <h2 className="mb-2 text-sm font-semibold text-foreground">Table</h2>
        {o.fields.length ? <GenerativeUINode spec={table} /> : (
          <p className="text-xs text-muted-foreground">This object has no field yet, so the Table has no column. Ask Mira to add fields.</p>
        )}
      </section>
      <section aria-label="Board" className={PANEL}>
        <h2 className="mb-2 text-sm font-semibold text-foreground">Board</h2>
        {board ? <GenerativeUINode spec={board} /> : (
          <p className="text-xs text-muted-foreground">{noBoardReason(o.fields)} Add one to see a Board.</p>
        )}
      </section>
    </>
  );
}

export default function PreviewTab({ manifest, hash, pkg, object, onObject }: PreviewTabProps) {
  const objects = useMemo(() => draftObjects(manifest), [manifest]);
  const [phone, setPhone] = useState(false);
  const door = useMemo(
    () => makeDraftDoor(`${pkg}:${hash}`, Object.fromEntries(objects.map((o) => [o.name, o.fields]))),
    [pkg, hash, objects],
  );
  const picked = objects.find((o) => o.name === object) ?? objects[0];

  if (!picked) {
    return (
      <EmptyState icon="PackageOpen" message="This draft has no object yet."
        hint="Ask Mira in the panel on the right what the app should track. The preview fills in as she builds it." />
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <SelectButton label="Object" value={picked.name} widthClass="w-48"
          options={objects.map((o) => ({ value: o.name, label: o.label ?? o.name }))} onChange={onObject} />
        <Button type="button" variant="secondary" size="sm" icon="Smartphone" selected={phone} aria-pressed={phone}
          onClick={() => setPhone((p) => !p)}>
          Phone width
        </Button>
        <span role="status" className="text-xs text-muted-foreground">{SAMPLE_LABEL}</span>
      </div>
      <div data-preview-frame={phone ? "phone" : "wide"} className="mx-auto flex w-full min-w-0 flex-col gap-3"
        style={phone ? { maxWidth: PHONE_WIDTH } : undefined}>
        <StudioDataContext.Provider value={door}>
          <Drawn key={picked.name} o={picked} />
        </StudioDataContext.Provider>
      </div>
    </div>
  );
}
