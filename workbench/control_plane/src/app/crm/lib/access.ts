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

/** A member who lacks the `feature:crm` grant. */
export const NO_FEATURE_TITLE = "You do not have access to the CRM";
export const NO_FEATURE_BODY = "Ask an admin for the CRM permission.";

/** An org where the CRM is switched off (`CRM_ENABLED` or `CRM_ORGS`). */
export const CRM_OFF_TITLE = "The CRM is not turned on for your organization yet.";

/** The toast of the `new-lead` job when the form cannot open. */
export const NEW_LEAD_MIRROR = "New leads come from Zoho in this organization.";

/**
 * The words of the CRM access rule 403 (`routes/crm/access.py::_forbidden`).
 * The feature gate answers 403 too, with a "missing permission" message.
 */
const ACCESS_RULE_WORDS = "has not given you access to the CRM";

/** Which 403 is it: the access rule (T5), or a missing `feature:crm`? */
export type Denial = "access" | "feature";

/** The kind of a gateway 403, or `null` when the error is not a 403. */
export function denialOf(err: unknown): Denial | null {
  if (!(err instanceof CrmError) || err.status !== 403) return null;
  return err.message.includes(ACCESS_RULE_WORDS) ? "access" : "feature";
}

/** The words for a job that cannot run, by first-open phase. `null` when none applies. */
export function refusalOf(phase: string): string | null {
  switch (phase) {
    case "no-access":
      return NO_ACCESS_TITLE;
    case "no-feature":
      return NO_FEATURE_TITLE;
    case "off":
      return CRM_OFF_TITLE;
    default:
      return null;
  }
}

/** Is this error a 404 from a CRM route? With the CRM off, each one answers it. */
export function isNotFound(err: unknown): boolean {
  return err instanceof CrmError && err.status === 404;
}

/** Does the rule admit admins only? No row, or no access value, reads as yes. */
export function adminsOnly(access: string | undefined | null): boolean {
  return !access || access === "admins";
}

/**
 * Does the page need the organization's group list?
 *
 * Only when the rule is "groups". An admin who chose "admins" or "everyone"
 * sees no group checkboxes, so the read is wasted. In CRM-Q3 (spec section
 * 18.6) the read moved out of the first effect, so the settings page opens with
 * no extra request that could fail.
 */
export function wantsGroups(access: string | undefined | null): boolean {
  return access === "groups";
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
