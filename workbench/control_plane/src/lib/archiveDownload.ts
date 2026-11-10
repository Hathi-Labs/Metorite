/**
 * The download of a zip that `package_artifacts` made (2026-10-11).
 *
 * A zip is a derived file, so the gateway keeps it on disk only. After a
 * disk wipe the gateway answers its download with 410 Gone and
 * `PACKAGE_EXPIRED`. A plain `<a download>` would show the member only a
 * failed download, so the card fetches the zip first and shows this text
 * when it expired. `archiveDownload.test.ts` reads the gateway's constant and
 * fails if the two texts differ.
 */

/** The text of `gateway.routes.workspace.PACKAGE_EXPIRED`, word for word. */
export const PACKAGE_EXPIRED =
  "This package expired. Ask the assistant to zip the files again.";

/** The text when the download fails for another reason. */
export const DOWNLOAD_FAILED = "The download failed. Try again.";

export type ArchiveFetch =
  | { kind: "ok"; blob: Blob }
  | { kind: "expired" }
  | { kind: "error"; status: number };

/** Fetch a zip, and say whether it downloaded, expired or failed. */
export async function fetchArchive(
  url: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ArchiveFetch> {
  let res: Response;
  try {
    res = await fetchImpl(url);
  } catch {
    return { kind: "error", status: 0 };
  }
  if (res.ok) return { kind: "ok", blob: await res.blob() };
  if (res.status === 410) return { kind: "expired" };
  return { kind: "error", status: res.status };
}

/** The text a card shows for a fetch that did not download, or null. */
export function archiveNotice(result: ArchiveFetch): string | null {
  if (result.kind === "expired") return PACKAGE_EXPIRED;
  if (result.kind === "error") return DOWNLOAD_FAILED;
  return null;
}

/** Hand a fetched blob to the browser as a download named *name*. */
export function saveBlob(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 0);
}
