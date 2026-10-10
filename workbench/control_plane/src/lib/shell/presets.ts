/**
 * The eight presets, and the rules that pick one (`navigation_shell.md` §8.1,
 * §8.4, NS-7). The owner approved the table on 2026-10-10.
 *
 * A preset only ARRANGES three things: the pins of the sidebar's "My apps",
 * the order of the My Day cards, and the order of the command bar's jobs when
 * the bar is empty. ⚠️ **A preset never grants or denies.** Each pin draws
 * through `visibleSections`, so a pin for an app the member lacks, or for an
 * app that is not live yet, draws nothing (`pinnedPanes`).
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
  /** Pane hrefs from `nav.ts`, in the order "My apps" shows them. */
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
 * The approved table (owner, 2026-10-10).
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
  /** False for a guest: the preset arranges the cards, and pins nothing. */
  pins: boolean;
}

/**
 * The preset for a member who skipped the question or was never asked.
 * The highest role wins. A custom role reads as `member`.
 *
 * owner, admin → Founder · manager → Operations manager · member → New hire
 * · guest alone → New hire with no pins.
 */
export function roleDefault(roles: readonly string[]): RoleDefault {
  const has = new Set(roles.map((r) => r.toLowerCase()));
  if (has.has("owner") || has.has("admin")) return { preset: "founder", pins: true };
  if (has.has("manager")) return { preset: "operations-manager", pins: true };
  const onlyGuest = has.has("guest") && [...has].every((r) => r === "guest");
  return { preset: "new-hire", pins: !onlyGuest };
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
  pins: string[] | null;
  cardOrder: CardKey[] | null;
  newOrder: string[] | null;
}

export const EMPTY_SHELL: StoredShell = {
  preset: null,
  answered: null,
  pins: null,
  cardOrder: null,
  newOrder: null,
};

export interface ShellLayout {
  preset: Preset;
  answered: StoredShell["answered"];
  pins: readonly string[];
  cards: readonly CardKey[];
  jobs: readonly string[];
  needsFirst?: Preset["needsFirst"];
}

/**
 * The layout to draw. A stored value wins, field by field. With nothing
 * stored, or with no answer from the server at all, the role picks.
 */
export function shellLayout(stored: StoredShell | null | undefined, roles: readonly string[]): ShellLayout {
  const role = roleDefault(roles);
  const chosen = presetById(stored?.preset);
  const preset = chosen ?? presetById(role.preset)!;
  // The guest's empty pins apply only to the role's own default.
  const presetPins = chosen || role.pins ? preset.pins : [];
  return {
    preset,
    answered: stored?.answered ?? null,
    pins: stored?.pins ?? presetPins,
    cards: stored?.cardOrder ?? preset.cards,
    jobs: stored?.newOrder ?? preset.jobs,
    needsFirst: preset.needsFirst,
  };
}

/**
 * The pinned apps to draw, in pin order. ⚠️ **The subset rule (§8.1).** It
 * picks only from `sections`, which is `visibleSections(features, isAdmin)`,
 * so it can never draw a pane the member does not hold or that is not live.
 * A `setting` (My Profile, Appearance) is a page about the member, not an
 * app, so it is never a pin.
 */
export function pinnedPanes(pins: readonly string[], sections: readonly NavSection[]): NavPane[] {
  const held = new Map(sections.flatMap((s) => s.items).map((p) => [p.href, p]));
  const out: NavPane[] = [];
  for (const href of pins) {
    const pane = held.get(href);
    if (pane && !pane.setting && !out.includes(pane)) out.push(pane);
  }
  return out;
}

/**
 * The panes a member may pin: every pane `nav.ts` declares by its own
 * `href`. The six Center panes are derived from `lib/centers.ts`, and D49
 * keeps them unlinked, so they are not pins. The gateway refuses any other
 * href (`SHELL_PANES` in `routes/admin/me.py`), and
 * `test_auth_me_shell.py::TestOneVocabulary` holds that list to `nav.ts`.
 * So All apps shows a star only where the save can land.
 */
export const PINNABLE: ReadonlySet<string> = new Set(
  PANES.filter((p) => !p.href.startsWith("/centers/")).map((p) => p.href),
);

/** The pins with `href` added at the end, or taken out. */
export function togglePin(pins: readonly string[], href: string): string[] {
  return pins.includes(href) ? pins.filter((p) => p !== href) : [...pins, href];
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
