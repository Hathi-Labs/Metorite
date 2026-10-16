// ── The words and the marks of Knowledge (WS-61 KB-2a) ──────────────────────
//
// Pure. One label for each state, each reason of a version and each level,
// so that the rail, the landing and the page view say the same thing.
// A state resolves to a Badge TONE, never to a colour (DESIGN_SYSTEM.md §1).

import type { BadgeTone } from "@/components/ui/Badge";

import type { PageState, PageSummary, Space, SpaceKind, SpaceLevel } from "./types";

/** The label, the tone and the icon of each page state (§4.2). */
export const STATE_LABELS: Record<PageState, { label: string; tone: BadgeTone; icon: string }> = {
  draft: { label: "Draft", tone: "warning", icon: "FilePen" },
  published: { label: "Published", tone: "success", icon: "CircleCheck" },
  archived: { label: "Archived", tone: "neutral", icon: "Archive" },
};

/** The state of a page in words. An unknown state reads as itself. */
export function stateLabel(state: string): string {
  return STATE_LABELS[state as PageState]?.label ?? state;
}

/** The Badge tone of a state. An unknown state is neutral. */
export function stateTone(state: string): BadgeTone {
  return STATE_LABELS[state as PageState]?.tone ?? "neutral";
}

/** The rail icon of a page: a pen for a draft, a page for the rest. */
export function pageIcon(page: Pick<PageSummary, "state">): string {
  return page.state === "draft" ? "FilePen" : "FileText";
}

/** Each reason of `kb_page_versions` in words (§4.2). */
export const REASON_LABELS: Record<string, string> = {
  create: "Created",
  edit: "Edited",
  publish: "Published",
  move: "Moved",
  archive: "Archived",
  restore: "Restored",
  revert: "Reverted",
  agent_proposal: "Proposed by an agent",
  accept: "Accepted",
  import: "Imported",
};

/** A version's reason in words. An unknown reason reads as itself. */
export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

/** What a member may do in a space, in words. */
export const LEVEL_LABELS: Record<SpaceLevel, string> = {
  read: "You can read",
  write: "You can write",
  manage: "You manage",
};

/** True when the level lets the member make and edit pages (`core.can_write_page`). */
export function canWriteIn(level: SpaceLevel | null | undefined): boolean {
  return level === "write" || level === "manage";
}

/** The default icon of each kind of space. A space's own icon wins. */
const KIND_ICONS: Record<SpaceKind, string> = {
  company: "Building2",
  team: "Users",
  me: "Lock",
  agent: "Bot",
};

/** The icon of a space: its own, else the one of its kind. */
export function spaceIcon(space: Pick<Space, "icon" | "kind">): string {
  return space.icon?.trim() || KIND_ICONS[space.kind] || "BookOpen";
}

/** "1 page", "3 pages". */
export function pageCount(n: number): string {
  return `${n} ${n === 1 ? "page" : "pages"}`;
}

/**
 * A date that a reader in India and a reader in the US read as the same day
 * (DESIGN_SYSTEM.md §10): "16 Apr 2027". Empty for no date.
 */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** The name of the space a page made with no space lands in (§4.1). */
export const MY_NOTES = "My notes";

/** The value of the "My notes" choice when the member has no `me` space yet. */
export const MY_NOTES_CHOICE = "";

/**
 * The spaces a member may make a page in, for the New page form. The `me`
 * space is made on the first write, so before that the form offers "My
 * notes" as a choice with no id, and the gateway makes the space.
 */
export function writableSpaces(spaces: readonly Space[]): Array<{ value: string; label: string }> {
  const out = spaces
    .filter((s) => canWriteIn(s.level) && !s.archived_at)
    .map((s) => ({ value: s.id, label: s.name }));
  if (!spaces.some((s) => s.kind === "me")) out.push({ value: MY_NOTES_CHOICE, label: MY_NOTES });
  return out;
}

/**
 * The space a new page starts in: the space on screen when the member can
 * write there, else their `me` space (or the "My notes" choice).
 */
export function defaultSpaceChoice(spaces: readonly Space[], current: string | null): string {
  const here = spaces.find((s) => s.id === current);
  if (here && canWriteIn(here.level)) return here.id;
  const me = spaces.find((s) => s.kind === "me");
  return me ? me.id : MY_NOTES_CHOICE;
}

/** The body of a create: no `space_id` for the "My notes" choice. */
export function createBody(input: { space: string; parentId?: string | null; title: string }): {
  space_id?: string;
  parent_id?: string;
  title: string;
} {
  const body: { space_id?: string; parent_id?: string; title: string } = { title: input.title.trim() };
  if (input.space !== MY_NOTES_CHOICE) body.space_id = input.space;
  if (input.parentId) body.parent_id = input.parentId;
  return body;
}

/** Only the fields that changed, or `null` when nothing did. A title is trimmed. */
export function patchBody(
  saved: { title: string; body_md: string },
  draft: { title: string; body_md: string },
): { title?: string; body_md?: string } | null {
  const out: { title?: string; body_md?: string } = {};
  const title = draft.title.trim();
  if (title && title !== saved.title) out.title = title;
  if (draft.body_md !== saved.body_md) out.body_md = draft.body_md;
  return Object.keys(out).length ? out : null;
}

/**
 * The body of the publish confirm. In a `me` space a published page stays
 * the member's own (`core.visible_pages_clause` gives no reader a `me`
 * space), so the words must not promise readers (review of KB-2a, P3-b).
 */
export function publishBody(space: Pick<Space, "kind" | "name"> | null): string {
  const review = "The review date is set to 180 days from today.";
  if (space?.kind === "me") {
    return `Only you can read pages in ${space.name}, also after you publish. ${review}`;
  }
  return `Every reader of ${space?.name ?? "this space"} can then read it, and search will find it. ${review}`;
}
