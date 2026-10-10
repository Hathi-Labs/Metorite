// ── The list's column vocabulary ──────────────────────────────────────────
//
// One declaration of "what columns does a leads list have, in what order,
// under what heading". It lives here rather than inside RecordList.tsx
// because it now has TWO consumers that must not disagree:
//
//   * `../components/RecordList.tsx` draws it on screen;
//   * `routes/crm/export.py::COLUMNS` writes it into the CSV.
//
// A spreadsheet whose columns are shuffled — or renamed, or one short —
// relative to the screen is a second opinion about the same rows, and nobody
// re-derives a column set from a CSV to check. So the Python side is held
// against THIS file by `tests/unit/test_crm_export.py`, which parses it rather
// than mirroring it — the same mechanism `test_crm_stage_discipline_parity.py`
// uses for `board.ts::REQUIREABLE_FIELDS`, and for the same reason: a mirror
// goes stale and then lies.
//
// ⚠️ **Keep it declarative.** The fence reads `key`, `label` and `exportLabel`
// out of the source text. Cell RENDERING deliberately stays in RecordList.tsx:
// it is JSX and per-theme, and a `.ts` file cannot hold JSX anyway.

import type { EntitySlug } from "./types";

export type CrmColumn = {
  /** The row field this column reads. Also the CSV's column key. */
  key: string;
  /**
   * The table's `<th>` text. May be empty for a column the table draws bare —
   * the leads list's converted badge needs no heading on screen.
   */
  label: string;
  /**
   * The CSV header, when `label` will not do.
   *
   * A blank header is a column nobody can name in a formula or a pivot, so a
   * column with a blank `label` must declare one of these. Read by the Python
   * fence, never by the table.
   */
  exportLabel?: string;
  /** The sort key this column offers, when the gateway allowlists one. */
  sort?: string;
  className?: string;
};

export const COLUMNS: Record<EntitySlug, CrmColumn[]> = {
  deals: [
    {
      key: "name",
      label: "Deal",
      sort: "name",
      className: "font-medium text-foreground",
    },
    { key: "organization_name", label: "Company" },
    { key: "amount", label: "Amount", sort: "amount" },
    {
      key: "expected_close_date",
      label: "Closing",
      sort: "expected_close_date",
    },
    { key: "owner_email", label: "Owner" },
  ],
  leads: [
    {
      key: "lead_name",
      label: "Lead",
      sort: "lead_name",
      className: "font-medium text-foreground",
    },
    { key: "organization_name", label: "Company" },
    { key: "email", label: "Email", sort: "email" },
    { key: "owner_email", label: "Owner", sort: "owner_email" },
    {
      // "Converted" is the FK, never the timestamp (B6): deleting the deal
      // SET-NULLs the link and the lead comes back to the working list. The
      // table draws it as a badge with no heading; the file needs a name.
      key: "converted_deal_id",
      label: "",
      exportLabel: "Converted",
    },
  ],
  contacts: [
    {
      key: "first_name",
      label: "Name",
      sort: "first_name",
      className: "font-medium text-foreground",
    },
    { key: "title", label: "Title" },
    { key: "email", label: "Email", sort: "email" },
    { key: "phone", label: "Phone" },
    { key: "owner_email", label: "Owner", sort: "owner_email" },
  ],
  organizations: [
    {
      key: "name",
      label: "Company",
      sort: "name",
      className: "font-medium text-foreground",
    },
    { key: "industry", label: "Industry" },
    { key: "phone", label: "Phone" },
    { key: "owner_email", label: "Owner", sort: "owner_email" },
  ],
};
