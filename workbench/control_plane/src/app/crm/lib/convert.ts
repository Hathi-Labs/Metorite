// ── Lead → deal conversion, the caller's half ─────────────────────────────
//
// Conversion is the one place this app decides that two records are the same
// person or the same company (specs/crm_app.md §3.7). The server has the
// rules — caller's choice → email match → create for the contact, caller's
// choice → exact name match → create for the organization. The modal's job is
// to let a human make that choice BEFORE the server guesses, which is what
// `contact_id` / `company_id` in the request body are for.
//
// This module is the modal's brain: what the server would do if nobody chose,
// and what to send once somebody has.

import type { Contact, Lead, Organization } from "./types";

/** What the modal offers for one side of the conversion. */
export type MatchChoice =
  /** Use this existing record. Sends its id, so the server does not re-guess. */
  | { kind: "existing"; id: string }
  /** Create a fresh one from the lead's own fields. */
  | { kind: "new" }
  /** Only for the organization: an individual buyer has no company. */
  | { kind: "none" };

export type ConvertChoice = {
  contact: MatchChoice;
  organization: MatchChoice;
  deal: { name: string; amount: string; expected_close_date: string };
};

/**
 * The contact the server WOULD match, so the modal can pre-select it and say
 * why. Email is the one dedup rule — an address is the same person in any
 * casing, which is why both sides are folded.
 */
export function matchContact(
  lead: Pick<Lead, "email">,
  contacts: Contact[]
): Contact | null {
  const email = (lead.email || "").trim().toLowerCase();
  if (!email) return null;
  return (
    contacts.find((c) => (c.email || "").trim().toLowerCase() === email) ?? null
  );
}

/**
 * The organization the server would match: EXACT name, deliberately not fuzzy.
 * A near-match that silently attaches a deal to the wrong account is worse
 * than a duplicate the owner can merge, and the modal is where a human
 * resolves the near-misses anyway.
 */
export function matchOrganization(
  lead: Pick<Lead, "organization_name">,
  organizations: Organization[]
): Organization | null {
  const name = (lead.organization_name || "").trim();
  if (!name) return null;
  return organizations.find((o) => o.name.trim() === name) ?? null;
}

/**
 * Near-misses to show under the exact match — case- and whitespace-folded, so
 * "bosch india" surfaces next to "Bosch India" without the server ever
 * treating them as the same row.
 *
 * ⚠️ The exact match is excluded **by identity**, not by comparing strings.
 * The first version compared a lowercased candidate against a non-lowercased
 * lead name, so the exact match never matched its own exclusion and the modal
 * rendered that account twice — both entries carrying the same id, so both
 * drew as selected. Excluding by id also keeps a case-variant genuinely
 * visible: `matchOrganization` is case-SENSITIVE, so "bosch india" is NOT the
 * exact match and is exactly the kind of near-miss this list exists to
 * surface. Folding both sides instead would have hidden it entirely.
 */
export function similarOrganizations(
  lead: Pick<Lead, "organization_name">,
  organizations: Organization[],
  limit = 5
): Organization[] {
  const name = (lead.organization_name || "").trim().toLowerCase();
  if (!name) return [];
  const exact = matchOrganization(lead, organizations);
  return organizations
    .filter((o) => {
      if (o.id === exact?.id) return false;
      const candidate = o.name.trim().toLowerCase();
      return candidate.includes(name) || name.includes(candidate);
    })
    .slice(0, limit);
}

/** The modal's opening state: whatever the server would have done, shown. */
export function initialChoice(
  lead: Lead,
  contacts: Contact[],
  organizations: Organization[]
): ConvertChoice {
  const contact = matchContact(lead, contacts);
  const organization = matchOrganization(lead, organizations);
  return {
    contact: contact ? { kind: "existing", id: contact.id } : { kind: "new" },
    organization: !lead.organization_name?.trim()
      ? { kind: "none" }
      : organization
        ? { kind: "existing", id: organization.id }
        : { kind: "new" },
    deal: {
      // §3.7 step 3: the deal is named for the company, falling back to the
      // lead's own display name.
      name: (lead.organization_name || "").trim() || lead.lead_name,
      amount: "",
      expected_close_date: "",
    },
  };
}

export type ConvertBody = {
  contact_id?: string;
  company_id?: string;
  deal?: {
    name?: string;
    amount?: number;
    expected_close_date?: string;
  };
};

/**
 * The choice as `POST /crm/leads/{id}/convert`'s body.
 *
 * An id is sent ONLY for "use this one". "Create new" omits the field, which
 * is how §3.7 spells it: absent means "work it out", and re-sending the id
 * the modal matched would look identical while meaning something different if
 * the server's rule ever changes.
 */
export function convertBody(choice: ConvertChoice): ConvertBody {
  const body: ConvertBody = {};
  if (choice.contact.kind === "existing") body.contact_id = choice.contact.id;
  if (choice.organization.kind === "existing") {
    body.company_id = choice.organization.id;
  }
  const deal: NonNullable<ConvertBody["deal"]> = {};
  if (choice.deal.name.trim()) deal.name = choice.deal.name.trim();
  const amount = Number.parseFloat(choice.deal.amount);
  if (choice.deal.amount.trim() && Number.isFinite(amount)) deal.amount = amount;
  if (choice.deal.expected_close_date) {
    deal.expected_close_date = choice.deal.expected_close_date;
  }
  if (Object.keys(deal).length) body.deal = deal;
  return body;
}

/** Whether the modal can submit. The deal needs a name; nothing else does. */
export function canConvert(choice: ConvertChoice): boolean {
  return choice.deal.name.trim().length > 0;
}

/** A lead is convertible while the deal it became still exists — the FK, not
 *  the timestamp (B6). Deleting that deal returns the lead to the queue. */
export function isConverted(lead: Pick<Lead, "converted_deal_id">): boolean {
  return Boolean(lead.converted_deal_id);
}
