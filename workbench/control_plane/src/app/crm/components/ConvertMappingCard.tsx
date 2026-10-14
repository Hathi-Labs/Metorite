"use client";

/**
 * The lead-conversion mapping of a Native CRM (WS-53 CRM-C6a, spec section
 * 20.9). It is the card of the `field-mapping` section in Native mode.
 *
 * One table for each target entity: Contact, Company and Deal. Each row is a
 * custom lead field and a `SelectButton` with Auto, Skip and the target fields
 * that the gateway says can take its value. A choice writes the whole map
 * through `PUT /crm/conversion-mapping`, then the card reads it again.
 *
 * The page draws this card only for an admin of a Native org whose layout
 * says `studio_admin` (`settingsSections.ts`). The Mirror Field map
 * (`FieldMapCard`, CRM-Q7a) is another card, and this one never draws there.
 * The card does not navigate.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { FIELD_MAP_TITLE } from "../lib/fieldMap";
import {
  CONVERT_MAPPING_KEY,
  CONVERT_MAPPING_NOTE,
  MAP_ENTITIES,
  MAPPING_READ_FAILED,
  NO_LEAD_FIELDS,
  offersSetToAuto,
  problemText,
  AUTO,
  choiceOf,
  choiceOptions,
  getConversionMapping,
  putConversionMapping,
  rowsWith,
  tableRows,
  type ConversionMapping,
  type MapEntity,
} from "../lib/convertMapping";
import { Card } from "./SettingsCards";

/** The tables, drawn from a served map. Pure, so the test renders it. */
export function ConvertMappingView({
  mapping,
  saving,
  error,
  onChange,
}: {
  mapping: ConversionMapping;
  saving: boolean;
  /** The message of the last refused write, or null. */
  error: string | null;
  onChange: (source: string, entity: MapEntity, choice: string) => void;
}) {
  return (
    <Card title={FIELD_MAP_TITLE} note={CONVERT_MAPPING_NOTE}>
      {error && (
        <p
          role="alert"
          className="border-b border-border px-4 py-2 text-xs text-destructive"
        >
          {error}
        </p>
      )}
      {mapping.fields.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {NO_LEAD_FIELDS}
        </p>
      ) : (
        <div className="divide-y divide-border">
          {MAP_ENTITIES.map(({ entity, label }) => (
            <div key={entity} className="px-4 py-3">
              <h3 className="mb-2 text-xs font-semibold text-foreground">
                Lead to {label}
              </h3>
              <ul className="space-y-1.5" aria-label={`Lead to ${label}`}>
                {tableRows(mapping, entity).map(({ field, cell }) => (
                  <li
                    key={field.key}
                    className="grid grid-cols-1 items-center gap-1 sm:grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <span className="min-w-0 text-xs text-foreground">
                      <span className="truncate">{field.label}</span>
                      <span className="ml-1.5 text-[10px] text-muted-foreground">
                        {field.type}
                      </span>
                      {cell.problem && (
                        <span className="block text-[10px] text-warning">
                          {problemText(cell, mapping.targets[entity])}
                        </span>
                      )}
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5">
                      {offersSetToAuto(cell) && (
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={saving}
                          onClick={() => onChange(field.key, entity, AUTO)}
                        >
                          Set to Auto
                        </Button>
                      )}
                      <SelectButton
                        label={`${field.label} to ${label}`}
                        value={choiceOf(cell)}
                        options={choiceOptions(cell, mapping.targets[entity])}
                        onChange={(choice) =>
                          onChange(field.key, entity, choice)
                        }
                        disabled={saving}
                        widthClass="w-full sm:w-56"
                      />
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/** The card with its own read and its writes. */
export default function ConvertMappingCard() {
  const res = useCachedResource<ConversionMapping>(
    CONVERT_MAPPING_KEY,
    getConversionMapping,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(
    source: string,
    entity: MapEntity,
    choice: string,
  ): Promise<void> {
    if (!res.data || saving) return;
    setSaving(true);
    setError(null);
    try {
      await putConversionMapping(
        rowsWith(res.data, { source, entity, choice }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
      // Also after a refusal: another admin may have changed the map.
      res.refresh();
    }
  }

  if (res.data === undefined && res.error === null) {
    return (
      <Card title={FIELD_MAP_TITLE}>
        <SkeletonRows count={4} className="p-4" />
      </Card>
    );
  }
  if (res.data === undefined) {
    return (
      <Card title={FIELD_MAP_TITLE}>
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 px-4 py-3 text-xs text-muted-foreground"
        >
          <span>
            {MAPPING_READ_FAILED} {res.error}
          </span>
          <Button
            variant="secondary"
            size="sm"
            icon="RefreshCw"
            onClick={res.refresh}
          >
            Retry
          </Button>
        </div>
      </Card>
    );
  }
  return (
    <ConvertMappingView
      mapping={res.data}
      saving={saving}
      error={error}
      onChange={(source, entity, choice) => void change(source, entity, choice)}
    />
  );
}
