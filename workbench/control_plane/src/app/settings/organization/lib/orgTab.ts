/**
 * orgTab: the tab that a link names (PAR-1, `chat_parity.md` section 6).
 *
 * `/settings/organization?tab=members` opens the Organisation page on that tab.
 * The chat opens the page this way ("open the page where I invite users"). A
 * name that is not a tab gives nothing, so the page opens on its first tab as
 * before. The five ids are the ones that `OrganizationAdmin` draws.
 *
 * Fence: `orgTab.test.ts`.
 */

export const ORG_TABS = ["members", "seats", "branding", "requests", "email"] as const;
export type OrgTab = (typeof ORG_TABS)[number];

/** The tab that a `?tab=` value names, or null for none. The match ignores case. */
export function orgTabFromSearch(search: string): OrgTab | null {
  const value = new URLSearchParams(search).get("tab")?.trim().toLowerCase();
  return (ORG_TABS as readonly string[]).includes(value ?? "") ? (value as OrgTab) : null;
}
