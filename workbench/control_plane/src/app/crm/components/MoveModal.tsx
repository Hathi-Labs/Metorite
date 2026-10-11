"use client";

/**
 * The move prompt — everything the gateway will demand before it lets a deal
 * into a stage, asked once (specs/crm_app.md §5.1 system 3, WS-26h).
 *
 * Two gates guard the same PATCH and both answer 422 BEFORE any of the
 * transition's three effects: a `lost`-type stage needs a `lost_reason_id`
 * (WS-26a), and any stage may demand entry requirements (WS-26h). This is one
 * modal rather than two because a lost stage can also be a gated one, and two
 * sequential dialogs — each raised by its own refusal — is the shape where a
 * user answers a question, is refused again, and reads it as the app being
 * broken.
 *
 * Everything it collects rides in the SAME request as the status change.
 * Filling the fields first and moving second would be two requests, and the
 * half that lands is the one that moved the deal.
 *
 * ⚠️ It supersedes `LostReasonModal`, which it absorbed rather than sat beside:
 * a second surface that also knows how to ask for a lost reason is the second
 * copy that stops agreeing with the first.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useState } from "react";
import {
  REQUIREABLE_FIELD_LABELS,
  type RequireableField,
} from "../lib/board";
import type { LostReason, Organization } from "../lib/types";

export type MovePayload = Record<string, unknown>;

export default function MoveModal({
  statusName,
  needsReason,
  missing,
  reasons,
  organizations,
  saving,
  onCancel,
  onConfirm,
}: {
  statusName: string;
  /** The target is a lost-type stage and the deal carries no reason yet. */
  needsReason: boolean;
  /** Entry requirements the deal does not satisfy, in the stage's own order. */
  missing: RequireableField[];
  reasons: LostReason[];
  /** Loaded on open — the picker for an `company_id` requirement. */
  organizations: Organization[];
  saving: boolean;
  onCancel: () => void;
  onConfirm: (payload: MovePayload) => void;
}) {
  const [reasonId, setReasonId] = useState<string>(reasons[0]?.id ?? "");
  const [note, setNote] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});

  const value = (field: RequireableField): string => fields[field] ?? "";
  const set = (field: RequireableField, next: string) =>
    setFields((f) => ({ ...f, [field]: next }));

  // Every requirement answered, and a reason chosen if one is owed. Judged on
  // what will be SENT rather than on what has been typed anywhere: a blank box
  // is what the server would refuse, so the button that would earn that
  // refusal is the one that stays disabled.
  const complete =
    missing.every((field) => value(field).trim().length > 0) &&
    (!needsReason || Boolean(reasonId));

  function confirm(): void {
    const payload: MovePayload = {};
    if (needsReason) {
      payload.lost_reason_id = reasonId;
      if (note.trim()) payload.lost_note = note.trim();
    }
    for (const field of missing) {
      const raw = value(field).trim();
      // `amount` is the one numeric requirement; sending "400000" as text
      // would reach a NUMERIC column as a string the driver has to guess at.
      payload[field] = field === "amount" ? Number(raw) : raw;
    }
    onConfirm(payload);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        aria-label="Cancel"
        onClick={onCancel}
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
      />
      <div className="relative flex max-h-[85vh] w-full max-w-sm flex-col rounded-xl border border-border bg-card shadow-xl">
        <header className="flex shrink-0 items-start justify-between gap-2 border-b border-border px-4 py-3">
          <div>
            <h2 className="text-base font-bold text-foreground">
              Moving to {statusName}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {needsReason && missing.length > 0
                ? "This stage needs a reason and a few details before a deal can enter it."
                : needsReason
                  ? "A lost deal needs a reason — it is what the funnel report is for."
                  : `${statusName} does not accept a deal without these.`}
            </p>
          </div>
          <Button
            size="icon-sm"
            variant="ghost"
            icon="X"
            aria-label="Cancel"
            onClick={onCancel}
          />
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {needsReason && (
            <section className="space-y-2">
              {reasons.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No lost reasons are configured. Add one in Pipeline settings
                  before closing deals as lost — the gateway will refuse the
                  move without it.
                </p>
              ) : (
                <div className="space-y-1">
                  {[...reasons]
                    .sort((a, b) => a.position - b.position)
                    .map((reason) => (
                      <button
                        key={reason.id}
                        onClick={() => setReasonId(reason.id)}
                        className={`w-full rounded-lg border px-3 py-2 text-left text-xs tech-transition ${
                          reasonId === reason.id
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
                        }`}
                      >
                        {reason.label}
                      </button>
                    ))}
                </div>
              )}
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                placeholder="Anything worth remembering (optional)"
                className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary/40 focus:outline-none"
              />
            </section>
          )}

          {missing.map((field) => (
            <section key={field} className="space-y-1.5">
              <label className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {REQUIREABLE_FIELD_LABELS[field]}
              </label>
              {field === "company_id" ? (
                <OrganizationPicker
                  organizations={organizations}
                  selected={value(field)}
                  onSelect={(id) => set(field, id)}
                />
              ) : (
                <Input
                  inputSize="sm"
                  type={
                    field === "expected_close_date"
                      ? "date"
                      : field === "amount"
                        ? "number"
                        : "email"
                  }
                  inputMode={field === "amount" ? "decimal" : undefined}
                  value={value(field)}
                  onChange={(e) => set(field, e.target.value)}
                  placeholder={
                    field === "amount"
                      ? "Amount (₹)"
                      : field === "owner_email"
                        ? "owner@example.com"
                        : undefined
                  }
                  className="w-full"
                />
              )}
            </section>
          ))}
        </div>

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="secondary" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            loading={saving}
            disabled={saving || !complete}
            onClick={confirm}
          >
            {needsReason ? "Mark lost" : "Move"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

/**
 * A short, searchable account list.
 *
 * A picker and not a free-text box because `company_id` is a foreign key:
 * a typed name reaches the gateway as an invalid UUID and comes back as a 422
 * about a cast, which is not an answer to "which company is this".
 */
function OrganizationPicker({
  organizations,
  selected,
  onSelect,
}: {
  organizations: Organization[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const shown = organizations
    .filter((org) => !needle || org.name.toLowerCase().includes(needle))
    .slice(0, 8);

  return (
    <div className="space-y-1.5">
      <Input
        inputSize="sm"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search accounts"
        className="w-full"
      />
      {organizations.length === 0 ? (
        <p className="text-[10px] text-muted-foreground">
          No companies loaded yet.
        </p>
      ) : (
        <div className="space-y-1">
          {shown.map((org) => (
            <button
              key={org.id}
              onClick={() => onSelect(org.id)}
              className={`flex w-full items-center gap-1.5 rounded-lg border px-3 py-2 text-left text-xs tech-transition ${
                selected === org.id
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
              }`}
            >
              <Icon name="Building2" className="w-3 h-3 shrink-0" />
              <span className="truncate">{org.name}</span>
            </button>
          ))}
          {shown.length === 0 && (
            <p className="text-[10px] text-muted-foreground">
              Nothing matches “{query}”.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
