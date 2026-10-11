"use client";

/**
 * The convert modal (specs/crm_app.md §5 surface 4, §3.7).
 *
 * Conversion is the one place this app decides that two records are the same
 * person or the same company. The server has a rule for each — email for the
 * contact, exact name for the organization — and this modal is where a human
 * confirms or overrides it BEFORE the server guesses, which is what
 * `contact_id` / `company_id` in the request body are for.
 *
 * The pre-selection IS the server's rule, shown (lib/convert.ts), so the
 * modal reads as a confirmation rather than as a second opinion.
 */

import Icon from "@/components/Icon";
import { useState } from "react";
import {
  canConvert,
  convertBody,
  initialChoice,
  matchContact,
  matchOrganization,
  similarOrganizations,
  type ConvertChoice,
  type MatchChoice,
} from "../lib/convert";
import type { Contact, Lead, Organization } from "../lib/types";

export default function ConvertModal({
  lead,
  contacts,
  organizations,
  saving,
  onClose,
  onConvert,
}: {
  lead: Lead;
  contacts: Contact[];
  organizations: Organization[];
  saving: boolean;
  onClose: () => void;
  onConvert: (body: Record<string, unknown>) => void;
}) {
  const [choice, setChoice] = useState<ConvertChoice>(() =>
    initialChoice(lead, contacts, organizations)
  );

  // The directory loads AFTER the modal opens (it is a picker, not a mirror),
  // so the pre-selection has to be re-seeded once the matches are knowable.
  // Adjusted during render rather than in an effect — React's own "resetting
  // state when a prop changes" pattern: an effect here would render the
  // stale pre-selection first and then correct it, which reads as the modal
  // changing its mind.
  const seed = `${lead.id}:${contacts.length}:${organizations.length}`;
  const [seeded, setSeeded] = useState(seed);
  if (seeded !== seed) {
    setSeeded(seed);
    setChoice(initialChoice(lead, contacts, organizations));
  }

  const matchedContact = matchContact(lead, contacts);
  const matchedOrg = matchOrganization(lead, organizations);
  const similar = similarOrganizations(lead, organizations);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
      />
      <div className="relative flex max-h-[85vh] w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-xl">
        <header className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h2 className="text-base font-bold text-foreground">Convert lead</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {lead.lead_name} becomes a deal, with a contact and an
              company behind it.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg border border-border p-2 text-muted-foreground hover:bg-secondary tech-transition"
          >
            <Icon name="X" className="w-4 h-4" />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          <section>
            <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
              Contact
            </h3>
            {matchedContact ? (
              <Option
                selected={choice.contact.kind === "existing"}
                onSelect={() =>
                  setChoice((c) => ({
                    ...c,
                    contact: { kind: "existing", id: matchedContact.id },
                  }))
                }
                title={`${matchedContact.first_name} ${matchedContact.last_name ?? ""}`.trim()}
                note={`Already in the CRM — matched on ${matchedContact.email}`}
              />
            ) : null}
            <Option
              selected={choice.contact.kind === "new"}
              onSelect={() =>
                setChoice((c) => ({ ...c, contact: { kind: "new" } }))
              }
              title="Create a new contact"
              note={
                lead.email
                  ? `From this lead's own details (${lead.email})`
                  : "From this lead's own details"
              }
            />
          </section>

          <section>
            <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
              Company
            </h3>
            {!lead.organization_name?.trim() ? (
              <p className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
                This lead names no company, so no company is created —
                minting one named after a person is how a CRM fills with noise.
              </p>
            ) : (
              <>
                {matchedOrg && (
                  <Option
                    selected={
                      choice.organization.kind === "existing" &&
                      choice.organization.id === matchedOrg.id
                    }
                    onSelect={() =>
                      setChoice((c) => ({
                        ...c,
                        organization: { kind: "existing", id: matchedOrg.id },
                      }))
                    }
                    title={matchedOrg.name}
                    note="Exact name match"
                  />
                )}
                {/* Near-misses are shown but never auto-taken: a fuzzy match
                    that silently attaches a deal to the wrong account is
                    worse than a duplicate somebody can merge. */}
                {similar.map((org) => (
                  <Option
                    key={org.id}
                    selected={
                      choice.organization.kind === "existing" &&
                      choice.organization.id === org.id
                    }
                    onSelect={() =>
                      setChoice((c) => ({
                        ...c,
                        organization: { kind: "existing", id: org.id },
                      }))
                    }
                    title={org.name}
                    note="Similar name — pick it only if it is the same company"
                  />
                ))}
                <Option
                  selected={choice.organization.kind === "new"}
                  onSelect={() =>
                    setChoice((c) => ({ ...c, organization: { kind: "new" } }))
                  }
                  title={`Create “${lead.organization_name}”`}
                  note="A new company from this lead's company fields"
                />
              </>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Deal
            </h3>
            <input
              value={choice.deal.name}
              onChange={(e) =>
                setChoice((c) => ({
                  ...c,
                  deal: { ...c.deal, name: e.target.value },
                }))
              }
              placeholder="Deal name"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground focus:border-primary/40 focus:outline-none"
            />
            <div className="flex gap-2">
              <input
                value={choice.deal.amount}
                onChange={(e) =>
                  setChoice((c) => ({
                    ...c,
                    deal: { ...c.deal, amount: e.target.value },
                  }))
                }
                inputMode="decimal"
                placeholder="Amount (₹)"
                className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground focus:border-primary/40 focus:outline-none"
              />
              <input
                type="date"
                value={choice.deal.expected_close_date}
                onChange={(e) =>
                  setChoice((c) => ({
                    ...c,
                    deal: { ...c.deal, expected_close_date: e.target.value },
                  }))
                }
                className="rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground focus:border-primary/40 focus:outline-none"
              />
            </div>
            <p className="text-[10px] text-muted-foreground">
              The deal lands in the default stage and carries this lead as its
              provenance, so its timeline keeps everything said before now.
            </p>
          </section>
        </div>

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:border-primary/30 tech-transition"
          >
            Cancel
          </button>
          <button
            onClick={() => onConvert(convertBody(choice) as Record<string, unknown>)}
            disabled={saving || !canConvert(choice)}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 tech-transition"
          >
            Convert
          </button>
        </footer>
      </div>
    </div>
  );
}

function Option({
  selected,
  onSelect,
  title,
  note,
}: {
  selected: boolean;
  onSelect: () => void;
  title: string;
  note: string;
}) {
  return (
    <button
      onClick={onSelect}
      className={`mb-1.5 w-full rounded-lg border p-2.5 text-left tech-transition ${
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/20"
          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
      }`}
    >
      <span className="block truncate text-xs font-medium text-foreground">
        {title}
      </span>
      <span className="block truncate text-[10px] text-muted-foreground">
        {note}
      </span>
    </button>
  );
}

export type { MatchChoice };
