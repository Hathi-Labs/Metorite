"use client";

/**
 * People Center · the person editor (WS-28b-write).
 *
 * Restores the write half that WS-28's scope narrowing removed with the tasks
 * app's People view. `person=null` is "add somebody"; otherwise it edits.
 *
 * Two things this is careful about, both of which cost a whole record if got
 * wrong:
 *
 * 1. **Without `admin:members:read` the HR half is not editable.** The skills
 *    and capacity the gateway projected away come back blank, and a form that
 *    submitted those blanks would erase them. `buildWriteBody` omits the
 *    fields; this component says so where they would have been, so the gap
 *    reads as "restricted" rather than "empty".
 * 2. **The status list comes from the API.** Migration 148 replaced the old
 *    `active | inactive | on_leave` vocabulary with `active | contractor |
 *    alumni | invited` and put a CHECK behind it. A hardcoded list here would
 *    be a save button that fails at the database.
 *
 * The editor is never rendered without `can_manage` — its callers decide that,
 * because a control that appears and then refuses is worse than one that was
 * never there (spec §3.2).
 */

import { useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import type { PersonDetail, PersonRow } from "../lib/api";
import {
  DEFAULT_STATUS,
  type PersonForm,
  addSkill,
  buildWriteBody,
  emptyForm,
  formFromPerson,
  mergeResumeSkills,
  removeSkill,
  resumeNotice,
  validate,
} from "../lib/form";
import { peopleWriteApi } from "../lib/write";

interface Props {
  /** The person being edited, or null to create one. */
  person: PersonDetail | null;
  /** Whether the caller may see (and therefore safely re-save) the HR half. */
  hrVisible: boolean;
  /** Everyone in the directory — the manager picker's options. */
  directory: PersonRow[];
  /** The live status vocabulary from `/people/facets`. */
  statuses: string[];
  onClose: () => void;
  /** Called after a successful save so the directory behind reloads. */
  onSaved: () => void;
}

const FIELD =
  "w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground outline-none focus:border-primary/50";

export function PersonEditor({
  person,
  hrVisible,
  directory,
  statuses,
  onClose,
  onSaved,
}: Props) {
  const [form, setForm] = useState<PersonForm>(() =>
    person ? formFromPerson(person) : emptyForm(statuses[0] ?? DEFAULT_STATUS),
  );
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [skillInput, setSkillInput] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const set = <K extends keyof PersonForm>(key: K, value: PersonForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // A person cannot be their own manager, and the list is otherwise everyone.
  const managers = directory.filter((p) => p.id !== person?.id);

  const save = async () => {
    const complaint = validate(form);
    if (complaint) {
      setError(complaint);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body = buildWriteBody(form, { hrVisible });
      if (person) {
        await peopleWriteApi.update(person.id, body);
      } else {
        await peopleWriteApi.create(body);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError((err as Error).message || "Couldn't save.");
      setSaving(false);
    }
  };

  const onResume = async (file: File | undefined) => {
    if (!file || !person) return;
    setUploading(true);
    setNotice(null);
    setError(null);
    try {
      const { added_skills: added } = await peopleWriteApi.uploadResume(
        person.id,
        file,
      );
      // The merge already happened server-side; reflecting it in the open form
      // stops the next Save from writing the pre-upload skill list back over it.
      setForm((prev) => ({
        ...prev,
        skills: mergeResumeSkills(prev.skills, added),
      }));
      setNotice(resumeNotice(added));
    } catch (err) {
      setError((err as Error).message || "Couldn't process that résumé.");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={person ? `Edit ${person.name}` : "Add person"}
        className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-foreground">
            {person ? `Edit ${person.name}` : "Add person"}
          </h2>
          <Button
            variant="ghost"
            size="icon-xs"
            icon="X"
            aria-label="Close editor"
            onClick={onClose}
          />
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name *">
              <Input
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="Full name"
              />
            </Field>
            <Field label="Email">
              <Input
                value={form.email}
                onChange={(e) => set("email", e.target.value)}
                placeholder="name@company.com"
              />
            </Field>
            <Field label="Title">
              <Input
                value={form.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="e.g. Senior Engineer"
              />
            </Field>
            <Field label="Role">
              <Input
                value={form.role}
                onChange={(e) => set("role", e.target.value)}
                placeholder="e.g. Software Engineer"
              />
            </Field>
            <Field label="Department">
              <Input
                value={form.department}
                onChange={(e) => set("department", e.target.value)}
                placeholder="e.g. Engineering"
              />
            </Field>
            <Field label="Team">
              <Input
                value={form.team}
                onChange={(e) => set("team", e.target.value)}
                placeholder="e.g. Platform"
              />
            </Field>
            <Field label="Manager">
              <select
                value={form.manager_id}
                aria-label="Manager"
                onChange={(e) => set("manager_id", e.target.value)}
                className={FIELD}
              >
                <option value="">— none —</option>
                {managers.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Status">
              <select
                value={form.status}
                aria-label="Status"
                onChange={(e) => set("status", e.target.value)}
                className={FIELD}
              >
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
            {hrVisible ? (
              <>
                <Field label="Capacity (h/wk)">
                  <Input
                    value={form.capacity_hours_per_week}
                    onChange={(e) =>
                      set("capacity_hours_per_week", e.target.value)
                    }
                    type="number"
                    min={0}
                    placeholder="e.g. 40"
                  />
                </Field>
                <Field label="Stated load (h/wk)">
                  <Input
                    value={form.current_load_hours_per_week}
                    onChange={(e) =>
                      set("current_load_hours_per_week", e.target.value)
                    }
                    type="number"
                    min={0}
                    placeholder="e.g. 20"
                  />
                </Field>
              </>
            ) : null}
            {/* ⚠️ The "ClickUp user id" field was removed 2026-08-24 (D52,
                board WS-39 S1). `gtd_people.clickup_user_id` still exists —
                D52.3 keeps the column under R6 — but it was the assignment
                target for a provider that is gone, so offering the input
                asked a customer to fill in a value nothing would read. */}
          </div>

          <Field label="Skills">
            {!hrVisible ? (
              <p className="text-[11px] text-muted-foreground">
                Restricted — skills and capacity need{" "}
                <code>admin:members:read</code>. They are left untouched by this
                save rather than blanked.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5 rounded-lg border border-border bg-background p-2">
                {form.skills.map((skill) => (
                  <span
                    key={skill}
                    className="flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] text-foreground"
                  >
                    {skill}
                    <Button
                      variant="text"
                      size="none"
                      aria-label={`Remove ${skill}`}
                      onClick={() => set("skills", removeSkill(form.skills, skill))}
                    >
                      <Icon name="X" size={10} />
                    </Button>
                  </span>
                ))}
                <input
                  value={skillInput}
                  aria-label="Add a skill"
                  onChange={(e) => setSkillInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === ",") {
                      e.preventDefault();
                      set("skills", addSkill(form.skills, skillInput));
                      setSkillInput("");
                    }
                  }}
                  placeholder={form.skills.length ? "add…" : "add a skill…"}
                  className="min-w-[6rem] flex-1 bg-transparent text-[11px] text-foreground outline-none placeholder:text-muted-foreground"
                />
              </div>
            )}
          </Field>

          <Field label="Résumé">
            {!person ? (
              <p className="text-[11px] text-muted-foreground">
                Save this person first, then upload a résumé to auto-extract
                skills.
              </p>
            ) : (
              <div className="space-y-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".pdf,.docx,.txt,.md"
                  aria-label="Résumé file"
                  onChange={(e) => onResume(e.target.files?.[0])}
                  className="hidden"
                />
                <Button
                  variant="secondary"
                  icon="Upload"
                  loading={uploading}
                  onClick={() => fileRef.current?.click()}
                >
                  {uploading
                    ? "Parsing résumé…"
                    : "Upload résumé (PDF/DOCX) → auto-update skills"}
                </Button>
                {notice ? (
                  <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
                    <Icon name="Check" size={12} className="mt-0.5 shrink-0" />
                    {notice}
                  </p>
                ) : null}
              </div>
            )}
          </Field>

          {error ? (
            <p role="alert" className="text-[11px] text-destructive">
              {error}
            </p>
          ) : null}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={saving} onClick={save}>
            {person ? "Save changes" : "Add person"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  full,
}: {
  label: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`flex flex-col gap-1 ${full ? "col-span-2" : ""}`}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}
