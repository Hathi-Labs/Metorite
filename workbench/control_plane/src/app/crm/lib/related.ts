// ── The related lists of a record page (WS-53 CRM-Q3) ─────────────────────
//
// Spec crm_platform.md section 18.4: a company shows its contacts and deals, a
// contact shows its deals, and a deal shows its contacts with their roles. The
// deal's contacts come from `deal_contacts` and stay in `FieldsPanel`, with the
// primary star and the detach verb.
//
// Pure, so the sections and their row words have tests without a browser.

import { money } from "./format";
import type { EntitySlug } from "./types";

type Row = Record<string, unknown>;

/** The rows of the open record's related lists. */
export type RelatedLists = {
  /** Contacts of an open company. */
  contacts: Row[];
  /** Deals of an open company, or of an open contact. */
  deals: Row[];
  /** The whole count behind each page. Zero until a read answers. */
  contactsTotal: number;
  dealsTotal: number;
  /** The read failed. The section says so and offers a retry. */
  failed: boolean;
};

/** Rows asked for in each related list. */
export const RELATED_PAGE = 50;

export const NO_RELATED: RelatedLists = {
  contacts: [],
  deals: [],
  contactsTotal: 0,
  dealsTotal: 0,
  failed: false,
};

export type RelatedSection = {
  title: string;
  /** The collection a row opens in. */
  entity: EntitySlug;
  rows: Row[];
  /** The whole count, which can pass the rows shown. */
  total: number;
  empty: string;
  name: (row: Row) => string;
  detail: (row: Row) => string;
};

const text = (value: unknown): string =>
  typeof value === "string" ? value.trim() : "";

function contactName(row: Row): string {
  return [text(row.first_name), text(row.last_name)].filter(Boolean).join(" ") || "Unnamed contact";
}

function contactDetail(row: Row): string {
  return text(row.title) || text(row.email) || "";
}

function dealName(row: Row): string {
  return text(row.name) || "Untitled deal";
}

function dealDetail(row: Row): string {
  const amount = typeof row.amount === "number" ? money(row.amount, text(row.currency) || "INR") : "";
  return [text(row.organization_name), amount].filter(Boolean).join(" · ");
}

/**
 * The sections a record page draws under its fields.
 *
 * A deal has none here: its contacts are the People section of `FieldsPanel`.
 * A lead has none: it has no company link until it converts.
 */
export function relatedSections(entity: EntitySlug, related: RelatedLists): RelatedSection[] {
  if (entity === "organizations") {
    return [
      {
        title: "Contacts",
        entity: "contacts",
        rows: related.contacts,
        total: related.contactsTotal,
        empty: "No contacts at this company.",
        name: contactName,
        detail: contactDetail,
      },
      {
        title: "Deals",
        entity: "deals",
        rows: related.deals,
        total: related.dealsTotal,
        empty: "No deals with this company.",
        name: dealName,
        detail: dealDetail,
      },
    ];
  }
  if (entity === "contacts") {
    return [
      {
        title: "Deals",
        entity: "deals",
        rows: related.deals,
        total: related.dealsTotal,
        empty: "This contact is on no deal.",
        name: dealName,
        detail: dealDetail,
      },
    ];
  }
  return [];
}

/** "Showing 50 of 73" when the page is short of the whole, else null. */
export function showingLine(section: Pick<RelatedSection, "rows" | "total">): string | null {
  return section.total > section.rows.length
    ? `Showing ${section.rows.length} of ${section.total}`
    : null;
}
