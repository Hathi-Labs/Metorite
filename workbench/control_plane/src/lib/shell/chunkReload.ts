/**
 * A tab that was open across a deploy, asking for code that no longer exists.
 *
 * Each build of the workbench names its script files by hash. A tab opened
 * before a deploy still holds the OLD page, and the first time it navigates
 * to a part it has not loaded yet, it asks for an old file name. The new
 * server has no such file, the import fails, and React shows its raw
 * "Application error" screen. That reads as "Metorite is broken". It is
 * "Metorite was updated, and this tab is out of date".
 *
 * The cure is one reload, which fetches the new page. The guard is that it
 * runs at most once a minute per tab, so a real bug that throws the same
 * error can never become a reload loop. Spec: `navigation_shell.md` §7.3.
 */

const KEY = "metorite:chunk-reload-at";
export const RELOAD_GUARD_MS = 60_000;

/** The messages each browser and bundler gives a failed code import. */
const CHUNK_MESSAGES = [
  /Loading (?:CSS )?chunk [\w./-]+ failed/i,
  /Failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /Importing a module script failed/i,
  /Failed to load chunk/i,
];

export function isChunkLoadError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as { name?: unknown; message?: unknown };
  if (e.name === "ChunkLoadError") return true;
  const msg = typeof e.message === "string" ? e.message : "";
  return CHUNK_MESSAGES.some((re) => re.test(msg));
}

type Store = Pick<Storage, "getItem" | "setItem">;

/**
 * May this tab reload itself now? Records the reload when it says yes.
 *
 * Storage can be missing or can throw (a private window, blocked site data).
 * Then the answer is no, because without the record nothing stops a loop.
 */
export function claimAutoReload(store: Store | null | undefined, now: number): boolean {
  if (!store) return false;
  try {
    const last = Number(store.getItem(KEY) ?? 0);
    if (Number.isFinite(last) && now - last < RELOAD_GUARD_MS) return false;
    store.setItem(KEY, String(now));
    return true;
  } catch {
    return false;
  }
}

/** The session store, or null where reading it throws. */
export function sessionStore(): Store | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}
