/**
 * The eight presets, and the rules that pick one (`navigation_shell.md` §8.1,
 * §8.4, NS-7). The owner approved the table on 2026-10-10.
 *
 * A preset only ARRANGES three things: the apps an answer stars in the
 * sidebar, the order of the My Day cards, and the order of the command bar's
 * jobs when the bar is empty. ⚠️ **A preset never grants or denies.** A star
 * draws through `visibleSections`, so a star for an app the member lacks, or
 * for an app that is not live yet, draws nothing (`starredHrefs`).
 *
 * **The star model** (owner, 2026-10-11). The owner said: "The higher-level
 * categories should always remain in the left sidebar, unless there are no
 * apps beneath them. … There is no 'my apps' concept; only favorited apps
 * are shown under appropriate categories." So the sidebar shows each
 * category in `nav.ts` order, and under it only the apps the member starred,
 * in `nav.ts` order. Nothing moves between categories.
 *
 * Everything here is pure, so the rules are assertions and not clicks. Fence:
 * `presets.test.ts`. The gateway checks only the names (`routes/admin/me.py`
 * `SHELL_PRESETS`, `SHELL_CARDS`), and `test_auth_me_shell.py` fails when the
 * two lists differ.
 */
import { PANES, type NavPane, type NavSection } from "@/lib/nav";

export type PresetId =
  | "founder"
  | "sales-manager"
  | "marketing-lead"
  | "finance-manager"
  | "operations-manager"
  | "engineer"
  | "accounts-assistant"
  | "new-hire";

/**
 * The My Day cards a preset may order. `team-pulse` and `out-today` wait on
 * NS-5. A key for a card that is not built draws nothing.
 */
export type CardKey = "needs" | "today" | "next" | "team-pulse" | "out-today";
export const CARD_KEYS: readonly CardKey[] = ["needs", "today", "next", "team-pulse", "out-today"];

/** The cards My Day draws today, in the page's own order (NS-3). */
export const BUILT_CARDS: readonly CardKey[] = ["needs", "today", "next"];

export interface Preset {
  id: PresetId;
  label: string;
  /**
   * Pane hrefs from `nav.ts` that an answer stars, beside every Personal
   * Center app (`answerStars`). The field keeps its NS-7 name.
   */
  pins: readonly string[];
  /** My Day's cards, first to last. A card not named here follows them. */
  cards: readonly CardKey[];
  /**
   * The group of Needs you that comes first. Only an org admin gets
   * `approval` rows (NS-3 slice C), so for anyone else the order is the
   * server's.
   */
  needsFirst?: "needs_reply" | "approval";
  /** Job ids from `registry.ts` `JOBS`, first in the empty command bar. */
  jobs: readonly string[];
  /** Desk mode (§8.3). It waits on NS-8, so nothing reads it yet. */
  desk?: true;
}

/**
 * The approved table (owner, 2026-10-10). Under the star model an answer
 * stars these apps AND every Personal Center app (owner, 2026-10-11,
 * "Personal + preset").
 *
 * ⚠️ Two pins of the table are not here. CRM IS here: it is a `preview`
 * pane, so it draws when the owner promotes it (H-21). **Invoices is not**,
 * because no Invoices pane exists in `nav.ts`. A pin for a pane that does
 * not exist is a guess at its href. The Finance manager gains it when the
 * pane is added.
 */
export const PRESETS: readonly Preset[] = [
  {
    id: "founder",
    label: "Founder",
    pins: ["/projects", "/approvals", "/email", "/chat"],
    cards: ["needs", "today", "team-pulse"],
    jobs: ["new-project", "invite", "capture"],
  },
  {
    id: "sales-manager",
    label: "Sales manager",
    pins: ["/email", "/whatsapp", "/projects", "/crm"],
    cards: ["needs", "today", "next"],
    needsFirst: "needs_reply",
    jobs: ["compose", "whatsapp-reply", "capture"],
  },
  {
    id: "marketing-lead",
    label: "Marketing lead",
    pins: ["/projects", "/email", "/chat"],
    cards: ["needs", "next", "today"],
    jobs: ["capture", "new-chat", "compose"],
  },
  {
    id: "finance-manager",
    label: "Finance manager",
    pins: ["/approvals", "/email", "/projects"],
    cards: ["needs", "today"],
    needsFirst: "approval",
    jobs: ["review-approvals", "compose"],
  },
  {
    id: "operations-manager",
    label: "Operations manager",
    pins: ["/projects", "/people", "/calendar"],
    cards: ["needs", "today", "next", "out-today"],
    jobs: ["new-project", "capture", "find-person"],
  },
  {
    id: "engineer",
    label: "Engineer",
    pins: ["/tasks", "/projects", "/calendar", "/chat"],
    cards: ["next", "today", "needs"],
    jobs: ["capture", "plan-day", "new-chat"],
  },
  {
    id: "accounts-assistant",
    label: "Accounts assistant",
    pins: ["/tasks", "/email"],
    cards: ["needs", "next"],
    jobs: ["capture", "compose"],
    desk: true,
  },
  {
    id: "new-hire",
    label: "New hire",
    pins: ["/tasks", "/people", "/chat"],
    cards: ["today", "next"],
    jobs: ["edit-profile", "find-person", "new-chat"],
  },
];

export const PRESET_IDS: readonly PresetId[] = PRESETS.map((p) => p.id);

export function presetById(id: string | null | undefined): Preset | null {
  return PRESETS.find((p) => p.id === id) ?? null;
}

// ── The first sign-in question (§8.4) ──────────────────────────────────────

export const QUESTION = "What will you do most here?";

export interface Answer {
  label: string;
  preset: PresetId;
  icon: string;
}

/** The six answers, in the order the dialog lists them. */
export const ANSWERS: readonly Answer[] = [
  { label: "Run the company", preset: "founder", icon: "Building2" },
  { label: "Sell and look after customers", preset: "sales-manager", icon: "Handshake" },
  { label: "Plan campaigns and content", preset: "marketing-lead", icon: "Megaphone" },
  { label: "Handle money and approvals", preset: "finance-manager", icon: "Wallet" },
  { label: "Run projects and operations", preset: "operations-manager", icon: "FolderKanban" },
  { label: "Build and ship the work", preset: "engineer", icon: "Hammer" },
];

/** The preset an answer picks, or `null` for a label that is not an answer. */
export function presetForAnswer(answer: string): PresetId | null {
  return ANSWERS.find((a) => a.label === answer)?.preset ?? null;
}

// ── The role's default (rule 2) ────────────────────────────────────────────

export interface RoleDefault {
  preset: PresetId;
}

/**
 * The preset for a member who skipped the question or was never asked. It
 * orders My Day's cards and the empty command bar. It stars nothing: with
 * no stored stars, every held app shows (`starredHrefs`).
 * The highest role wins. A custom role reads as `member`.
 *
 * owner, admin → Founder · manager → Operations manager · member and guest
 * → New hire.
 */
export function roleDefault(roles: readonly string[]): RoleDefault {
  const has = new Set(roles.map((r) => r.toLowerCase()));
  if (has.has("owner") || has.has("admin")) return { preset: "founder" };
  if (has.has("manager")) return { preset: "operations-manager" };
  return { preset: "new-hire" };
}

export function presetForRole(roles: readonly string[]): PresetId {
  return roleDefault(roles).preset;
}

// ── What the member sees ───────────────────────────────────────────────────

/** `GET /auth/me/shell`. `null` in a field means "the preset's". */
export interface StoredShell {
  preset: PresetId | null;
  /** `"skipped"` is a choice. `null` means the member was never asked. */
  answered: "answered" | "skipped" | null;
  /**
   * ⚠️ **The STARRED apps** (owner, 2026-10-11). The field keeps its NS-7
   * name, `pins`, so no row and no migration changes (R6). `null` means
   * that the member never chose, so every held app shows.
   */
  pins: string[] | null;
  /**
   * The hrefs the member could see when they last saved. An app that is
   * not here is new to them, and it shows starred (`starredHrefs`).
   */
  known: string[] | null;
  cardOrder: CardKey[] | null;
  newOrder: string[] | null;
}

export const EMPTY_SHELL: StoredShell = {
  preset: null,
  answered: null,
  pins: null,
  known: null,
  cardOrder: null,
  newOrder: null,
};

export interface ShellLayout {
  preset: Preset;
  answered: StoredShell["answered"];
  /** The stored stars. `null`: every held app shows. */
  pins: readonly string[] | null;
  known: readonly string[] | null;
  cards: readonly CardKey[];
  jobs: readonly string[];
  needsFirst?: Preset["needsFirst"];
}

/**
 * The layout to draw. A stored value wins, field by field. With nothing
 * stored, or with no answer from the server at all, the role picks the
 * cards and the jobs, and every held app shows.
 */
export function shellLayout(stored: StoredShell | null | undefined, roles: readonly string[]): ShellLayout {
  const preset = presetById(stored?.preset) ?? presetById(roleDefault(roles).preset)!;
  return {
    preset,
    answered: stored?.answered ?? null,
    pins: stored?.pins ?? null,
    known: stored?.known ?? null,
    cards: stored?.cardOrder ?? preset.cards,
    jobs: stored?.newOrder ?? preset.jobs,
    needsFirst: preset.needsFirst,
  };
}

// ── Stars (owner, 2026-10-11) ──────────────────────────────────────────────

/**
 * Whether a member may star a pane. A `setting` (My Profile, Appearance) is
 * a page about the member in the account menu, not an app. The six Center
 * panes are derived from `lib/centers.ts`, and D49 keeps them unlinked. The
 * gateway refuses their hrefs (`SHELL_PANES` in `routes/admin/me.py`), and
 * `test_auth_me_shell.py::TestOneVocabulary` holds that list to `nav.ts`.
 * Home (`/`) is not a `nav.ts` pane, and it always shows.
 */
export function canStar(pane: NavPane): boolean {
  return !pane.setting && (pane.door ?? "sidebar") === "sidebar" && !pane.href.startsWith("/centers/");
}

/** The hrefs a member may star. All apps shows a star only on these. */
export const STARRABLE: ReadonlySet<string> = new Set(PANES.filter(canStar).map((p) => p.href));

/** The held panes a member may star, in `nav.ts` order. */
export function starrableHeld(sections: readonly NavSection[]): NavPane[] {
  return sections.flatMap((s) => s.items).filter(canStar);
}

/** The two stored fields that say what the sidebar shows. */
export interface Stars {
  pins: readonly string[] | null;
  known: readonly string[] | null;
}

/**
 * The starred hrefs among the apps the member holds, or `null` for "every
 * held app shows". ⚠️ **The subset rule (§8.1).** It reads `sections`
 * alone, which is `visibleSections(features, isAdmin)`, so a star never
 * draws a pane the member does not hold or that is not live.
 *
 * The rules, in order:
 *
 * 1. `pins` null: the member never chose (never asked, or skipped). Every
 *    held app shows, so the sidebar is the one it was before stars.
 * 2. ⚠️ **`known` null: every held app shows too.** That is a row that
 *    NS-7 saved before the star model. NS-7's pins were a "My apps" list ON
 *    TOP of the whole sidebar, never a filter, and a member who pinned and
 *    then unpinned stored `pins: []`. Reading those pins as stars would
 *    hide apps that the member never chose to hide, or every app. So such
 *    a row draws the sidebar of the day before the merge. The first save
 *    of the star model (a star, a hide, or the dialog) writes `known`, and
 *    from then on the stars filter.
 * 3. An app in `pins` shows.
 * 4. **An app that is not in `known` is new to the member** (newly granted,
 *    or newly live), and it shows until a save covers it. Without this
 *    rule, a new app would be hidden from every member who ever saved.
 */
export function starredHrefs(stars: Stars | null | undefined, sections: readonly NavSection[]): Set<string> | null {
  if (!stars || stars.pins === null || stars.known === null) return null;
  const pins = new Set(stars.pins);
  const known = new Set(stars.known);
  return new Set(
    starrableHeld(sections)
      .map((p) => p.href)
      .filter((h) => pins.has(h) || !known.has(h)),
  );
}

/** Whether `href` shows in the sidebar for these stars. */
export function isStarred(stars: Stars | null | undefined, sections: readonly NavSection[], href: string): boolean {
  const set = starredHrefs(stars, sections);
  return set === null ? starrableHeld(sections).some((p) => p.href === href) : set.has(href);
}

/**
 * The stars to SAVE, from the hrefs the member wants to show. Every save
 * writes `known` as every app the member can star now, so nothing they saw
 * reads as new again. A stored star for an app the member does not hold
 * today is kept, so it shows again when the grant comes back.
 */
export function starsToSave(
  shown: Iterable<string>,
  sections: readonly NavSection[],
  before: Stars | null | undefined,
): { pins: string[]; known: string[] } {
  const held = starrableHeld(sections).map((p) => p.href);
  const want = new Set(shown);
  const kept = (before?.pins ?? []).filter((h) => !held.includes(h) && STARRABLE.has(h));
  return { pins: [...held.filter((h) => want.has(h)), ...kept], known: held };
}

/** The stars with `href` shown, or hidden. */
export function toggleStar(
  stars: Stars | null | undefined,
  sections: readonly NavSection[],
  href: string,
): { pins: string[]; known: string[] } {
  const now = starredHrefs(stars, sections) ?? new Set(starrableHeld(sections).map((p) => p.href));
  if (now.has(href)) now.delete(href);
  else now.add(href);
  return starsToSave(now, sections, stars);
}

/**
 * The one app every answer stars for a member who holds it: Approvals.
 * Orchestrator decision, 2026-10-11. The bell and My Day are owner-only
 * flags that are not reliably on, so the sidebar row is an approver's main
 * door to the queue (§3.2a item 2).
 */
export const ALWAYS_STARRED: readonly string[] = ["/approvals"];

/**
 * What an answer stars (owner, 2026-10-11, "Personal + preset"): every
 * Personal Center app the member holds, the preset's apps that they hold,
 * and `ALWAYS_STARRED`. In `nav.ts` order.
 */
export function answerStars(preset: Preset, sections: readonly NavSection[]): string[] {
  return starrableHeld(sections)
    .filter((p) => p.team === "personal" || preset.pins.includes(p.href) || ALWAYS_STARRED.includes(p.href))
    .map((p) => p.href);
}

/**
 * The preset's own apps that the member holds, beyond Personal Center. The
 * first sign-in question names them under each answer.
 */
export function presetExtras(preset: Preset, sections: readonly NavSection[]): NavPane[] {
  return starrableHeld(sections).filter((p) => p.team !== "personal" && preset.pins.includes(p.href));
}

/**
 * The cards to draw, in the preset's order. `available` is what the member
 * gets (`cardsFor`). A card the preset does not name keeps its place after
 * the named ones, so a preset never hides a card.
 */
export function orderCards<T extends string>(available: readonly T[], order: readonly string[]): T[] {
  const named = order.filter((k): k is T => (available as readonly string[]).includes(k));
  return [...new Set([...named, ...available])];
}
