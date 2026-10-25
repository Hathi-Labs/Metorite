"use client";

/**
 * The one Add a product / Add a topic form (WS-61 KC-8d,
 * `knowledge_curation.md` §6). A name, optional aliases, and a space.
 *
 * The form offers only the spaces the member can write in, and the company
 * space first when they can. The gateway checks the space again. A refusal
 * (403 or 404) reads "You cannot add to that space."
 *
 * ⚠️ The route is not get-or-make for a product or a topic: a second call
 * with the same name makes a second entity. So the form reads the directory
 * for that name first, and stops when the name or an alias is taken.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";

import { KnowledgeError } from "../lib/api";
import { createEntity, listEntities, typeNoun, type CreatableType, type Entity } from "../lib/entities";
import {
  ALIASES_MAX,
  ALIAS_MAX,
  NAME_MAX,
  entitySpaces,
  defaultEntitySpace,
  sameNameIn,
  splitAliases,
} from "../lib/entityForm";
import type { Space } from "../lib/types";

export default function AddEntityDialog({
  type,
  spaces,
  onClose,
  onCreated,
}: {
  /** The type to add, or `null` when the form is closed. */
  type: CreatableType | null;
  spaces: readonly Space[];
  onClose: () => void;
  onCreated: (entity: Entity) => void;
}) {
  return (
    <Modal
      open={type !== null}
      onClose={onClose}
      title={type === "topic" ? "Add a topic" : "Add a product"}
      icon={type === "topic" ? "Tag" : "Package"}
      size="sm"
      closeLabel="Cancel"
    >
      {type ? <AddEntityForm type={type} spaces={spaces} onClose={onClose} onCreated={onCreated} /> : null}
    </Modal>
  );
}

export function AddEntityForm({
  type,
  spaces,
  onClose,
  onCreated,
}: {
  type: CreatableType;
  spaces: readonly Space[];
  onClose: () => void;
  onCreated: (entity: Entity) => void;
}) {
  const choices = entitySpaces(spaces);
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [space, setSpace] = useState(() => defaultEntitySpace(spaces));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spaceField = useId();
  const noun = typeNoun(type);

  /** Move the words in the alias box into the chips. Returns the full list. */
  function commitDraft(): string[] {
    const next = [...aliases];
    for (const word of splitAliases(draft)) {
      if (!next.some((a) => a.toLowerCase() === word.toLowerCase())) next.push(word);
    }
    const kept = next.slice(0, ALIASES_MAX);
    setAliases(kept);
    setDraft("");
    return kept;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const clean = name.split(/\s+/).filter(Boolean).join(" ");
    if (!clean || !space || saving) return;
    const all = commitDraft();
    setSaving(true);
    setError(null);
    try {
      // The route makes a new entity on each call, so look for the name first.
      // A failed look does not stop the save: the gateway is the authority.
      const found = await listEntities(type, clean).catch(() => null);
      const taken = found ? sameNameIn(found.entities, clean) : null;
      if (taken) {
        setError(`A ${noun} named ${taken.name} already exists. Open it from the list.`);
        setSaving(false);
        return;
      }
      const made = await createEntity({ type, name: clean, aliases: all, space_id: space });
      onCreated(made);
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setError(`Could not add the ${noun}. ${reason}`);
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="contents" data-add-entity={type}>
      <div className="space-y-3 p-4">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-foreground">Name</span>
          <Input
            autoFocus
            value={name}
            maxLength={NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            placeholder={type === "topic" ? "For example: Refunds" : "For example: Sapphire printer"}
            className="w-full"
          />
        </label>
        <div className="space-y-1">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-foreground">Other names (optional)</span>
            <Input
              value={draft}
              maxLength={ALIAS_MAX}
              onChange={(e) => {
                const v = e.target.value;
                if (v.includes(",")) {
                  setAliases((cur) => {
                    const next = [...cur];
                    for (const w of splitAliases(v)) {
                      if (!next.some((a) => a.toLowerCase() === w.toLowerCase())) next.push(w);
                    }
                    return next.slice(0, ALIASES_MAX);
                  });
                  setDraft("");
                } else {
                  setDraft(v);
                }
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) {
                  e.preventDefault();
                  commitDraft();
                }
              }}
              placeholder="Type a name, then a comma or Enter"
              className="w-full"
            />
          </label>
          {aliases.length > 0 ? (
            <ul className="flex flex-wrap gap-1" aria-label="Other names">
              {aliases.map((alias) => (
                <li key={alias}>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    icon="X"
                    aria-label={`Remove ${alias}`}
                    onClick={() => setAliases((cur) => cur.filter((a) => a !== alias))}
                  >
                    {alias}
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="space-y-1">
          <label htmlFor={spaceField} className="block text-xs font-medium text-foreground">
            Space
          </label>
          <SelectButton
            id={spaceField}
            label="Space"
            value={space}
            options={choices}
            onChange={setSpace}
            widthClass="w-full"
          />
        </div>
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </div>
      <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={saving} disabled={saving || !name.trim() || !space}>
          {type === "topic" ? "Add topic" : "Add product"}
        </Button>
      </footer>
    </form>
  );
}
