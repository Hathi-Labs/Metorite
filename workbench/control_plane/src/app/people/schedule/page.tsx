"use client";

/**
 * People Center · the company's working week (WS-28p).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.11.
 *
 * Readable by any `feature:people` holder — a person cannot understand their
 * own effective schedule without seeing the layer underneath it — and editable
 * only with `admin:members:manage`, because this is the definition of the
 * working week for everybody and it moves every capacity figure in the product
 * at once.
 *
 * **It shows what it will move before it moves it.** Every edit runs a dry run
 * first and renders the impact; the save button is what applies it. A settings
 * page that silently re-baselines every load bar in the org is a settings page
 * nobody trusts twice.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import { PeopleApiError, peopleApi } from "../lib/api";
import {
  DAY_NAMES,
  type PolicyImpact,
  type WorkPolicy,
  describeImpact,
  formatDays,
  formatHours,
  policyChanged,
} from "../lib/schedule";

export default function WorkSchedulePage() {
  const [saved, setSaved] = useState<WorkPolicy | null>(null);
  const [draft, setDraft] = useState<WorkPolicy | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [hours, setHours] = useState<number | null>(null);
  const [impact, setImpact] = useState<PolicyImpact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [updatedBy, setUpdatedBy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await peopleApi.schedule();
      setSaved(res.policy);
      setDraft(res.policy);
      setCanManage(res.can_manage);
      setHours(res.contracted_hours_per_week);
      setUpdatedBy(res.updated_by ?? null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = draft && saved ? policyChanged(draft, saved) : false;

  /**
   * The dry run. Runs on demand rather than on every keystroke: it walks the
   * whole roster, and a preview that fires per character would be a load test
   * somebody typed by accident.
   */
  async function preview() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const res = await peopleApi.saveSchedule(draft, true);
      setImpact(res.impact);
    } catch (err) {
      setError(
        err instanceof PeopleApiError ? err.message : String((err as Error).message)
      );
      setImpact(null);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await peopleApi.saveSchedule(draft, false);
      setSaved(res.policy);
      setDraft(res.policy);
      setImpact(res.impact);
      setNotice(
        res.impact.changed === 0
          ? "Saved. Nobody's contracted hours moved."
          : `Saved. ${res.impact.changed} people's contracted hours changed.`
      );
      await load();
    } catch (err) {
      // The gateway refuses a bad policy with the sentence, not a field code —
      // shown verbatim because the person typing it is the person who fixes it.
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function set<K extends keyof WorkPolicy>(key: K, value: WorkPolicy[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setImpact(null);
  }

  function toggleDay(iso: number) {
    if (!draft) return;
    const days = draft.working_days.includes(iso)
      ? draft.working_days.filter((d) => d !== iso)
      : [...draft.working_days, iso].sort((a, b) => a - b);
    set("working_days", days);
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-sm font-medium text-foreground">
            The company&apos;s working week
          </h1>
          <p className="text-[11px] text-muted-foreground">
            The default everybody&apos;s contracted hours are derived from. A
            person can override any of it on their own profile.
          </p>
        </div>
      </header>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {draft && (
        <>
          <section className="rounded-xl border border-border p-3">
            <h2 className="text-xs font-medium text-foreground">Working days</h2>
            <div className="mt-2 flex flex-wrap gap-1">
              {DAY_NAMES.map((name, index) => {
                const iso = index + 1;
                const on = draft.working_days.includes(iso);
                return (
                  <Button
                    key={name}
                    size="sm"
                    variant={on ? "primary" : "secondary"}
                    disabled={!canManage}
                    onClick={() => toggleDay(iso)}
                  >
                    {name}
                  </Button>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">
              {formatDays(draft.working_days)} ·{" "}
              {formatHours(
                draft.working_days.length * (draft.hours_per_day || 0)
              )}{" "}
              a week on the company default
              {hours !== null && !dirty ? ` (currently ${formatHours(hours)})` : ""}
            </p>
          </section>

          <section className="grid gap-3 rounded-xl border border-border p-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted-foreground">
                Hours per day
              </span>
              <Input
                inputSize="sm"
                type="number"
                step="0.5"
                value={String(draft.hours_per_day ?? "")}
                readOnly={!canManage}
                onChange={(e) =>
                  set("hours_per_day", Number(e.target.value) || 0)
                }
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted-foreground">Day starts</span>
              <Input
                inputSize="sm"
                type="time"
                value={draft.start ?? ""}
                readOnly={!canManage}
                onChange={(e) => set("start", e.target.value || null)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] text-muted-foreground">Day ends</span>
              <Input
                inputSize="sm"
                type="time"
                value={draft.end ?? ""}
                readOnly={!canManage}
                onChange={(e) => set("end", e.target.value || null)}
              />
            </label>
            <p className="text-[11px] text-muted-foreground sm:col-span-3">
              Leave the times blank for an organisation with no fixed clock —
              hours still count, and the calendar keeps its own defaults.
            </p>
          </section>

          <section className="rounded-xl border border-border p-3">
            <h2 className="text-xs font-medium text-foreground">Shifts</h2>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Named alternatives to the standard day. A person picks one on their
              profile; changing a shift&apos;s hours moves everybody on it.
            </p>
            {(draft.shifts ?? []).length === 0 ? (
              <p className="mt-2 text-[11px] text-muted-foreground">
                None — everybody works the standard day.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1">
                {(draft.shifts ?? []).map((shift) => (
                  <li key={shift.name} className="text-xs text-foreground">
                    <span className="font-medium">{shift.name}</span>
                    <span className="text-muted-foreground">
                      {" — "}
                      {shift.start ?? "—"}–{shift.end ?? "—"}
                      {shift.days ? ` · ${formatDays(shift.days)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {impact && (
            <section className="rounded-xl border border-border p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-foreground">
                <Icon name="AlertTriangle" className="h-3.5 w-3.5" />
                What this change moves
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {describeImpact(impact)}
              </p>
              {impact.examples.length > 0 && (
                <ul className="mt-2 flex flex-col gap-0.5">
                  {impact.examples.map((row) => (
                    <li key={row.id} className="text-[11px] text-muted-foreground">
                      <span className="text-foreground">{row.name}</span>{" "}
                      {formatHours(row.before)} → {formatHours(row.after)}
                    </li>
                  ))}
                  {impact.changed > impact.examples.length && (
                    <li className="text-[11px] text-muted-foreground">
                      …and {impact.changed - impact.examples.length} more
                    </li>
                  )}
                </ul>
              )}
            </section>
          )}

          {notice && !error && (
            <p className="text-xs text-muted-foreground">{notice}</p>
          )}

          {/*
            Absent, not disabled, for somebody who may never edit this — the
            same rule the person editor follows. The day buttons above stay
            visible because reading the company's week is the point of the page.
          */}
          {canManage && (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={preview}
                disabled={!dirty}
                loading={busy && !notice}
              >
                Preview the impact
              </Button>
              <Button size="sm" onClick={save} disabled={!dirty} loading={busy}>
                Save
              </Button>
              {updatedBy && (
                <span className="text-[11px] text-muted-foreground">
                  Last changed by {updatedBy}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </main>
  );
}
