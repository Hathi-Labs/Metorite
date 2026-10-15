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

/** A module that the last run could not read: no Zoho scope, or turned off in Zoho. */
export function isUnavailable(entity: HealthEntity): boolean {
  return (entity.last_run.unavailable ?? 0) > 0;
}

/** Why the module is unavailable, in words the admin can act on (CRM-Q15). */
export function unavailableSentence(entity: HealthEntity): string {
  return (entity.last_run.hidden ?? 0) > 0 ? HIDDEN_SENTENCE : UNAVAILABLE_SENTENCE;
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
  return `${label} has ${recordCount(count)} records. Sync it on its own to keep Metorite fast`;
}

/** The toast of a hold write that failed, from the status and code of `PUT /crm/sync/modules/{key}`. */
export function holdFailureMessage(status: number, code: string | undefined): string {
  if (code === "unknown_module") return "Zoho has no such module, so it cannot be held.";
  if (code === "no_settings") return "Set up the CRM before you hold a module.";
  if (status === 403) return "Only an admin can hold a module.";
  return "The change did not save. Try again.";
}
