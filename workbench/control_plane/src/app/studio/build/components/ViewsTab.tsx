"use client";

/**
 * The Views tab of the Studio IDE (`ai_studio.md` §6.3, slice G3c). A member adds a view (a Table or a
 * Board), changes its title and its object, and deletes it. The edit changes the working copy of the
 * manifest. The toolbar of the page saves it.
 *
 * A Board needs an object with a select field of one value and options, because the lanes come from
 * it. So the Board is offered only for such an object. A delete takes the page cells that show the
 * view with it, and says so first.
 */
import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import CommitInput from "./CommitInput";
import { VIEW_BLOCKS, addView, objectsFor, removeView, updateView, viewUses, type ViewBlock } from "@/lib/studio/draftEdit";

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const BLOCK_LABEL: Record<string, string> = { table: "Table", board: "Board" };

export interface ViewsTabProps {
  manifest: unknown;
  onChange: (manifest: unknown) => void;
  /** The Code tab holds JSON that does not parse, so an edit here would drop it. */
  locked: boolean;
}

export default function ViewsTab({ manifest, onChange, locked }: ViewsTabProps) {
  const views = isObject(manifest) && Array.isArray(manifest.views) ? manifest.views.filter(isObject) : [];
  const objects = isObject(manifest) && Array.isArray(manifest.objects) ? manifest.objects.filter(isObject).filter((o) => str(o.name)) : [];
  const label = (name: string) => str(objects.find((o) => str(o.name) === name)?.label) || name;
  const [block, setBlock] = useState<ViewBlock>("table");
  const [object, setObject] = useState("");
  const [title, setTitle] = useState("");
  const [ask, setAsk] = useState<string | null>(null);

  const choices = objectsFor(manifest, block);
  const chosen = choices.includes(object) ? object : choices[0] ?? "";
  const cells = ask ? viewUses(manifest, ask) : 0;

  const adder = (
    <div className="flex flex-wrap items-center gap-2">
      <SelectButton label="Block of the new view" value={block} widthClass="w-32" disabled={locked}
        options={VIEW_BLOCKS.map((b) => ({ value: b, label: BLOCK_LABEL[b] }))} onChange={(x) => setBlock(x as ViewBlock)} />
      <SelectButton label="Object of the new view" value={chosen} widthClass="w-44" prompt="No object fits"
        disabled={locked || choices.length === 0} options={choices.map((n) => ({ value: n, label: label(n) }))} onChange={setObject} />
      <Input inputSize="sm" className="w-48" aria-label="Title of the new view" placeholder="Title, such as All visits"
        value={title} maxLength={120} disabled={locked} onChange={(e) => setTitle(e.target.value)} />
      <Button size="sm" icon="Plus" disabled={locked || !chosen} onClick={() => {
        onChange(addView(manifest, block, chosen, title).manifest);
        setTitle("");
      }}>Add view</Button>
    </div>
  );
  const hint = choices.length === 0
    ? (block === "board"
      ? "A Board needs an object with a select field that has options. Add one in the Data tab first."
      : "Add an object in the Data tab first.")
    : null;

  return (
    <div className="flex flex-col gap-3 p-3">
      {adder}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {views.length === 0 ? (
        <EmptyState icon="LayoutGrid" message="This draft declares no view yet."
          hint="Add a Table or a Board above, or ask Mira for a Form." />
      ) : (
        <ul className="flex flex-col gap-2">
          {views.map((v, i) => {
            const id = str(v.id);
            const own = str(v.object) || (isObject(v.source) ? str(v.source.object) : "");
            const fits = objectsFor(manifest, str(v.block));
            const editable = VIEW_BLOCKS.includes(str(v.block) as ViewBlock);
            return (
              <li key={`${id}-${i}`} className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-3 py-2">
                <Badge tone="neutral" size="xs">{BLOCK_LABEL[str(v.block)] ?? (str(v.block) || "no block")}</Badge>
                <CommitInput className="w-48" label={`Title of the ${id} view`} value={str(v.title)} placeholder={id}
                  disabled={locked} onCommit={(t) => onChange(updateView(manifest, id, { title: t }))} />
                {editable ? (
                  <SelectButton label={`Object of the ${id} view`} value={fits.includes(own) ? own : ""} widthClass="w-44" disabled={locked}
                    prompt={own ? `${own} (no select field)` : "No object"} options={fits.map((n) => ({ value: n, label: label(n) }))}
                    onChange={(x) => onChange(updateView(manifest, id, { object: x }))} />
                ) : (
                  <span className="min-w-0 break-words text-xs text-muted-foreground" title="Mira sets this block. Change it in the Code tab.">
                    shows {own || "nothing yet"}
                  </span>
                )}
                <span className="text-xs text-muted-foreground">{id}</span>
                <span className="flex-1" />
                <Button size="icon-sm" variant="ghost" icon="Trash2" aria-label={`Delete the ${id} view`} disabled={locked}
                  onClick={() => setAsk(id)} />
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">A change is a draft until you press Save. A view is not a row, so a delete never touches records.</p>
      <ConfirmDialog open={ask !== null} title="Delete this view?" subject={ask}
        body={`The view leaves this draft${cells > 0 ? `, and ${cells} page cell${cells === 1 ? "" : "s"} that show${cells === 1 ? "s" : ""} it` : ""}. A view holds no records, so no record is lost. The live app keeps the view until you publish.`}
        confirmLabel="Delete" cancelLabel="Keep it" icon="Trash2"
        onConfirm={() => { if (ask) onChange(removeView(manifest, ask)); setAsk(null); }} onCancel={() => setAsk(null)} />
    </div>
  );
}
