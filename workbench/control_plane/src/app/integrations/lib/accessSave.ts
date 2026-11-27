// ── Who can use an integration: the writes (ACC-2) ─────────────────────────
//
// Fence: `accessSave.test.ts`. Spec: integrations_console.md §3.9.5.
//
// The editor and the "Set to Everyone" confirm save through here. The PUT has
// no version check. So each save reads the stored rule first, and it writes
// nothing when that rule is not the one that the admin saw. A write of another
// admin between that read and the PUT is still replaced.

import { invalidate, peek, put, read } from "@/lib/dataCache";

import * as api from "./api";
import { ruleBody, sameStored, withItem, type AccessItem, type AccessList, type RuleBody } from "./whoCanUse";

/** The one cached read of the rules. `ConnectionsView` watches it. */
export const ACCESS_KEY = "integrations:access";

const onlyAccess = (key: string) => key === ACCESS_KEY;

/** Store a newer list. A read that is in flight then cannot replace it. */
function keepList(list: AccessList): void {
  invalidate(onlyAccess);
  put(ACCESS_KEY, list);
}

/**
 * Draw a saved rule at once. The base is the newest cached list, so a read that
 * landed during the save keeps its news of each other service.
 */
export function keepSavedRule(saved: AccessItem): void {
  const latest = peek<AccessList | null>(ACCESS_KEY)?.data;
  if (latest) {
    keepList(withItem(latest, saved));
  } else if (latest === undefined) {
    // Another write dropped the entry. A read that started before this save
    // must not store its answer, so start a new one.
    invalidate(onlyAccess);
    void read(ACCESS_KEY, api.getIntegrationAccess, { force: true }).catch(() => undefined);
  }
}

export type SaveOutcome =
  | { kind: "saved"; item: AccessItem }
  /** The stored rule is not the one that the admin saw. Nothing was written. */
  | { kind: "changed"; current: AccessItem }
  /** The gateway shows no rule for the service now. Nothing was written. */
  | { kind: "gone" };

/**
 * Save `rule` over `seen`, the rule that the screen shows as stored. It throws
 * for a read or a write that fails, and a failed read writes nothing.
 */
export async function saveAccessRule(seen: AccessItem, rule: RuleBody): Promise<SaveOutcome> {
  const list = await api.getIntegrationAccess();
  const current = list?.services.find((i) => i.service === seen.service);
  if (!list || !current) return { kind: "gone" };
  if (!sameStored(seen, current)) {
    keepList(list);
    return { kind: "changed", current };
  }
  const item = await api.setIntegrationAccess(seen.service, rule);
  keepSavedRule(item);
  return { kind: "saved", item };
}

/** "Set to Everyone" for a stored rule that no row shows. */
export function clearAccessRule(seen: AccessItem): Promise<SaveOutcome> {
  return saveAccessRule(seen, ruleBody("everyone", [], []));
}
