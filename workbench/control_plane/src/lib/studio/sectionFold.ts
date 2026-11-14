/**
 * The folded state of a page section (STU-AF7). Pure: it takes a `Storage` and never reads `window`.
 *
 * A viewer folds a section and it stays folded after a reload. The state is a per-viewer convenience,
 * so it lives in `localStorage` and not in the manifest. Each call guards the storage, because a
 * private window, a full disk or a blocked cookie setting makes it throw. With no storage a
 * section is open and the page works the same. The key holds the app, the page and the section.
 */

const PREFIX = "studio.fold.v1";

/** The storage key of one section. A part cannot hold the separator, so two sections never share a key. */
export function foldKey(scope: string, pageId: string, sectionId: string): string {
  return [PREFIX, scope, pageId, sectionId].map((p, i) => (i === 0 ? p : encodeURIComponent(p))).join(":");
}

/** The browser storage, or `null` when it is missing or blocked. */
export function viewerStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** True when the viewer folded the section. A missing key, a bad value and a throw all give false. */
export function readFolded(storage: Pick<Storage, "getItem"> | null, key: string): boolean {
  try {
    return storage?.getItem(key) === "1";
  } catch {
    return false;
  }
}

/** Keep the folded state. An open section drops its key, so the storage holds only what differs. */
export function writeFolded(storage: Pick<Storage, "setItem" | "removeItem"> | null, key: string, folded: boolean): void {
  try {
    if (folded) storage?.setItem(key, "1");
    else storage?.removeItem(key);
  } catch {
    // A full or blocked storage: the section still folds on this page, and the next load opens it.
  }
}
