// ── The sections of CRM settings (WS-53 CRM-C1, crm_platform.md §20.4) ─────
//
// The owner said on 2026-10-12: "The CRM settings are on one overwhelming
// page." Settings now opens one section at a time. Each section is a child
// row of "CRM settings" in the rail, and `?section=<id>` names it.
//
// This file is the ONE list of the sections. The rail draws its child rows
// from it, the page picks the cards from it, and the title bar reads its
// label. Pure, so the test holds the table without a browser.
//
// ⚠️ A `null` mode reads as Native (§20.4 item 3). `CrmMode` can be `null`
// before an org picks a mode, and the page treats only "mirror" as Mirror.
//
// It imports no runtime value from `urlState.ts`, because `urlState.ts`
// imports this file to parse `?section=`. Fence: `components/crmC1.test.ts`.

import type { CrmMode } from "./connect";

/** Who may open a section. */
export type SectionAudience = "admin" | "member";

export interface SettingsSection {
  /** The `?section=` value. It never changes, so a link keeps working. */
  id: string;
  /** What the member reads, in the rail and in the title bar. */
  label: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon: string;
  /** "admin" shows to an admin only. "member" shows to each member with CRM access. */
  audience: SectionAudience;
  /** The modes that show the section. */
  modes: readonly ("native" | "mirror")[];
}

/**
 * The sections, in rail order (§20.4 item 2).
 *
 * `field-mapping` is Mirror only. CRM-C6 adds Native to it.
 */
export const SETTINGS_SECTIONS = [
  { id: "setup", label: "Setup and sync", icon: "Plug", audience: "admin", modes: ["native", "mirror"] },
  { id: "access", label: "Access", icon: "ShieldCheck", audience: "admin", modes: ["native", "mirror"] },
  { id: "stages", label: "Deal stages", icon: "Kanban", audience: "member", modes: ["native", "mirror"] },
  { id: "lead-statuses", label: "Lead statuses", icon: "ListOrdered", audience: "member", modes: ["native", "mirror"] },
  { id: "lost-reasons", label: "Lost reasons", icon: "CircleSlash", audience: "member", modes: ["native", "mirror"] },
  { id: "fields", label: "Fields", icon: "TextCursorInput", audience: "admin", modes: ["native", "mirror"] },
  { id: "field-mapping", label: "Field mapping", icon: "ArrowLeftRight", audience: "admin", modes: ["mirror"] },
  { id: "sync-health", label: "Sync health", icon: "Activity", audience: "admin", modes: ["mirror"] },
] as const satisfies readonly SettingsSection[];

export type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]["id"];

/** What decides the sections a caller sees. */
export type SectionContext = { isAdmin: boolean; mode: CrmMode };

/** Is this a section id at all? An unknown `?section=` parses as `null`. */
export function isSettingsSection(value: string | null | undefined): value is SettingsSectionId {
  return SETTINGS_SECTIONS.some((section) => section.id === value);
}

/** The label of a section, for the title bar. */
export function sectionLabel(id: SettingsSectionId): string {
  return SETTINGS_SECTIONS.find((section) => section.id === id)?.label ?? "";
}

/** The mode of a section rule. Only "mirror" is Mirror (§20.4 item 3). */
function modeOf(mode: CrmMode): "native" | "mirror" {
  return mode === "mirror" ? "mirror" : "native";
}

/** The sections this caller can open, in rail order. */
export function visibleSections(ctx: SectionContext): SettingsSection[] {
  const mode = modeOf(ctx.mode);
  return SETTINGS_SECTIONS.filter(
    (section) =>
      (section.audience === "member" || ctx.isAdmin) &&
      (section.modes as readonly string[]).includes(mode),
  );
}

/** The section that opens with no `?section=`: Setup for an admin, Deal stages for a member. */
export function defaultSection(ctx: SectionContext): SettingsSectionId {
  return ctx.isAdmin ? "setup" : "stages";
}

/** The section the page draws: the one asked for, or the default when the caller cannot open it. */
export function shownSection(
  section: string | null | undefined,
  ctx: SectionContext,
): SettingsSectionId {
  const visible = visibleSections(ctx);
  return visible.some((s) => s.id === section) ? (section as SettingsSectionId) : defaultSection(ctx);
}

/**
 * The section to send the URL to, or `null` when the URL stays.
 *
 * Only a missing or unknown section is rewritten, to the default. The page
 * sends it through `replaceView`, so Back does not return to the bad URL
 * (§20.4 item 6). Another tab never has a section.
 *
 * A known section that the caller cannot open now keeps its URL, and the
 * page draws the default (`shownSection`). The caller or the mode can be a
 * guess for a moment: a stale access read during a gateway restart, or a
 * failed mode read. A rewrite then would lose an admin's link for good
 * (CRM-C1 review). For the same reason nothing is rewritten until `trusted`
 * is true, which means that the access read and the mode read both settled.
 */
export function sectionRedirect(
  view: { tab: string; section?: string | null },
  ctx: SectionContext,
  trusted: boolean,
): SettingsSectionId | null {
  if (view.tab !== "settings" || !trusted) return null;
  return isSettingsSection(view.section) ? null : defaultSection(ctx);
}

/**
 * The view with the section that the page draws, for `boundaryKey`. A
 * settings URL with no section and the same URL after the rewrite then give
 * one key, so the subtree mounts once (CRM-C1 review).
 */
export function withShownSection<V extends { tab: string; section?: string | null }>(
  view: V,
  ctx: SectionContext,
): V & { section: SettingsSectionId | null } {
  return { ...view, section: view.tab === "settings" ? shownSection(view.section, ctx) : null };
}
