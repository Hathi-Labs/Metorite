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
  credits: { budget_24h: number; used_24h: number };
};

export const UNAVAILABLE_SENTENCE = "Connect Zoho again to allow this module";

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

/** A name for an entity key. A key that is not known reads as words. */
export function entityLabel(key: string): string {
  const known = ENTITY_LABELS[key];
  if (known) return known;
  const words = key.replace(/_/g, " ").trim();
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
  return health?.last_run?.status === "running";
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
    // A run that is still going has no error to show yet.
    error: run && run.status !== "running" ? errorSentence(run.error) : null,
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

/** A module that the last run could not read, for lack of a Zoho scope. */
export function isUnavailable(entity: HealthEntity): boolean {
  return (entity.last_run.unavailable ?? 0) > 0;
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
