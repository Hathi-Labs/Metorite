"use client";

/**
 * The input of one field, by its type (WS-53 CRM-C4p item 6).
 *
 * Spec: project-docs/specs/crm_platform.md section 20.7, "CRM-C4p: the shared
 * client", the table "The input of each type". C4b draws each custom value of
 * the create form and the Fields tab with it, and C4c draws a default value
 * with it.
 *
 * - The value in and the value out are the JSON value that the record holds.
 *   A text input keeps its own draft, and each edit sends `toWire(draft)`, so
 *   a number is a JSON number and a time has its `Z`.
 * - The caller runs `checkValue` before a save and passes the message as
 *   `error`. The input shows it under itself.
 * - With `locked`, the input draws the value read only, with `LOCK_TIP`. A
 *   Mirror record stays locked until CRM-C5.
 * - Only the shared primitives: `Input`, `Textarea`, `SelectButton` and
 *   `Checkbox`. `crmX4.test.ts` refuses a raw control under `app/crm`.
 */

import { useEffect, useId, useState } from "react";

import Icon from "@/components/Icon";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import { SelectButton, type SelectOption } from "@/components/ui/SelectButton";
import * as api from "../lib/api";
import { LOCK_TIP } from "../lib/connect";
import { fieldOptions, formatField, type FieldDef } from "../lib/fields";
import {
  fromWire,
  isLookupTarget,
  lookupValue,
  toWire,
} from "../lib/fieldLayout";
import type { EntitySlug } from "../lib/types";
import FieldValue from "./FieldValue";

export type FieldInputProps = {
  def: FieldDef;
  /** The JSON value of the field, as the record holds it. */
  value: unknown;
  /** The JSON value to save. `null` clears the field. */
  onChange: (next: unknown) => void;
  /** The message of the client check or of a 422. It shows under the input. */
  error?: string | null;
  /** A Mirror record: the value reads only, with `LOCK_TIP`. */
  locked?: boolean;
  /**
   * What the record holds now. A retired option that it holds stays a
   * choice. Absent: the value of the first draw.
   */
  held?: unknown;
  /** The id of the control, for a `<label htmlFor>`. */
  id?: string;
};

/** The text input of each text-like type, from the table of the spec. */
const TEXT_INPUTS: Record<string, React.InputHTMLAttributes<HTMLInputElement>> = {
  text: {},
  number: { inputMode: "numeric" },
  decimal: { inputMode: "decimal" },
  currency: { inputMode: "decimal" },
  percent: { inputMode: "decimal" },
  date: { type: "date" },
  datetime: { type: "datetime-local" },
  email: { type: "email" },
  phone: { type: "tel" },
  url: { type: "url" },
};

/** The JSON text of a value, to see whether two values are the same. */
function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

export default function FieldInput({
  def,
  value,
  onChange,
  error = null,
  locked = false,
  held,
  id,
}: FieldInputProps) {
  const ownId = useId();
  const controlId = id ?? ownId;
  const [firstValue] = useState(value);
  const kept = held === undefined ? firstValue : held;

  if (locked) {
    return (
      <span className="flex items-start gap-1.5 text-xs text-foreground" title={LOCK_TIP}>
        <span className="min-w-0 flex-1 break-words">
          <FieldValue value={formatField(def, value)} />
        </span>
        <Icon
          name="Lock"
          className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground"
          aria-label={LOCK_TIP}
        />
      </span>
    );
  }

  return (
    <div className="min-w-0">
      <Control def={def} value={value} onChange={onChange} kept={kept} id={controlId} invalid={!!error} />
      {def.help_text ? (
        <p className="mt-1 text-[11px] text-muted-foreground">{def.help_text}</p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-[11px] text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

type ControlProps = {
  def: FieldDef;
  value: unknown;
  onChange: (next: unknown) => void;
  kept: unknown;
  id: string;
  invalid: boolean;
};

function Control(props: ControlProps) {
  const { def } = props;
  if (def.type === "textarea" || Object.hasOwn(TEXT_INPUTS, def.type)) {
    return <TextControl {...props} />;
  }
  switch (def.type) {
    case "boolean":
      return (
        <Checkbox
          id={props.id}
          checked={props.value === true}
          onChange={(e) => props.onChange(e.target.checked)}
          aria-label={def.label}
          aria-invalid={props.invalid || undefined}
        />
      );
    case "select":
      return <SelectControl {...props} />;
    case "multiselect":
      return <MultiControl {...props} />;
    case "lookup":
      return <LookupControl {...props} />;
    default:
      // A type that the studio does not know reads only.
      return (
        <span className="text-xs text-foreground">
          <FieldValue value={formatField(def, props.value)} />
        </span>
      );
  }
}

/**
 * A text-like input. It keeps the text that the member types, and each edit
 * sends `toWire(text)`. A new value from the caller (a reset, a default)
 * replaces the draft, unless the draft already gives that value.
 */
function TextControl({ def, value, onChange, id, invalid }: ControlProps) {
  const [draft, setDraft] = useState(() => fromWire(def, value));
  const [seen, setSeen] = useState(value);
  if (!same(seen, value)) {
    setSeen(value);
    if (!same(toWire(def, draft), value)) setDraft(fromWire(def, value));
  }
  const edit = (text: string) => {
    setDraft(text);
    onChange(toWire(def, text));
  };
  if (def.type === "textarea") {
    return (
      <Textarea
        id={id}
        rows={3}
        aria-label={def.label}
        aria-invalid={invalid || undefined}
        placeholder="—"
        value={draft}
        onChange={(e) => edit(e.target.value)}
      />
    );
  }
  return (
    <Input
      id={id}
      inputSize="sm"
      aria-label={def.label}
      aria-invalid={invalid || undefined}
      placeholder="—"
      {...TEXT_INPUTS[def.type]}
      value={draft}
      onChange={(e) => edit(e.target.value)}
    />
  );
}

/** The value that clears a picker. */
const NONE: SelectOption = { value: "", label: "—" };

/** The options a picker offers: each active option, and each one the record holds. */
function choices(def: FieldDef, kept: unknown, value: unknown): SelectOption[] {
  const holds = new Set(
    [...(Array.isArray(kept) ? kept : [kept]), ...(Array.isArray(value) ? value : [value])].filter(
      (v): v is string => typeof v === "string",
    ),
  );
  return fieldOptions(def)
    .filter((o) => !o.retired || holds.has(o.value))
    .map((o) => ({ value: o.value, label: o.label }));
}

function SelectControl({ def, value, onChange, kept }: ControlProps) {
  return (
    <SelectButton
      label={def.label}
      value={typeof value === "string" ? value : ""}
      options={[NONE, ...choices(def, kept, value)]}
      onChange={(next) => onChange(next === "" ? null : next)}
      prompt="—"
      widthClass="w-full"
    />
  );
}

function MultiControl({ def, value, onChange, kept, id }: ControlProps) {
  const chosen = Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
  const toggle = (option: string, on: boolean) => {
    const next = on ? [...chosen.filter((v) => v !== option), option] : chosen.filter((v) => v !== option);
    onChange(next.length > 0 ? next : null);
  };
  return (
    <div id={id} role="group" aria-label={def.label} className="flex flex-wrap gap-x-3 gap-y-1">
      {choices(def, kept, value).map((o) => (
        <label key={o.value} className="flex items-center gap-1.5 text-xs text-foreground">
          <Checkbox
            size="sm"
            checked={chosen.includes(o.value)}
            onChange={(e) => toggle(o.value, e.target.checked)}
          />
          {o.label}
        </label>
      ))}
    </div>
  );
}

/** How long the lookup search waits after the last key. */
export const LOOKUP_WAIT_MS = 250;

/** The most hits the lookup search asks for. */
export const LOOKUP_PAGE = 10;

/**
 * A lookup: an `Input` for the search and a `SelectButton` for the hits. The
 * search is the `?q=` list search of CRM-X1 on `lookup_target` only. A pick
 * stores `{id, name}`, and the gateway checks the `id` again.
 */
function LookupControl({ def, value, onChange, id, invalid }: ControlProps) {
  const target: EntitySlug | null = isLookupTarget(def.lookup_target) ? def.lookup_target : null;
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Array<{ id: string; name: string }>>([]);

  useEffect(() => {
    const q = query.trim();
    if (!target || !q) return;
    let live = true;
    const timer = setTimeout(() => {
      api
        .listRecords(target, { q, page_size: LOOKUP_PAGE })
        .then((page) => {
          if (live) setHits(page.rows.map((row) => lookupValue(target, row)));
        })
        .catch(() => {
          if (live) setHits([]);
        });
    }, LOOKUP_WAIT_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, target]);

  const current =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as { id?: unknown; name?: unknown })
      : null;
  const currentId = typeof current?.id === "string" ? current.id : "";
  const currentName = typeof current?.name === "string" ? current.name : "";
  const options: SelectOption[] = [
    NONE,
    ...(currentId ? [{ value: currentId, label: currentName || currentId }] : []),
    ...hits.filter((h) => h.id !== currentId).map((h) => ({ value: h.id, label: h.name || h.id })),
  ];

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Input
        id={id}
        inputSize="sm"
        icon="Search"
        aria-label={`Search for ${def.label}`}
        aria-invalid={invalid || undefined}
        placeholder={target ? `Search ${target}` : "This lookup has no target"}
        disabled={!target}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <SelectButton
        label={def.label}
        value={currentId}
        options={options}
        onChange={(next) => {
          if (next === "") return onChange(null);
          const hit = hits.find((h) => h.id === next);
          onChange(hit ?? { id: next, name: currentName });
        }}
        prompt="—"
        widthClass="w-full"
        disabled={!target}
      />
    </div>
  );
}
