"use client";

/**
 * People Center · when somebody is away (WS-28k).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.8 · **D-PC-7**.
 *
 * **Availability, not leave management.** There is no approve button here and
 * there is not meant to be one: a half-built approval chain looks like a
 * control, so people stop checking with each other, and then it turns out
 * nothing was ever enforced. What this records is a fact — who is away, when,
 * and how much — which is the half assignment actually needs.
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";

import { type Absence, PeopleApiError, peopleApi } from "../lib/api";
import { ABSENCE_KINDS, describeAbsence, sortAbsences } from "../lib/absence";

interface Props {
  /** `"me"` or a person id — the self door is ungated (D-PC-15). */
  target: string;
  absences: Absence[];
  /** Hours left this week after absences — HR tier, so it can be absent. */
  hoursThisWeek?: number | null;
  /** Absent when this caller may not record one; the form is then not drawn. */
  canEdit: boolean;
  onChanged: () => void;
}

export function AbsencePanel({
  target,
  absences,
  hoursThisWeek,
  canEdit,
  onChanged,
}: Props) {
  const today = new Date().toISOString().slice(0, 10);
  const [starts, setStarts] = useState(today);
  const [ends, setEnds] = useState(today);
  const [kind, setKind] = useState("away");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await peopleApi.addAbsence(target, {
        starts_on: starts,
        ends_on: ends,
        kind,
        note: note.trim() || null,
      });
      setNote("");
      onChanged();
    } catch (err) {
      // The gateway refuses with a sentence — "an absence cannot end before it
      // starts" — which is the only version anybody can act on.
      setError(
        err instanceof PeopleApiError ? err.message : String((err as Error).message)
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    try {
      await peopleApi.removeAbsence(target, id);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-xs font-medium text-foreground">Time away</h3>
        {hoursThisWeek !== null && hoursThisWeek !== undefined && (
          <span className="text-[11px] text-muted-foreground">
            {hoursThisWeek}h left this week
          </span>
        )}
      </div>
      {/*
        ⚠️ Second person ONLY on the self door. `target` already carries the
        answer — the literal `"me"` for your own row, a person id for somebody
        else's — so the voice needs no new prop. Before this, a colleague's
        page told the reader the suggester would not hand THEM work while THEY
        were away, about somebody else entirely.
      */}
      <p className="mt-0.5 text-[11px] text-muted-foreground">
        {target === "me"
          ? "So the assignment suggester does not hand you work while you are away."
          : "So the assignment suggester does not hand them work while they are away."}{" "}
        No approval — this is a note, not a request.
      </p>

      {absences.length === 0 ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Nothing recorded.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {sortAbsences(absences).map((absence) => (
            <li
              key={absence.id}
              className="flex items-center justify-between gap-2 text-xs"
            >
              <span className="min-w-0 text-foreground">
                {describeAbsence(absence)}
              </span>
              {canEdit && (
                <Button
                  size="icon-xs"
                  variant="ghost"
                  icon="X"
                  aria-label={`Remove ${describeAbsence(absence)}`}
                  onClick={() => void remove(absence.id)}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p className="mt-2 text-[11px] text-destructive" role="alert">
          {error}
        </p>
      )}

      {/* Absent, not disabled, for a caller who may not record one. */}
      {canEdit && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-muted-foreground">From</span>
            <Input
              inputSize="sm"
              type="date"
              value={starts}
              onChange={(e) => {
                setStarts(e.target.value);
                // A single day is the common case, and an end before the start
                // is refused by the server — so the field follows rather than
                // waiting to be corrected.
                if (e.target.value > ends) setEnds(e.target.value);
              }}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-muted-foreground">To</span>
            <Input
              inputSize="sm"
              type="date"
              value={ends}
              min={starts}
              onChange={(e) => setEnds(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-muted-foreground">Kind</span>
            <Select
              inputSize="sm"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {ABSENCE_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-[10px] text-muted-foreground">Note</span>
            <Input
              inputSize="sm"
              value={note}
              placeholder="optional"
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <Button size="sm" icon="Plus" loading={busy} onClick={add}>
            Add
          </Button>
        </div>
      )}
    </section>
  );
}

/** The one-line "away until" badge, for a directory row or a person header. */
export function AwayBadge({
  away,
}: {
  away?: { kind: string; until: string } | null;
}) {
  if (!away) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
      <Icon name="Plane" className="h-3 w-3" />
      {away.kind === "partial" ? "part-time until" : "away until"}{" "}
      {away.until}
    </span>
  );
}

export default AbsencePanel;
