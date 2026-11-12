/**
 * The open counter of the pin suggestion (PKG-19a-1). It lives in this
 * browser, for each account, in the style of `rememberRecent` in
 * `registry.ts`. It holds open times and hide dates. It holds no tenant row.
 *
 * Every read and write is in a try/catch. When storage is off, the counter
 * forgets and the member sees no suggestion. Nothing here writes the
 * arrangement (D111). The fence is `arrangement.test.ts`, check 4.
 *
 * Pass `storage` in a test. The default is `localStorage`, read only inside
 * the try.
 */
import { DAY_MS, SUGGEST_HIDE_DAYS, SUGGEST_WINDOW_DAYS, paneHrefOf } from "./pinSuggest";

/** The most open times kept in all, over every pane. */
export const OPENS_CAP = 200;

export type StoreLike = Pick<Storage, "getItem" | "setItem">;

const keyOf = (kind: "opens" | "hidden", email: string | null) =>
  `cc-shell-pin-${kind}::${(email ?? "anon").toLowerCase()}`;

export const opensKey = (email: string | null) => keyOf("opens", email);
export const hiddenKey = (email: string | null) => keyOf("hidden", email);

function store(storage?: StoreLike): StoreLike | null {
  try {
    return storage ?? localStorage;
  } catch {
    return null;
  }
}

function readJson(key: string, storage?: StoreLike): unknown {
  try {
    const raw = store(storage)?.getItem(key);
    return raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    return null;
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Pane href to open times. A bad value reads as empty. */
export function readOpens(email: string | null, storage?: StoreLike): Record<string, number[]> {
  const raw = readJson(opensKey(email), storage);
  const out: Record<string, number[]> = {};
  if (!isRecord(raw)) return out;
  for (const [href, times] of Object.entries(raw)) {
    if (!Array.isArray(times)) continue;
    const ok = times.filter((t): t is number => typeof t === "number" && Number.isFinite(t));
    if (ok.length) out[href] = ok;
  }
  return out;
}

/** Pane href to the end of its hide. An ended hide is dropped. */
export function readHidden(email: string | null, now: number, storage?: StoreLike): Record<string, number> {
  const raw = readJson(hiddenKey(email), storage);
  const out: Record<string, number> = {};
  if (!isRecord(raw)) return out;
  for (const [href, until] of Object.entries(raw)) {
    if (typeof until === "number" && Number.isFinite(until) && now < until) out[href] = until;
  }
  return out;
}

function write(key: string, value: unknown, storage?: StoreLike): void {
  try {
    store(storage)?.setItem(key, JSON.stringify(value));
  } catch {
    /* storage off or full: the counter forgets */
  }
}

/**
 * Count one open. `pathnameOrHref` may be a route such as `/tasks/123`: it
 * maps to its pane `/tasks`. A route outside the starrable panes, such as an
 * install route, is ignored. Old times and the oldest times over the cap
 * are dropped.
 */
export function recordOpen(email: string | null, pathnameOrHref: string, now: number, storage?: StoreLike): void {
  const href = paneHrefOf(pathnameOrHref);
  if (!href) return;
  const from = now - SUGGEST_WINDOW_DAYS * DAY_MS;
  const all = readOpens(email, storage);
  const next: Record<string, number[]> = {};
  for (const [h, times] of Object.entries(all)) {
    const kept = times.filter((t) => t > from);
    if (kept.length) next[h] = kept;
  }
  next[href] = [...(next[href] ?? []), now];
  // Over the cap, drop the oldest time of any pane.
  const flat = Object.entries(next).flatMap(([h, ts]) => ts.map((t) => [h, t] as const));
  if (flat.length > OPENS_CAP) {
    flat.sort((a, b) => a[1] - b[1]);
    const keep = flat.slice(flat.length - OPENS_CAP);
    for (const h of Object.keys(next)) next[h] = [];
    for (const [h, t] of keep) next[h].push(t);
    for (const h of Object.keys(next)) if (!next[h].length) delete next[h];
  }
  write(opensKey(email), next, storage);
}

/** A No: hide the suggestion for this pane for 30 days. */
export function hideSuggestion(email: string | null, href: string, now: number, storage?: StoreLike): void {
  if (!paneHrefOf(href)) return;
  const next = { ...readHidden(email, now, storage), [href]: now + SUGGEST_HIDE_DAYS * DAY_MS };
  write(hiddenKey(email), next, storage);
}

/**
 * Forget the open times and the hides of each account in `emails`. A sign-out
 * calls it, so no address of a signed-out account stays in a key name. It
 * follows `clearAppearanceForAccounts` in `lib/theme/scope.ts`.
 */
export function clearPinSuggestForAccounts(
  emails: readonly string[],
  storage?: Pick<Storage, "removeItem">,
): void {
  const s = storage ?? (typeof window === "undefined" ? null : window.localStorage);
  if (!s) return;
  for (const e of new Set(emails.map((x) => x.trim().toLowerCase()).filter(Boolean))) {
    try {
      s.removeItem(opensKey(e));
      s.removeItem(hiddenKey(e));
    } catch {
      /* storage unavailable: nothing is stored */
    }
  }
}
