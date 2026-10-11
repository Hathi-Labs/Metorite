// ── Who may open the CRM (WS-53 CRM-L2, spec §10 item 6, CRM-T5) ────────────
//
// Pure. The gateway rule: no `crm_settings` row reads as `admins`, so a member
// gets 403 on every `/crm` route until an admin chooses. The page reads that
// 403 as a state, not as a broken screen.

import { CrmError } from "./api";

export type CrmAccess = "admins" | "everyone" | "groups";

/** The choices of the access control, in the order the admin reads them. */
export const ACCESS_OPTIONS: ReadonlyArray<{ value: CrmAccess; label: string; description: string }> = [
  { value: "admins", label: "Admins only", description: "Only admins can open the CRM." },
  { value: "everyone", label: "Everyone", description: "Each member with the CRM app can open it." },
  { value: "groups", label: "Chosen groups", description: "Members of the groups you pick can open it." },
];

/** What a member sees when the gateway refuses the first read. */
export const NO_ACCESS_TITLE = "The CRM is not open to you yet";
export const NO_ACCESS_BODY = "An admin chooses who can use the CRM in CRM settings.";

/** What an admin sees while the rule is still the default. */
export const ADMINS_ONLY_HINT = "Only admins can open the CRM now.";
export const ADMINS_ONLY_LINK = "Choose who else can use it.";

/** The id of the access control, so the hint can link to it. */
export const ACCESS_ANCHOR = "crm-access";

/** Is this error the access refusal (403) of the gateway? */
export function isAccessDenied(err: unknown): boolean {
  return err instanceof CrmError && err.status === 403;
}

/** Does the rule admit admins only? No row, or no access value, reads as yes. */
export function adminsOnly(access: string | undefined | null): boolean {
  return !access || access === "admins";
}

/** The `group:<slug>` subject of a group (D12). */
export function groupSubject(slug: string): string {
  return `group:${slug}`;
}

/** Add or drop one subject. Order is kept and twins are not made. */
export function toggleGroup(current: readonly string[], subject: string): string[] {
  return current.includes(subject)
    ? current.filter((s) => s !== subject)
    : [...current, subject];
}
