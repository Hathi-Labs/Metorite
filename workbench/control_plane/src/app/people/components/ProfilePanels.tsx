"use client";

/**
 * People Center · the profile panels (WS-28g).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.2 panels 5-6, §5.3.
 *
 * **One component, two entry points.** `/people/me` and the person page render
 * the same panels — the difference is only which row was fetched — so a field
 * added to the catalogue cannot be present on one and missing on the other.
 *
 * Two rules it exists to obey:
 *
 * 1. **Editability comes from the server.** Every control asks whether its
 *    field name is in `editable_fields`; nothing here has an opinion about who
 *    may write what (D-PC-4). A field the caller may not write is drawn
 *    read-only rather than hidden, because a person should be able to SEE what
 *    the company records about them.
 * 2. **A refusal is shown verbatim.** The gateway answers 403 naming the
 *    fields it refused and applies nothing; that sentence is the only form of
 *    the message anyone can act on, so it is displayed rather than replaced
 *    with "save failed" (D-PC-5).
 */

import { useMemo, useState } from "react";
import Link from "next/link";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input, Select, Textarea } from "@/components/ui/Input";

import { type PersonDetail, peopleApi } from "../lib/api";
import { describeSchedule, formatHours, overriddenFields } from "../lib/schedule";
import { SkillsPanel } from "./SkillsPanel";
import {
  type FieldSpec,
  type RenderedField,
  changedFields,
  completeness,
  formatChips,
  isFilled,
  parseChips,
  renderSections,
} from "../lib/profile";

interface Props {
  person: PersonDetail;
  /** Hide the private panel where it has no business being — a colleague's page. */
  includePrivate?: boolean;
  /** Show the "what the AI still cannot see about you" meter (`/people/me`). */
  showCompleteness?: boolean;
  onSaved?: (person: PersonDetail) => void;
}

/** A read-only value, rendered so "restricted" never looks like "empty". */
function ReadValue({ field }: { field: RenderedField }) {
  const { spec, value } = field;
  if (!isFilled(value)) {
    return <span className="text-muted-foreground">—</span>;
  }
  if (Array.isArray(value)) {
    return (
      <span className="flex flex-wrap gap-1">
        {value.map((v) => (
          <span
            key={String(v)}
            className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-foreground"
          >
            {String(v)}
          </span>
        ))}
      </span>
    );
  }
  if (spec.kind === "links" || spec.kind === "hours" || spec.kind === "contact") {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <span className="flex flex-col gap-0.5">
        {entries.map(([k, v]) => (
          <span key={k} className="text-xs">
            <span className="text-muted-foreground">{k}: </span>
            {Array.isArray(v) ? v.join(", ") : String(v)}
          </span>
        ))}
      </span>
    );
  }
  return <span className="text-foreground">{String(value)}</span>;
}

function EditControl({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const str = value === null || value === undefined ? "" : String(value);
  switch (spec.kind) {
    case "textarea":
      return (
        <Textarea
          inputSize="sm"
          rows={3}
          value={str}
          placeholder={spec.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "number":
      return (
        <Input
          inputSize="sm"
          type="number"
          value={str}
          onChange={(e) =>
            onChange(e.target.value === "" ? null : Number(e.target.value))
          }
        />
      );
    case "date":
      return (
        <Input
          inputSize="sm"
          type="date"
          value={str}
          onChange={(e) => onChange(e.target.value || null)}
        />
      );
    case "select":
      return (
        <Select
          inputSize="sm"
          value={str}
          onChange={(e) => onChange(e.target.value || null)}
        >
          <option value="">—</option>
          {(spec.options ?? []).map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </Select>
      );
    case "chips":
      return (
        <Input
          inputSize="sm"
          value={formatChips(value)}
          placeholder="comma, separated"
          onChange={(e) => onChange(parseChips(e.target.value))}
        />
      );
    case "hours":
    case "links":
    case "contact": {
      // A small key/value editor rather than a JSON textarea: the shape is
      // three or four named keys, and asking somebody to type valid JSON to
      // record a phone number is a form that will be filled in wrong.
      const record = (value ?? {}) as Record<string, unknown>;
      const keys =
        spec.kind === "hours"
          ? ["days", "start", "end"]
          : spec.kind === "contact"
            ? ["name", "relation", "phone"]
            : ["github", "linkedin", "website"];
      return (
        <div className="flex flex-col gap-1">
          {keys.map((k) => (
            <label key={k} className="flex items-center gap-2">
              <span className="w-16 shrink-0 text-[11px] text-muted-foreground">
                {k}
              </span>
              <Input
                inputSize="sm"
                value={
                  Array.isArray(record[k])
                    ? (record[k] as unknown[]).join(",")
                    : record[k] === undefined || record[k] === null
                      ? ""
                      : String(record[k])
                }
                onChange={(e) => {
                  const next = { ...record };
                  const raw = e.target.value;
                  if (raw === "") delete next[k];
                  else if (k === "days")
                    next[k] = parseChips(raw).map((n) => Number(n));
                  else next[k] = raw;
                  onChange(Object.keys(next).length ? next : null);
                }}
              />
            </label>
          ))}
        </div>
      );
    }
    default:
      return (
        <Input
          inputSize="sm"
          value={str}
          placeholder={spec.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}

export function ProfilePanels({
  person,
  includePrivate = true,
  showCompleteness = false,
  onSaved,
}: Props) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const original = person as unknown as Record<string, unknown>;
  const editableFields = person.editable_fields ?? [];
  const sections = useMemo(
    () => renderSections(person, { includePrivate }),
    [person, includePrivate]
  );
  const pending = changedFields(draft, original, editableFields);
  const dirty = Object.keys(pending).length > 0;
  const meter = completeness(person);

  async function save() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      // Your own row goes through the UNGATED `/people/me` door; somebody
      // else's through the id-bearing one behind `feature:people`. A colleague
      // holding no grant can save their own profile only via the first
      // (D-PC-15), so this is not a cosmetic choice of URL.
      const saved = await peopleApi.update(
        person.is_self ? "me" : person.id,
        pending,
      );
      setDraft({});
      setNotice("Saved.");
      onSaved?.(saved);
    } catch (err) {
      // Verbatim: the gateway's 403 names which fields it refused, and that
      // sentence is the only version of this message anybody can act on.
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {showCompleteness && meter.missing.length > 0 && (
        <section className="rounded-xl border border-border p-3">
          <div className="flex items-center gap-2 text-xs font-medium text-foreground">
            <Icon name="Sparkles" className="h-3.5 w-3.5" />
            {meter.filled} of {meter.total} planning fields filled in
          </div>
          <ul className="mt-2 flex flex-col gap-1">
            {meter.missing.map((m) => (
              <li key={m.label} className="text-[11px] text-muted-foreground">
                <span className="text-foreground">{m.label}</span> — {m.why}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/*
        WS-28p — the effective schedule, above the fields that override it.
        The layering is the SERVER's answer; `source` is what lets this say
        which half is the company's and which is yours, rather than showing
        four numbers a person cannot account for (§3.4a).
      */}
      {person.schedule && (
        <section className="rounded-xl border border-border p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-xs font-medium text-foreground">
              Your working week
            </h3>
            {person.contracted_hours_per_week !== null &&
              person.contracted_hours_per_week !== undefined && (
                <span className="text-[11px] text-muted-foreground">
                  {formatHours(person.contracted_hours_per_week)} contracted
                </span>
              )}
          </div>
          <p className="mt-1 text-xs text-foreground">
            {describeSchedule(person.schedule)}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {overriddenFields(person.schedule).length === 0 ? (
              <>
                All from the company default —{" "}
                <Link href="/people/schedule" className="underline">
                  see the working week
                </Link>
                . Change any of it below.
              </>
            ) : (
              `You override: ${overriddenFields(person.schedule).join(", ")}. Everything else follows the company default.`
            )}
          </p>
          {/*
            The typed capacity is NOT corrected here — surfaced, because
            quietly preferring one number is how two start disagreeing where
            nobody can see them (D-PC-18).
          */}
          {person.capacity_conflict ? (
            <p className="mt-1 text-[11px] text-muted-foreground">
              A capacity of{" "}
              {formatHours(person.capacity_hours_per_week ?? null)} is recorded
              separately, {formatHours(Math.abs(person.capacity_conflict))}{" "}
              {person.capacity_conflict > 0 ? "above" : "below"} the schedule.
              An administrator can reconcile them.
            </p>
          ) : null}
        </section>
      )}

      {sections.map(({ section, fields }) => (
        <section key={section.key} className="rounded-xl border border-border p-3">
          <h3 className="text-xs font-medium text-foreground">{section.title}</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground">{section.note}</p>
          <dl className="mt-3 grid gap-2.5 sm:grid-cols-2">
            {fields.map((field) => (
              <div key={field.spec.name} className="flex flex-col gap-1">
                <dt className="text-[11px] text-muted-foreground">
                  {field.spec.label}
                </dt>
                <dd className="text-xs">
                  {field.editable ? (
                    <EditControl
                      spec={field.spec}
                      value={
                        field.spec.name in draft
                          ? draft[field.spec.name]
                          : field.value
                      }
                      onChange={(next) =>
                        setDraft((d) => ({ ...d, [field.spec.name]: next }))
                      }
                    />
                  ) : (
                    <ReadValue field={field} />
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}

      {/* WS-28h — structured skills & credentials. Its own saves, its own
          endpoint: a skills row is a child record, not a field on the person,
          so it does not ride the PATCH above. */}
      <SkillsPanel person={person} onSaved={() => onSaved?.(person)} />

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="text-xs text-muted-foreground">{notice}</p>
      )}

      {/*
        Absent, not disabled: with nothing editable there is no save button at
        all. A greyed-out control teaches people to hunt for a permission they
        may never get (§5.2).
      */}
      {editableFields.length > 0 && (
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={save} disabled={!dirty} loading={saving}>
            Save changes
          </Button>
          {dirty && (
            <span className="text-[11px] text-muted-foreground">
              {Object.keys(pending).length} field
              {Object.keys(pending).length === 1 ? "" : "s"} changed
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default ProfilePanels;
