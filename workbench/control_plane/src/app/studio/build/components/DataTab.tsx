"use client";

/**
 * The Data tab of the Studio IDE (`ai_studio.md` §6.3, slice G3c). A member adds, renames (the label)
 * and deletes an object. For the object they pick, they add a field, change its label, type, required
 * flag and options, and delete it. Every edit changes the working copy of the manifest and nothing
 * else. The toolbar of the page saves it, through the one save (`draftSave.ts`).
 *
 * Only the label of an object or a field changes in place. The name and the key stay, because
 * actions, views and records name them. Only the safe field types are offered (`SAFE_TYPES`).
 *
 * A delete asks first and says what it really does (`draftEdit.publishEffect`): the draft loses the
 * item, and a live item blocks the next publish until it is back.
 */
import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input, Select } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import {
  LIVE_FIELD_NOTE, SAFE_TYPES, TYPE_LABELS, addField, addObject, isLiveField, isLiveObject, linksTo, objectCut,
  publishEffect, removeField, removeObject, renameObject, updateField, type LiveObject,
} from "@/lib/studio/draftEdit";
import type { FieldType } from "@/lib/studio/manifest";

import OptionsInput from "./OptionsInput";

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

export interface DataTabProps {
  manifest: unknown;
  object: string | null;
  onObject: (name: string) => void;
  /** Replace the working copy of the manifest. */
  onChange: (manifest: unknown) => void;
  /** The objects that the last publish made. A live item cannot go yet. */
  live: readonly LiveObject[];
  /** The Code tab holds JSON that does not parse, so an edit here would drop it. */
  locked: boolean;
}

type Ask = { kind: "object"; name: string } | { kind: "field"; key: string } | null;

function dropWords(m: unknown, ask: NonNullable<Ask>, object: string, live: readonly LiveObject[]) {
  if (ask.kind === "object") {
    const cut = objectCut(m, ask.name);
    const links = linksTo(m, ask.name);
    const lost = [
      cut.actions.length ? `${cut.actions.length} action${cut.actions.length === 1 ? "" : "s"}` : "",
      cut.views.length ? `${cut.views.length} view${cut.views.length === 1 ? "" : "s"}` : "",
    ].filter(Boolean).join(" and ");
    return {
      title: "Delete this object?",
      body: `${links.length ? `Not yet: ${links.join(", ")} link to it. Delete those fields first. ` : ""}The object, its fields${lost ? `, ${lost} that use it` : ""} leave this draft. ${publishEffect("object", isLiveObject(live, ask.name))}`,
      blocked: links.length > 0,
    };
  }
  return {
    title: "Delete this field?",
    body: `The field leaves this draft, and its name leaves the views of this object. ${publishEffect("field", isLiveField(live, object, ask.key))}`,
    blocked: false,
  };
}

export default function DataTab({ manifest, object, onObject, onChange, live, locked }: DataTabProps) {
  const [ask, setAsk] = useState<Ask>(null);
  const [adding, setAdding] = useState(false);
  const [newObject, setNewObject] = useState("");
  const [newField, setNewField] = useState("");
  const [newType, setNewType] = useState<FieldType>("text");

  const objects = isObject(manifest) && Array.isArray(manifest.objects) ? manifest.objects.filter(isObject) : [];
  const named = objects.filter((o) => str(o.name));
  const picked = named.find((o) => str(o.name) === object) ?? named[0];

  const makeObject = () => {
    if (!newObject.trim() || locked) return;
    const r = addObject(manifest, newObject);
    onChange(r.manifest);
    if (r.name) onObject(r.name);
    setNewObject("");
    setAdding(false);
  };
  const addObjectRow = (
    <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); makeObject(); }}>
      <Input inputSize="sm" className="w-56" aria-label="Name of the new object" placeholder="Name of the object, such as Visit"
        value={newObject} maxLength={120} autoFocus onChange={(e) => setNewObject(e.target.value)} />
      <Button type="submit" size="sm" icon="Plus" disabled={!newObject.trim() || locked}>Add object</Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => { setAdding(false); setNewObject(""); }}>Cancel</Button>
    </form>
  );

  if (!picked) {
    return (
      <div className="flex flex-col gap-3 p-3">
        <EmptyState icon="Table" message="This draft has no object yet."
          hint="Add an object here, or ask Mira in the panel on the right what the app should track." />
        <div className="flex justify-center">{adding ? addObjectRow : (
          <Button size="sm" icon="Plus" disabled={locked} onClick={() => setAdding(true)}>Add object</Button>
        )}</div>
      </div>
    );
  }
  const name = str(picked.name);
  const fields = Array.isArray(picked.fields) ? picked.fields.filter(isObject) : [];
  const rules = Array.isArray(picked.rules) ? picked.rules.filter(isObject) : [];
  const words = ask ? dropWords(manifest, ask, name, live) : null;
  const confirm = () => {
    if (!ask || !words) return;
    if (!words.blocked) {
      if (ask.kind === "object") {
        const rest = named.map((o) => str(o.name)).filter((n) => n !== ask.name);
        onChange(removeObject(manifest, ask.name));
        if (rest[0]) onObject(rest[0]);
      } else {
        onChange(removeField(manifest, name, ask.key));
      }
    }
    setAsk(null);
  };

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <SelectButton label="Object" value={name} widthClass="w-48"
          options={named.map((o) => ({ value: str(o.name), label: str(o.label) || str(o.name) }))} onChange={onObject} />
        {adding ? addObjectRow : (
          <Button size="sm" variant="secondary" icon="Plus" disabled={locked} onClick={() => setAdding(true)}>Add object</Button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Label
          <Input inputSize="sm" className="w-56" aria-label={`Label of the ${name} object`} value={str(picked.label)}
            placeholder={name} maxLength={120} disabled={locked}
            onChange={(e) => onChange(renameObject(manifest, name, e.target.value))} />
        </label>
        <span className="text-xs text-muted-foreground">The name <code>{name}</code> stays, because actions and records use it.</span>
        {isLiveObject(live, name) ? <Badge tone="neutral" size="xs">Live</Badge> : null}
        <Button size="sm" variant="ghost" icon="Trash2" disabled={locked} onClick={() => setAsk({ kind: "object", name })}>Delete object</Button>
      </div>

      {fields.length === 0 ? (
        <p className="text-xs text-muted-foreground">This object has no field yet. Add one below, or ask Mira.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border bg-card">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <caption className="sr-only">Fields of {str(picked.label) || name}</caption>
            <thead className="border-b border-border text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Label</th>
                <th scope="col" className="px-3 py-2 font-medium">Type</th>
                <th scope="col" className="px-3 py-2 font-medium">Required</th>
                <th scope="col" className="px-3 py-2 font-medium">Options</th>
                <th scope="col" className="px-3 py-2"><span className="sr-only">Delete</span></th>
              </tr>
            </thead>
            <tbody>
              {fields.map((f) => {
                const key = str(f.key);
                const type = str(f.type);
                const isLive = isLiveField(live, name, key);
                const choice = type === "select" || type === "multiSelect";
                const known = (SAFE_TYPES as readonly string[]).includes(type);
                return (
                  <tr key={key} className="border-b border-border align-top last:border-0">
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <Input inputSize="sm" className="w-44" aria-label={`Label of ${key}`} value={str(f.label)} placeholder={key}
                          maxLength={120} disabled={locked}
                          onChange={(e) => onChange(updateField(manifest, name, key, { label: e.target.value }))} />
                        {isLive ? <span title={LIVE_FIELD_NOTE}><Badge tone="neutral" size="xs">Live</Badge></span> : null}
                      </div>
                      <span className="mt-0.5 block text-xs text-muted-foreground">{key}</span>
                    </td>
                    <td className="px-3 py-2">
                      {known ? (
                        <Select inputSize="sm" className="w-36" aria-label={`Type of ${key}`} value={type} disabled={locked}
                          onChange={(e) => onChange(updateField(manifest, name, key, { type: e.target.value as FieldType }))}>
                          {SAFE_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
                        </Select>
                      ) : (
                        <span className="text-muted-foreground" title="Mira sets this type. Change it in the Code tab.">{TYPE_LABELS[type] ?? type}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Checkbox aria-label={`${key} is required`} checked={f.required === true} disabled={locked}
                        onChange={(e) => onChange(updateField(manifest, name, key, { required: e.target.checked }))} />
                    </td>
                    <td className="px-3 py-2">
                      {choice ? (
                        <OptionsInput label={`Options of ${key}`} options={Array.isArray(f.options) ? f.options.map(String) : []} disabled={locked}
                          onCommit={(options) => onChange(updateField(manifest, name, key, { options }))} />
                      ) : str(f.to) ? <span className="text-muted-foreground">links to {str(f.to)}</span> : <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button size="icon-sm" variant="ghost" icon="Trash2" aria-label={`Delete the ${key} field`} disabled={locked}
                        onClick={() => setAsk({ kind: "field", key })} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Input inputSize="sm" className="w-56" aria-label="Label of the new field" placeholder="New field, such as Phone"
          value={newField} maxLength={120} disabled={locked} onChange={(e) => setNewField(e.target.value)} />
        <Select inputSize="sm" className="w-36" aria-label="Type of the new field" value={newType} disabled={locked}
          onChange={(e) => setNewType(e.target.value as FieldType)}>
          {SAFE_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
        </Select>
        <Button size="sm" icon="Plus" disabled={!newField.trim() || locked} onClick={() => {
          onChange(addField(manifest, name, newField, newType).manifest);
          setNewField("");
        }}>Add field</Button>
      </div>
      <p className="text-xs text-muted-foreground">
        A change is a draft until you press Save. A Live field keeps its type, required flag, options and label at publish. Change one and publish is refused until you press Revert.
      </p>

      {rules.length > 0 ? (
        <section aria-label="Rules" className="rounded-xl border border-border bg-card p-3">
          <h2 className="mb-1 text-sm font-semibold text-foreground">Rules</h2>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {rules.map((r, i) => (
              <li key={i}>{str(r.name) || str(r.kind) || `rule ${i + 1}`}{str(r.kind) && str(r.name) ? ` (${str(r.kind)})` : ""}</li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-muted-foreground">Edit rules in the Code tab, or ask Mira.</p>
        </section>
      ) : null}

      <ConfirmDialog open={ask !== null} title={words?.title ?? ""} subject={ask?.kind === "object" ? ask.name : ask?.key ?? null}
        body={words?.body ?? ""} confirmLabel={words?.blocked ? "Close" : "Delete"} cancelLabel="Keep it"
        confirmVariant={words?.blocked ? "primary" : "destructive"} icon="Trash2" onConfirm={confirm} onCancel={() => setAsk(null)} />
    </div>
  );
}
