"use client";

/**
 * The Field map card of the CRM settings page (WS-53 CRM-Q7a, spec section 18.7).
 *
 * For each entity it lists the Zoho fields that go to `custom` today. For each
 * field it shows the rule, if one exists: the target column, the status, who
 * proposed it and why. An admin can approve or reject a proposal, reject an
 * approved rule, or pick a target by hand. The next sync applies the choice.
 *
 * It is for an admin in Mirror mode only. The page does not draw it in Native
 * mode. The gateway decides which columns a field may fill (`targets`), so
 * this card offers only those.
 *
 * "Suggest mappings" (CRM-Q7b) asks the assistant for one entity. Its answers
 * arrive as proposals, each with approve and reject, and nothing is mapped
 * until the admin approves. A failure shows its sentence under the entity.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";
import { useState } from "react";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import {
  FIELD_MAP_NOTE,
  FIELD_MAP_TITLE,
  NO_FIELDS,
  NO_TARGET,
  STALE_SENTENCE,
  SUGGEST_FAILED,
  SUGGEST_LABEL,
  actionsFor,
  columnLabel,
  pickOptions,
  ruleFor,
  ruleLine,
  statusLabel,
  statusTone,
  suggestionLine,
  type FieldMap,
  type FieldMapField,
  type FieldRule,
} from "../lib/fieldMap";
import type { EntitySlug } from "../lib/types";
import { FIELD_ENTITIES } from "./FieldsCard";
import { Card } from "./SettingsCards";

const KEY_PREFIX = "crm:field-map:";

export type FieldMapHandlers = {
  /** The key of the field whose request runs, or `null`. */
  busy: string | null;
  onPick: (entity: EntitySlug, field: string, column: string) => void;
  onDecide: (rule: FieldRule, status: "approved" | "rejected") => void;
  /** CRM-Q7b. Without it the card draws no "Suggest mappings" button. */
  onSuggest?: (entity: EntitySlug) => void;
  /** The entity whose suggestion runs, or `null`. */
  suggesting?: EntitySlug | null;
  /** The sentence of a failed suggestion, by entity. */
  suggestErrors?: Partial<Record<EntitySlug, string>>;
};

/** The "Suggest mappings" button of one entity. */
function SuggestControl({
  entity,
  map,
  handlers,
}: {
  entity: EntitySlug;
  map: FieldMap | null;
  handlers: FieldMapHandlers;
}) {
  const { onSuggest } = handlers;
  if (!onSuggest || map === null || map.fields.length === 0) return null;
  const running = handlers.suggesting === entity;
  return (
    <Button
      size="sm"
      variant="ghost"
      icon="Sparkles"
      loading={running}
      disabled={running || Boolean(handlers.suggesting) || handlers.busy !== null}
      onClick={() => onSuggest(entity)}
    >
      {SUGGEST_LABEL}
    </Button>
  );
}

/** One field: its type, its rule, and the actions on it. */
function FieldRow({
  entity,
  field,
  rule,
  handlers,
}: {
  entity: EntitySlug;
  field: FieldMapField;
  rule: FieldRule | null;
  handlers: FieldMapHandlers;
}) {
  const [column, setColumn] = useState("");
  const actions = actionsFor(rule, field);
  const busy = handlers.busy === `${entity}:${field.key}`;
  return (
    <li className="space-y-1.5 py-2" aria-label={field.label}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-foreground">{field.label}</span>
        <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">
          {field.type}
        </span>
        {rule && (
          <>
            <span className="text-muted-foreground">to</span>
            <span className="text-foreground">{columnLabel(rule.target_column)}</span>
            <Badge tone={statusTone(rule.status)} size="xs">
              {statusLabel(rule.status)}
            </Badge>
          </>
        )}
      </div>
      {rule && <p className="text-[11px] text-muted-foreground">{ruleLine(rule)}</p>}
      {rule?.status === "stale" && (
        <p className="text-[11px] text-warning">{STALE_SENTENCE}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {actions.includes("approve") && rule && (
          <Button
            size="sm"
            variant="secondary"
            loading={busy}
            disabled={busy}
            onClick={() => handlers.onDecide(rule, "approved")}
          >
            Approve
          </Button>
        )}
        {actions.includes("reject") && rule && (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => handlers.onDecide(rule, "rejected")}
          >
            Reject
          </Button>
        )}
        {actions.includes("pick") && (
          <>
            <SelectButton
              label="Target column"
              prompt="Pick a target"
              value={column}
              defaultValue=""
              options={pickOptions(field)}
              onChange={setColumn}
              widthClass="w-48"
              disabled={busy}
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || !column}
              loading={busy && !rule}
              onClick={() => handlers.onPick(entity, field.key, column)}
            >
              Map
            </Button>
          </>
        )}
        {actions.length === 0 && (
          <span className="text-[11px] text-muted-foreground">{NO_TARGET}</span>
        )}
      </div>
    </li>
  );
}

/** The card, drawn from maps that the caller already holds. */
export function FieldMapCardView({
  maps,
  handlers,
}: {
  maps: Record<EntitySlug, FieldMap | null>;
  handlers: FieldMapHandlers;
}) {
  return (
    <Card title={FIELD_MAP_TITLE} note={FIELD_MAP_NOTE}>
      <div className="divide-y divide-border/60">
        {FIELD_ENTITIES.map(({ slug, label }) => {
          const map = maps[slug];
          const error = handlers.suggestErrors?.[slug];
          return (
            <div key={slug} aria-label={label} className="px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {label}
                </h3>
                <SuggestControl entity={slug} map={map} handlers={handlers} />
              </div>
              {error && (
                <p role="alert" className="pt-1 text-[11px] text-destructive">
                  {error}
                </p>
              )}
              {map === null ? (
                <p className="pt-2 text-xs text-muted-foreground">Loading.</p>
              ) : map.fields.length === 0 ? (
                <p className="pt-2 text-xs text-muted-foreground">{NO_FIELDS}</p>
              ) : (
                <ul className="divide-y divide-border/40">
                  {map.fields.map((field) => (
                    <FieldRow
                      key={field.key}
                      entity={slug}
                      field={field}
                      rule={ruleFor(map, field.key)}
                      handlers={handlers}
                    />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function useFieldMap(entity: EntitySlug): FieldMap | null {
  const res = useCachedResource<FieldMap | null>(`${KEY_PREFIX}${entity}`, () =>
    api.getFieldMap(entity).catch(() => null),
  );
  return res.data ?? null;
}

/** The card with its data. The page draws it for an admin in Mirror mode. */
export default function FieldMapCard() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState<EntitySlug | null>(null);
  const [suggestErrors, setSuggestErrors] = useState<Partial<Record<EntitySlug, string>>>({});
  const leads = useFieldMap("leads");
  const contacts = useFieldMap("contacts");
  const organizations = useFieldMap("organizations");
  const deals = useFieldMap("deals");

  async function run(key: string, act: () => Promise<unknown>, done: string) {
    setBusy(key);
    try {
      await act();
      toast.show({ key: "crm-field-map", variant: "success", title: done });
    } catch (err) {
      const message = err instanceof CrmError ? err.message : "The change did not save.";
      toast.show({ key: "crm-field-map", variant: "error", title: message });
    } finally {
      setBusy(null);
      invalidate(KEY_PREFIX);
    }
  }

  async function suggest(entity: EntitySlug) {
    setSuggesting(entity);
    setSuggestErrors((errors) => ({ ...errors, [entity]: undefined }));
    try {
      const result = await api.suggestFieldRules(entity);
      toast.show({ key: "crm-field-map", variant: "success", title: suggestionLine(result) });
    } catch (err) {
      const message = err instanceof CrmError && err.message ? err.message : SUGGEST_FAILED;
      setSuggestErrors((errors) => ({ ...errors, [entity]: message }));
    } finally {
      setSuggesting(null);
      // The proposals are rules of the map, so the map reads them again.
      invalidate(KEY_PREFIX);
    }
  }

  const handlers: FieldMapHandlers = {
    busy,
    suggesting,
    suggestErrors,
    onSuggest: (entity) => void suggest(entity),
    onPick: (entity, field, column) =>
      run(
        `${entity}:${field}`,
        () => api.createFieldRule({ entity, field_api_name: field, target_column: column }),
        "Mapped. The next sync fills the column.",
      ),
    onDecide: (rule, status) => {
      const entity =
        FIELD_ENTITIES.find(({ slug }) => slugEntity(slug) === rule.entity)?.slug ?? "leads";
      return run(
        `${entity}:${rule.field_api_name}`,
        () => api.decideFieldRule(rule.id, status),
        status === "approved"
          ? "Approved. The next sync fills the column."
          : "Rejected. The next sync puts the column back.",
      );
    },
  };

  return (
    <FieldMapCardView maps={{ leads, contacts, organizations, deals }} handlers={handlers} />
  );
}

/** The stored entity word of an API slug. */
function slugEntity(slug: EntitySlug): string {
  return slug === "organizations" ? "company" : slug.replace(/s$/, "");
}
