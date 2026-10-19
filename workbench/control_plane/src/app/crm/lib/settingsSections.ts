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
import type { QuestionsGate } from "./questions";
import type { RulesGate } from "./rules";

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
  /**
   * The modes where the section also needs `studio_admin` of the layout
   * (CRM-C6a): the flag `CRM_FIELD_STUDIO_ORGS` allows the org, and the
   * caller is an admin of a Native org.
   */
  studioIn?: readonly ("native" | "mirror")[];
}

/**
 * The sections, in rail order (§20.4 item 2).
 *
 * `field-mapping` shows in Mirror mode, and in Native mode for a studio
 * admin only (CRM-C6a, section 20.9). `suggestions` (CRM-Q20-2) shows to an
 * admin in Mirror mode only. `questions` (CRM-Q32-3) shows to an admin in
 * Mirror mode, and only while the read of the questions answered (`gate`).
 */
export const SETTINGS_SECTIONS = [
  { id: "setup", label: "Setup and sync", icon: "Plug", audience: "admin", modes: ["native", "mirror"] },
  { id: "access", label: "Access", icon: "ShieldCheck", audience: "admin", modes: ["native", "mirror"] },
  { id: "stages", label: "Deal stages", icon: "Kanban", audience: "member", modes: ["native", "mirror"] },
  { id: "lead-statuses", label: "Lead statuses", icon: "ListOrdered", audience: "member", modes: ["native", "mirror"] },
  { id: "lost-reasons", label: "Lost reasons", icon: "CircleSlash", audience: "member", modes: ["native", "mirror"] },
  { id: "fields", label: "Fields", icon: "TextCursorInput", audience: "admin", modes: ["native", "mirror"] },
  {
    id: "field-mapping",
    label: "Field mapping",
    icon: "ArrowLeftRight",
    audience: "admin",
    modes: ["native", "mirror"],
    studioIn: ["native"],
  },
  // CRM-Q32-3: the questions that Jev could not answer, before Suggestions.
  // `GET /crm/questions` decides whether it shows (`SectionContext.questions`).
  { id: "questions", label: "Open questions", icon: "CircleHelp", audience: "admin", modes: ["mirror"] },
  // CRM-Q33: the business rules of the org and of each member. Each member
  // with CRM access sees it, while `GET /crm/rules` answers (`SectionContext.rules`).
  { id: "rules", label: "Business rules", icon: "ScrollText", audience: "member", modes: ["mirror"] },
  // CRM-Q20-2: the suggestions of the mapping, and Undo of each one in use.
  { id: "suggestions", label: "Suggestions", icon: "Sparkles", audience: "admin", modes: ["mirror"] },
  { id: "sync-health", label: "Sync health", icon: "Activity", audience: "admin", modes: ["mirror"] },
] as const satisfies readonly SettingsSection[];

export type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]["id"];

/**
 * What decides the sections a caller sees. `studioAdmin` is `studio_admin`
 * of the layout read (CRM-C6a). Absent, it is false.
 *
 * `questions` is the gate of the open questions (CRM-Q32-3, §18.14.5). The
 * client learns `CRM_QUESTIONS_ORGS` from `GET /crm/questions`: a 404
 * `questions_off` makes it `off`, and the section hides. Until the read
 * answers it is `unknown`, and the section waits too, so the row never comes
 * and goes. Absent, it is `unknown`. `openQuestions` is the open count: above
 * 0, the section opens first.
 */
export type SectionContext = {
  isAdmin: boolean;
  mode: CrmMode;
  studioAdmin?: boolean;
  questions?: QuestionsGate;
  openQuestions?: number;
  /** CRM-Q33: the gate of the business rules, from `GET /crm/rules`. Absent, it is `unknown`. */
  rules?: RulesGate;
};

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
    (section: SettingsSection) =>
      (section.audience === "member" || ctx.isAdmin) &&
      (section.modes as readonly string[]).includes(mode) &&
      (!(section.studioIn ?? []).includes(mode) || ctx.studioAdmin === true) &&
      (section.id !== "questions" || ctx.questions === "on") &&
      (section.id !== "rules" || ctx.rules === "on"),
  );
}

/**
 * The section that opens with no `?section=`: Setup for an admin, Deal
 * stages for a member. An admin with open questions gets Open questions
 * first (CRM-Q32-3, §18.14.5).
 */
export function defaultSection(ctx: SectionContext): SettingsSectionId {
  if (!ctx.isAdmin) return "stages";
  const questions =
    modeOf(ctx.mode) === "mirror" && ctx.questions === "on" && (ctx.openQuestions ?? 0) > 0;
  return questions ? "questions" : "setup";
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
