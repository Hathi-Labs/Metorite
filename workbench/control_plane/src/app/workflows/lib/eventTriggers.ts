/**
 * Event triggers of a workflow, as the Trigger panel edits them.
 *
 * A workflow may hold SEVERAL event triggers. Each helper here works on one
 * event trigger by its place among the event triggers, and it never touches
 * the other triggers. The Studio source takes a filter of three keys. The
 * gateway refuses any other key (RECORD_FILTER_KEYS in
 * gateway/routes/workflows/triggers.py), so the panel writes only these.
 */

import type { TriggerSpec } from "./types";

/** Sources the picker offers. A stored source that is not here shows as retired. */
export const EVENT_SOURCES: { value: string; label: string }[] = [
  { value: "zoho", label: "Zoho" },
  { value: "gmail", label: "Gmail" },
  { value: "custom", label: "Custom" },
  { value: "studio", label: "Studio records" },
];

export const KNOWN_EVENT_SOURCES = EVENT_SOURCES.map((s) => s.value);

export const STUDIO_SOURCE = "studio";

export const STUDIO_EVENT_TYPES: { value: string; label: string }[] = [
  { value: "studio.record.created", label: "Record created" },
  { value: "studio.record.updated", label: "Record updated" },
  { value: "studio.record.deleted", label: "Record deleted" },
];

/** The three values the gateway accepts for the `op` filter. */
export const STUDIO_OPS: { value: string; label: string }[] = [
  { value: "create", label: "Create" },
  { value: "update", label: "Update" },
  { value: "delete", label: "Delete" },
];

export const FILTER_KEYS = ["package_id", "object", "op"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

export function sourceOf(t: TriggerSpec): string {
  return String(t.config.source ?? "zoho");
}

export function isStudio(t: TriggerSpec): boolean {
  return sourceOf(t) === STUDIO_SOURCE;
}

/** The indexes (in the whole list) of the event triggers, in order. */
export function eventIndexes(triggers: TriggerSpec[]): number[] {
  const out: number[] = [];
  triggers.forEach((t, i) => {
    if (t.kind === "event") out.push(i);
  });
  return out;
}

/** Add a new event trigger. It starts on the Zoho source, as the old checkbox did. */
export function addEvent(triggers: TriggerSpec[]): TriggerSpec[] {
  return [
    ...triggers,
    { kind: "event", enabled: true, config: { source: "zoho" } },
  ];
}

/** Replace the trigger at `index`, keeping every other trigger as it is. */
export function patchAt(
  triggers: TriggerSpec[],
  index: number,
  patch: Partial<TriggerSpec>,
): TriggerSpec[] {
  return triggers.map((t, i) => (i === index ? { ...t, ...patch } : t));
}

export function removeAt(triggers: TriggerSpec[], index: number): TriggerSpec[] {
  return triggers.filter((_, i) => i !== index);
}

/**
 * Change the source of one event trigger. A change away from Studio drops the
 * filter, because only Studio reads it. A change to Studio drops a stale
 * event type, because no other source names a Studio event.
 */
export function setSource(
  triggers: TriggerSpec[],
  index: number,
  source: string,
): TriggerSpec[] {
  const t = triggers[index];
  if (!t) return triggers;
  const config: Record<string, unknown> = { ...t.config, source };
  const type = String(config.event_type ?? "");
  if (source !== STUDIO_SOURCE) delete config.filter;
  if ((source === STUDIO_SOURCE) !== type.startsWith("studio.")) delete config.event_type;
  return patchAt(triggers, index, { config });
}

/** Set the event type. A blank Studio type is left out, so the trigger matches all three. */
export function setEventType(
  triggers: TriggerSpec[],
  index: number,
  eventType: string,
): TriggerSpec[] {
  const t = triggers[index];
  if (!t) return triggers;
  const config = { ...t.config };
  if (isStudio(t) && eventType === "") delete config.event_type;
  else config.event_type = eventType;
  return patchAt(triggers, index, { config });
}

/** Read the three filter values of an event trigger as plain strings. */
export function filterOf(t: TriggerSpec): Record<FilterKey, string> {
  const raw = (t.config.filter ?? {}) as Record<string, unknown>;
  return {
    package_id: String(raw.package_id ?? ""),
    object: String(raw.object ?? ""),
    op: String(raw.op ?? ""),
  };
}

/**
 * Set one filter key. A value that is empty after trim leaves the key out. When
 * no key is left, the whole `filter` leaves the config. A key that the panel
 * does not own stays as it was.
 */
export function setFilterKey(
  triggers: TriggerSpec[],
  index: number,
  key: FilterKey,
  value: string,
): TriggerSpec[] {
  const t = triggers[index];
  if (!t) return triggers;
  const filter: Record<string, unknown> = {
    ...((t.config.filter ?? {}) as Record<string, unknown>),
  };
  // The panel keeps what the member types, and `cleanFilter` trims on save.
  if (value.trim() === "") delete filter[key];
  else filter[key] = value;
  const config = { ...t.config };
  if (Object.keys(filter).length === 0) delete config.filter;
  else config.filter = filter;
  return patchAt(triggers, index, { config });
}

/**
 * The list as the editor saves it. Each Studio filter value is trimmed, and an
 * empty value or an empty filter is left out. The gateway answers 422 for an
 * empty or untrimmed value, so the panel does not send one.
 */
export function cleanTriggers(triggers: TriggerSpec[]): TriggerSpec[] {
  return triggers.map((t) => {
    if (t.kind !== "event" || !t.config.filter) return t;
    const filter: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(t.config.filter as Record<string, unknown>)) {
      const s = typeof v === "string" ? v.trim() : v;
      if (s !== "" && s !== undefined && s !== null) filter[k] = s;
    }
    const config = { ...t.config };
    if (Object.keys(filter).length === 0) delete config.filter;
    else config.filter = filter;
    return { ...t, config };
  });
}
