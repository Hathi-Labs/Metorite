"use client";

/**
 * "Write the page" for one entity (WS-61 KC-8b, `knowledge_curation.md` §6.2).
 *
 * The act, in this order:
 *
 *   1. A row with no entity yet (`id` null) calls `by-ref` FIRST. The gateway
 *      reads the owner row under the owner app's rule, and makes the pointer.
 *   2. The page call takes that entity id. It sends a space only when the
 *      member changed the default (the company space). The page of a
 *      Knowledge-owned entity (a product, a topic) lives in the entity's own
 *      space, so it sends none.
 *   3. The cache is dropped and the new draft opens in the editor.
 *
 * A draft is for its author alone, so the leak that the warning names starts
 * when the page is published, to everyone who can read the space. The form
 * says so for the four types whose owner app is narrower than a space, and
 * offers another space. It never lists a `me` space: the page of an entity
 * serves the whole org.
 *
 * A 404 means the record is gone for this member. A 409 means the entity has
 * a page already, or has one that the member cannot see. Each shows its own
 * words, and the list reads again.
 */

import { useId, useState } from "react";
import { useRouter } from "next/navigation";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";

import { KNOWLEDGE_CACHE, KnowledgeError } from "../lib/api";
import { entityByRef, makeEntityPage, pageWarning, typeNoun, type Entity, type EntityType } from "../lib/entities";
import { canWriteIn } from "../lib/labels";
import { pageHref } from "../lib/links";
import type { Space } from "../lib/types";
import { useKnowledge } from "./KnowledgeContext";

export const GONE_MESSAGE = "You can't see that record any more.";

export function hasPageMessage(type: EntityType): string {
  return `This ${typeNoun(type)} already has a page. If you can't open it, it's a draft or in a space you can't read.`;
}

/** The spaces that an entity page may go in: where the member writes, never `me`. */
export function pageSpaces(spaces: readonly Space[]): Space[] {
  return spaces.filter((s) => canWriteIn(s.level) && s.kind !== "me" && !s.archived_at);
}

/** The space that the form starts on: Company when the member can write there. */
export function defaultPageSpace(spaces: readonly Space[]): string {
  const choices = pageSpaces(spaces);
  return (choices.find((s) => s.kind === "company") ?? choices[0])?.id ?? "";
}

export default function WritePageDialog({
  type,
  entity,
  onClose,
  onStale,
}: {
  type: EntityType;
  /** The row to write, or `null` when the form is closed. */
  entity: Entity | null;
  onClose: () => void;
  /** The list is out of date (a 404 or a 409). Read it again. */
  onStale: () => void;
}) {
  return (
    <Modal
      open={entity !== null}
      onClose={onClose}
      title="Write the page"
      icon="FilePlus"
      size="sm"
      closeLabel="Cancel"
    >
      {entity ? <WriteForm type={type} entity={entity} onClose={onClose} onStale={onStale} /> : null}
    </Modal>
  );
}

function WriteForm({
  type,
  entity,
  onClose,
  onStale,
}: {
  type: EntityType;
  entity: Entity;
  onClose: () => void;
  onStale: () => void;
}) {
  const { spaces, requestLeave } = useKnowledge();
  const router = useRouter();
  const toast = useToast();
  const spaceField = useId();
  const choices = pageSpaces(spaces);
  const company = choices.find((s) => s.kind === "company")?.id ?? null;
  const [space, setSpace] = useState(() => defaultPageSpace(spaces));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  // A product or a topic keeps its page in its own space, so it has no choice.
  const ownSpace = entity.ref_table === "kb_entities";
  const warning = pageWarning(type);

  async function create() {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const target = entity.id ? entity : await entityByRef(type, entity.ref_id);
      if (!target.id) throw new Error("The entity has no id.");
      const changed = !ownSpace && space !== "" && space !== company;
      const made = await makeEntityPage(target.id, changed ? space : undefined);
      invalidate(KNOWLEDGE_CACHE);
      onClose();
      requestLeave(() => router.push(pageHref(made.page.id, { edit: true })));
    } catch (err) {
      setBusy(false);
      if (err instanceof KnowledgeError && (err.status === 404 || err.status === 409)) {
        setProblem(err.status === 404 ? GONE_MESSAGE : hasPageMessage(type));
        setStale(true);
        invalidate(KNOWLEDGE_CACHE);
        onStale();
        return;
      }
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({
        key: `kc-entity-page:${entity.ref_id}`,
        variant: "error",
        title: "Could not make the page",
        description: reason,
      });
    }
  }

  return (
    <div className="contents">
      <div className="space-y-3 p-4">
        <p className="text-sm text-foreground">
          A page about <span className="font-medium">{entity.name}</span>.
        </p>
        {warning ? (
          <p className="text-xs text-muted-foreground" data-page-warning="">
            {warning}
          </p>
        ) : null}
        {ownSpace ? (
          <p className="text-xs text-muted-foreground">The page goes in the space of this {typeNoun(type)}.</p>
        ) : (
          <div className="space-y-1">
            <label htmlFor={spaceField} className="block text-xs font-medium text-foreground">
              Space
            </label>
            <SelectButton
              id={spaceField}
              label="Space"
              value={space}
              options={choices.map((s) => ({ value: s.id, label: s.name }))}
              onChange={setSpace}
              disabled={busy || stale}
              widthClass="w-full"
            />
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          The page starts as a draft. Only you see it until you publish it.
        </p>
        {problem ? (
          <p role="alert" className="text-xs text-destructive">
            {problem}
          </p>
        ) : null}
      </div>
      <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
          {stale ? "Close" : "Cancel"}
        </Button>
        <Button
          type="button"
          size="sm"
          loading={busy}
          disabled={busy || stale || (!ownSpace && space === "")}
          onClick={() => void create()}
        >
          Create
        </Button>
      </footer>
    </div>
  );
}
