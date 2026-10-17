// The Sync health card, apart from React (WS-53 CRM-Q9, spec section 18.3 step 4).
//
// `GET /crm/sync/health` carries counts, codes and times. Every decision the
// card makes lives here, where a test can reach it.

export type HealthRun = {
  kind: string | null;
  status: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  credits_used: number | null;
};

export type HealthEntity = {
  key: string;
  /** From the last estimate. `null` when Zoho gave no count. */
  zoho_count: number | null;
  /** Our rows with `ext_source = zoho`. `null` for a key with no table of its own. */
  ours: number | null;
  /** The count keys of the last run: seen, upserted, deleted, skipped, unavailable... */
  last_run: Record<string, number>;
  /**
   * CRM-Q24: Zoho's count minus ours at the end of the last full read, when
   * above 0. The connected Zoho user cannot see those records. An older
   * gateway sends no key, so read it through `shortBy`.
   */
  short_by?: number | null;
};

/**
 * CRM-Q24: the activities that the mirror does not hold. Numbers only. An
 * older gateway sends no block, so read it through `activitiesOf`.
 */
export type HealthActivities = {
  /** Activities with no parent record, by activity entity (`note`, `task`...). */
  parentless: Record<string, number>;
  /** Activities of the last full reads, by the Zoho API name of their parent module. */
  dropped: Record<string, number>;
  /** Kept activities that wait for their parent record, by activity entity. */
  waiting: Record<string, number>;
  /** Kept activities that linked in the last 7 days, by the Zoho API name of the module. */
  recovered_7d: Record<string, number>;
  /**
   * The drops of a module that the mirror reads, which no `waiting` entry holds,
   * by the Zoho API name of the module. Each drop on a core module (Deals,
   * Accounts, Contacts, Leads), and the drops past a full wait list. An older
   * gateway sends no key.
   */
  unkept: Record<string, number>;
};

export type HealthModule = {
  api_name: string;
  label: string;
  custom: boolean;
  api_supported: boolean;
  mirrored: boolean;
};

/** `GET /crm/sync/health`. */
export type SyncHealth = {
  last_run: HealthRun | null;
  last_success_at: string | null;
  next_run_at: string | null;
  entities: HealthEntity[];
  modules: HealthModule[];
  history: HealthRun[];
  /** A run is running and its heartbeat is fresh. A crashed run is not. */
  running: boolean;
  /**
   * `resumes_at` (CRM-Q10): when the credits of the 24-hour window fall below
   * the budget. `null` while the window is below it.
   */
  credits: { budget_24h: number; used_24h: number; resumes_at?: string | null };
  /**
   * CRM-Q16: the holds, keyed by the count keys of `entities`. An older
   * gateway sends no map, so read it through `holdsOf`, never directly.
   */
  holds?: Record<string, SyncHold>;
  /** CRM-Q24: the activities that the mirror does not hold. */
  activities?: HealthActivities;
};

/**
 * One entry of `crm_settings.sync_holds` (CRM-Q16, spec section 18.8).
 * `held: false` records that an admin turned the module on, so the next
 * import does not hold it again.
 */
export type SyncHold = {
  held: boolean;
  by: "auto" | "admin";
  /** The provider count when the hold was written. `null` when unknown. */
  count: number | null;
  at: string | null;
};

/** A module is large above this many records in the provider count (CRM-Q16). */
export const LARGE_MODULE_RECORDS = 5000;

export const UNAVAILABLE_SENTENCE = "Connect Zoho again to allow this module";
/**
 * CRM-Q15: the account refuses the module. It is turned off in Zoho, or the
 * user who connected cannot see it. A reconnect does not change that.
 */
export const HIDDEN_SENTENCE =
  "Turned off in your Zoho, or hidden from the user who connected. Turn it on in Zoho to sync it";

const ENTITY_LABELS: Record<string, string> = {
  organizations: "Companies",
  contacts: "Contacts",
  leads: "Leads",
  deals: "Deals",
  activities: "Activities",
  tasks: "Tasks",
  converted_leads: "Converted leads",
  deal_contacts: "Deal contacts",
};

const MODULE_PREFIX = "module:";

/**
 * A name for an entity key. A key that is not known reads as words. A generic
 * module key (`module:<ApiName>`) takes the Zoho label of that module when the
 * caller passes the modules, and the words of its API name when not.
 */
export function entityLabel(key: string, modules?: readonly HealthModule[]): string {
  const known = ENTITY_LABELS[key];
  if (known) return known;
  const apiName = key.startsWith(MODULE_PREFIX) ? key.slice(MODULE_PREFIX.length) : null;
  if (apiName !== null) {
    const zoho = modules?.find((m) => m.api_name === apiName)?.label;
    if (zoho) return zoho;
  }
  const words = (apiName ?? key).replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}

/** The sentence for the code that a failed run keeps. */
export function errorSentence(code: string | null | undefined): string | null {
  if (!code) return null;
  switch (code) {
    case "needs_reconnect":
      return "Zoho needs you to connect again.";
    case "budget":
      return "The daily credit budget is spent. The sync continues later.";
    case "rate_limited":
      return "Zoho asked the sync to slow down. It tries again soon.";
    case "untrusted_host":
      return "The Zoho address is not trusted. Connect Zoho again.";
    case "transport":
      return "Zoho did not answer. The sync tries again soon.";
    case "not_configured":
      return "Zoho sync is not set up on this server.";
    case "mode_changed":
      return "The CRM is no longer in Mirror mode.";
    case "shutdown":
      return "The server restarted during the run. The next run continues.";
    case "stale":
      return "A run stopped without an answer. The next run takes over.";
    case "disconnected":
      return "Zoho is disconnected.";
    default:
      return "The last run failed. Try again, and tell support if it repeats.";
  }
}

/** "07:30": the local time of day of an ISO time. Empty for a missing or bad time. */
export function clockLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const hh = String(at.getHours()).padStart(2, "0");
  const mm = String(at.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/**
 * The sentence of a run that stopped on the daily budget (CRM-Q10). It names
 * the time the sync resumes, when the health answer has one.
 */
export function budgetPauseSentence(resumesAt: string | null | undefined): string | null {
  const at = clockLabel(resumesAt);
  return at ? `Paused: the daily Zoho API budget is spent. It resumes at ${at}.` : null;
}

/** "12 min ago", "3 h ago", "2 days ago". Empty for a missing or bad time. */
export function agoLabel(iso: string | null | undefined, nowMs = Date.now()): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.max(Math.round((nowMs - then) / 60_000), 0);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

/** "in 8 min", "in 2 h". Empty for a time that has passed or is missing. */
export function untilLabel(iso: string | null | undefined, nowMs = Date.now()): string {
  if (!iso) return "";
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return "";
  const minutes = Math.round((at - nowMs) / 60_000);
  if (minutes < 1) return "soon";
  if (minutes < 60) return `in ${minutes} min`;
  return `in ${Math.round(minutes / 60)} h`;
}

export function isRunning(health: SyncHealth | null | undefined): boolean {
  return health?.running === true;
}

export type StatusLines = {
  synced: string;
  next: string | null;
  error: string | null;
};

/** The three lines at the top of the card. */
export function statusLines(health: SyncHealth, nowMs = Date.now()): StatusLines {
  const ago = agoLabel(health.last_success_at, nowMs);
  const when = untilLabel(health.next_run_at, nowMs);
  const run = health.last_run;
  return {
    synced: ago ? `Synced ${ago}` : "Never synced",
    next: isRunning(health) ? "Running now" : when ? `Next run ${when}` : null,
    // A run that is still going has no error to show yet. A budget stop names
    // the time the sync resumes (CRM-Q10).
    error:
      run && !isRunning(health)
        ? (run.error === "budget" && budgetPauseSentence(health.credits.resumes_at)) ||
          errorSentence(run.error)
        : null,
  };
}

/** Ours minus Zoho's. `null` when either count is unknown or the two match. */
export function difference(entity: HealthEntity): number | null {
  if (entity.zoho_count == null || entity.ours == null) return null;
  const diff = entity.ours - entity.zoho_count;
  return diff === 0 ? null : diff;
}

export function differenceLabel(diff: number): string {
  return diff > 0 ? `+${diff}` : `-${Math.abs(diff)}`;
}

/**
 * CRM-Q19: the values of the last run that a Field map rule found filled and
 * could not put into its column. The gateway sends a count under `unmapped`,
 * never a value. An older gateway sends none, and that is 0.
 */
export function unmappedCount(entity: HealthEntity): number {
  const n = entity.last_run.unmapped;
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? n : 0;
}

/** A module that the last run could not read: no Zoho scope, or turned off in Zoho. */
export function isUnavailable(entity: HealthEntity): boolean {
  return (entity.last_run.unavailable ?? 0) > 0;
}

/** Why the module is unavailable, in words the admin can act on (CRM-Q15). */
export function unavailableSentence(entity: HealthEntity): string {
  return isTurnedOff(entity) ? HIDDEN_SENTENCE : UNAVAILABLE_SENTENCE;
}

/**
 * Zoho refused the module: it is turned off, or hidden from the user who
 * connected (CRM-Q15). A missing scope is unavailable, and not turned off.
 */
export function isTurnedOff(entity: HealthEntity): boolean {
  return isUnavailable(entity) && (entity.last_run.hidden ?? 0) > 0;
}

export function moduleState(module: HealthModule): string {
  if (!module.api_supported) return "Not available through the API";
  return module.mirrored ? "Mirrored" : "Not mirrored yet";
}

/** The credits bar. 0 to 100, and 0 with no budget. */
export function creditsPercent(credits: SyncHealth["credits"]): number {
  if (!credits.budget_24h) return 0;
  return Math.min(Math.round((credits.used_24h / credits.budget_24h) * 100), 100);
}

/** One line of the history: "Reconcile, ok, 40 credits". */
export function runLine(run: HealthRun): string {
  const kind = run.kind ? run.kind.charAt(0).toUpperCase() + run.kind.slice(1) : "Run";
  const parts = [kind, run.status ?? "unknown"];
  if (run.credits_used != null) parts.push(`${run.credits_used} credits`);
  return parts.join(", ");
}

// ── Holds (CRM-Q16, spec section 18.8) ────────────────────────────────────

/** The core keys that an admin can hold. A generic module key is `module:<ApiName>`. */
const HOLDABLE_KEYS = new Set(["organizations", "contacts", "leads", "deals", "activities", "tasks"]);

/**
 * The keys that `PUT /crm/sync/modules/{key}` accepts. `converted_leads` and
 * `deal_contacts` are counts of a step, not modules, so they have no switch.
 */
export function isHoldable(key: string): boolean {
  return HOLDABLE_KEYS.has(key) || (key.startsWith(MODULE_PREFIX) && key.length > MODULE_PREFIX.length);
}

/** The hold map. An older health answer has none, and that holds nothing. */
export function holdsOf(health: SyncHealth | null | undefined): Record<string, SyncHold> {
  const holds = health?.holds;
  return holds && typeof holds === "object" ? holds : {};
}

/** The map is the source of truth. `last_run.held` only says the last run skipped it. */
export function isHeld(health: SyncHealth | null | undefined, key: string): boolean {
  return holdsOf(health)[key]?.held === true;
}

/** The held keys, in the order of the entity rows, then any key with no row. */
export function heldKeys(health: SyncHealth | null | undefined): string[] {
  const holds = holdsOf(health);
  const rows = (health?.entities ?? []).map((e) => e.key);
  const ordered = [...rows, ...Object.keys(holds).filter((k) => !rows.includes(k))];
  return ordered.filter((k) => holds[k]?.held === true);
}

/** "18,674": the Indian digit grouping of `format.ts`. */
export function recordCount(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

/**
 * The record count of a key: the larger of the hold count and the Zoho count
 * of the last estimate. `null` when neither is known.
 */
export function recordsOf(
  health: SyncHealth | null | undefined,
  key: string,
  entity?: HealthEntity | null,
): number | null {
  const row = entity ?? health?.entities.find((e) => e.key === key) ?? null;
  const counts = [holdsOf(health)[key]?.count, row?.zoho_count].filter(
    (n): n is number => typeof n === "number" && Number.isFinite(n),
  );
  return counts.length ? Math.max(...counts) : null;
}

/** The sentence beside a held module (spec section 18.8, "The card"). */
export function holdSentence(label: string, count: number): string {
  return `${label} has ${recordCount(count)} records in Zoho. Sync it on its own when Metorite is quiet, so the rest stays fast`;
}

/**
 * A row that the card suggests a hold for: it holds more than
 * `LARGE_MODULE_RECORDS`, it still has records to sync, and `holds` has NO
 * entry for it. An entry, held or released, means an admin or the first
 * import already decided, so the card does not ask again (review P2).
 */
export function largeUnheld(health: SyncHealth | null | undefined, entity: HealthEntity): boolean {
  if (!isHoldable(entity.key) || entity.key in holdsOf(health)) return false;
  const n = recordsOf(health, entity.key, entity);
  if (n === null || n <= LARGE_MODULE_RECORDS) return false;
  return entity.ours === null || entity.ours < n;
}

/** The suggestion for a large row that is not held. */
export function largeSentence(label: string, count: number): string {
  return `${label} has ${recordCount(count)} records. Hold it, and sync it on its own when Metorite is quiet`;
}

// ── "Needs you" (CRM-Q17b item 8, spec section 18.9.6) ─────────────────────

/** What the one button of a "Needs you" item does. */
export type NeedsYouAction = "check-again" | "reconnect" | "sync-alone" | "hold";

/** The words of each button. */
export const NEEDS_YOU_ACTIONS: Record<NeedsYouAction, string> = {
  // A run of the one key, which asks Zoho at once (engine `_unavailable_now`).
  "check-again": "Check again",
  // A missing scope clears only with a new consent, in Setup and sync.
  reconnect: "Connect again",
  "sync-alone": "Sync it on its own",
  hold: "Hold it",
};

/**
 * The action of a module that the last run could not read (fix round 1,
 * P2). "Check again" is a run of the one key, so it goes only to a key that
 * Zoho turned off and that `PUT /crm/sync/modules` and `POST /crm/sync/zoho`
 * accept (`isHoldable`). A missing scope never clears by a run, so it gets
 * "Connect again". A turned-off key that a run cannot name gets no button.
 */
export function unavailableAction(entity: HealthEntity): NeedsYouAction | null {
  if (!isTurnedOff(entity)) return "reconnect";
  return isHoldable(entity.key) ? "check-again" : null;
}

export type NeedsYouItem = {
  key: string;
  kind: "unavailable" | "held" | "large";
  /** The name of the module, or null when the text starts with it. */
  name: string | null;
  text: string;
  /** `null`: the item says what to do in Zoho, and has no button. */
  action: NeedsYouAction | null;
};

/**
 * The block at the top of Sync health (CRM-Q17b item 8): the modules that
 * need the admin, each with one action. A module that Zoho refuses comes
 * first (`unavailableAction`), then a held module ("Sync it on its own"), then a
 * large module that nobody decided on ("Hold it"). A key shows once, by
 * that order.
 */
export function needsYou(health: SyncHealth | null | undefined): NeedsYouItem[] {
  if (!health) return [];
  const label = (key: string) => entityLabel(key, health.modules);
  const seen = new Set<string>();
  const items: NeedsYouItem[] = [];
  const add = (item: NeedsYouItem) => {
    if (seen.has(item.key)) return;
    seen.add(item.key);
    items.push(item);
  };
  for (const entity of health.entities.filter(isUnavailable)) {
    add({
      key: entity.key,
      kind: "unavailable",
      name: label(entity.key),
      text: unavailableSentence(entity),
      action: unavailableAction(entity),
    });
  }
  for (const key of heldKeys(health)) {
    const count = recordsOf(health, key);
    add({
      key,
      kind: "held",
      name: count === null ? label(key) : null,
      text: count === null ? "" : holdSentence(label(key), count),
      action: "sync-alone",
    });
  }
  for (const entity of health.entities.filter((e) => largeUnheld(health, e))) {
    add({
      key: entity.key,
      kind: "large",
      name: null,
      text: largeSentence(label(entity.key), recordsOf(health, entity.key, entity) ?? 0),
      action: "hold",
    });
  }
  return items;
}

// ── A short read and the activities left out (CRM-Q24, spec section 18.10) ──

/** A count from the answer, as a whole number above 0, else `null`. */
function positive(n: unknown): number | null {
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}

/** The records of a row that the connected Zoho user cannot read. `null` when none. */
export function shortBy(entity: HealthEntity): number | null {
  return positive(entity.short_by);
}

/** The rows of a short read, in the order of the table. */
export function shortReads(health: SyncHealth | null | undefined): HealthEntity[] {
  return (health?.entities ?? []).filter((entity) => shortBy(entity) !== null);
}

/**
 * The line of a short read (CRM-Q24 item 2): "Zoho counts 4,947. The connected
 * Zoho user can read 3,003. Connect with a Zoho admin to read all of them."
 * Zoho's count is the one of the row, else ours plus the gap.
 */
export function shortReadSentence(entity: HealthEntity): string | null {
  const gap = shortBy(entity);
  if (gap === null) return null;
  const zoho = positive(entity.zoho_count) ?? (positive(entity.ours) ?? 0) + gap;
  const readable = Math.max(zoho - gap, 0);
  return (
    `Zoho counts ${recordCount(zoho)}. The connected Zoho user can read ${recordCount(readable)}. ` +
    "Connect with a Zoho admin to read all of them."
  );
}

/**
 * The most records that Zoho serves by offset inside one second
 * (`zoho/client.py` `OFFSET_LIMIT`). A wider tie stops the read.
 */
export const TIE_OFFSET_LIMIT = 2000;

/**
 * The rows whose last run met a tie that is too wide (CRM-Q24 fix round). The
 * gateway counts `tie_too_wide` on the row, and the sync of that entity is
 * paused until Zoho changes those records. The `failed` count alone does not
 * say so.
 */
export function tieStalls(health: SyncHealth | null | undefined): HealthEntity[] {
  return (health?.entities ?? []).filter((entity) => positive(entity.last_run?.tie_too_wide) !== null);
}

/** The line of a paused sync: what Zoho did, and when the sync goes on. */
export function tieStallSentence(label: string): string {
  return (
    `Zoho changed more than ${recordCount(TIE_OFFSET_LIMIT)} ${label} records in the same second. ` +
    `Sync of ${label} is paused until those records change.`
  );
}

const NO_ACTIVITIES: HealthActivities = { parentless: {}, dropped: {}, waiting: {}, recovered_7d: {}, unkept: {} };

/** The activities block. An older answer has none, and that shows no line. */
export function activitiesOf(health: SyncHealth | null | undefined): HealthActivities {
  const block = health?.activities;
  if (!block || typeof block !== "object") return NO_ACTIVITIES;
  const map = (value: unknown): Record<string, number> =>
    value && typeof value === "object" ? (value as Record<string, number>) : {};
  return {
    parentless: map(block.parentless),
    dropped: map(block.dropped),
    waiting: map(block.waiting),
    recovered_7d: map(block.recovered_7d),
    unkept: map(block.unkept),
  };
}

const ACTIVITY_NOUNS: Record<string, [string, string]> = {
  note: ["note", "notes"],
  call: ["call", "calls"],
  meeting: ["meeting", "meetings"],
  task: ["task", "tasks"],
};

/** "1 Zoho task", "1,145 Zoho tasks". An unknown entity reads as activities. */
function zohoActivities(n: number, entity?: string): string {
  const [one, many] = (entity && ACTIVITY_NOUNS[entity]) || ["activity", "activities"];
  return `${recordCount(n)} Zoho ${n === 1 ? one : many}`;
}

/** The Zoho label of a module, from its API name. */
function moduleLabel(apiName: string, modules: readonly HealthModule[]): string {
  return entityLabel(`${MODULE_PREFIX}${apiName}`, modules);
}

/** A module that the mirror reads: a core module or a mirrored generic module. */
function isSynced(apiName: string, modules: readonly HealthModule[]): boolean {
  return modules.some((m) => m.api_name === apiName && m.mirrored);
}

export type ActivityLine = {
  /** A stable key for the list, `<kind>:<entity or module>`. */
  key: string;
  kind: "parentless" | "dropped" | "waiting" | "recovered";
  text: string;
};

/**
 * The lines of the activities that the mirror does not hold (CRM-Q24 item 11):
 * one line for each number above 0. A drop on a module that the mirror reads
 * is not a "module that Metorite does not sync". A kept drop shows as
 * `waiting`. A drop that no `waiting` entry holds (`unkept`: each drop on a
 * core module, and the overflow of a full wait list) shows as `dropped`, with
 * words that say its record is not in Metorite yet.
 */
export function activityLines(health: SyncHealth | null | undefined): ActivityLine[] {
  const block = activitiesOf(health);
  const modules = health?.modules ?? [];
  const lines: ActivityLine[] = [];
  const each = (map: Record<string, number>, add: (name: string, n: number) => void) => {
    for (const name of Object.keys(map).sort()) {
      const n = positive(map[name]);
      if (n !== null) add(name, n);
    }
  };
  each(block.parentless, (entity, n) => {
    const [verb, them] = n === 1 ? ["belongs", "it"] : ["belong", "them"];
    lines.push({
      key: `parentless:${entity}`,
      kind: "parentless",
      text: `${zohoActivities(n, entity)} ${verb} to no record. Metorite leaves ${them} out.`,
    });
  });
  each(block.dropped, (module, n) => {
    // The gateway knows a module that it syncs, so an `unkept` count wins.
    if (isSynced(module, modules) || positive(block.unkept[module]) !== null) return;
    const verb = n === 1 ? "belongs" : "belong";
    lines.push({
      key: `dropped:${module}`,
      kind: "dropped",
      text: `${zohoActivities(n)} ${verb} to ${moduleLabel(module, modules)}, a module that Metorite does not sync.`,
    });
  });
  each(block.unkept, (module, n) => {
    const verb = n === 1 ? "links" : "link";
    lines.push({
      key: `unkept:${module}`,
      kind: "dropped",
      text: `${zohoActivities(n)} ${verb} to ${moduleLabel(module, modules)} records that are not in Metorite yet.`,
    });
  });
  each(block.waiting, (entity, n) => {
    const [verb, their, them] = n === 1 ? ["waits", "its", "it"] : ["wait", "their", "them"];
    lines.push({
      key: `waiting:${entity}`,
      kind: "waiting",
      text: `${zohoActivities(n, entity)} ${verb} for ${their} record. Metorite links ${them} when it reads that record.`,
    });
  });
  each(block.recovered_7d, (module, n) => {
    lines.push({
      key: `recovered:${module}`,
      kind: "recovered",
      text: `${zohoActivities(n)} on ${moduleLabel(module, modules)} came back in the last 7 days.`,
    });
  });
  return lines;
}

/** The toast of a hold write that failed, from the status and code of `PUT /crm/sync/modules/{key}`. */
export function holdFailureMessage(status: number, code: string | undefined): string {
  if (code === "unknown_module") return "Zoho has no such module, so it cannot be held.";
  if (code === "no_settings") return "Set up the CRM before you hold a module.";
  if (status === 403) return "Only an admin can hold a module.";
  return "The change did not save. Try again.";
}
