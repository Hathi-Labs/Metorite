/**
 * The CSV download seam — one per app would be one too many.
 *
 * Two apps export a filtered list today (Projects, WS-27ae; the CRM,
 * WS-26i-export) and both need exactly the same two things once the fetch has
 * come back: the name the SERVER chose, and the bytes handed to the browser
 * without being decoded on the way. Both are small, both are subtle, and both
 * were written first inside `app/projects/lib/export.ts` — this is that pair
 * promoted, not copied (CLAUDE.md §4: a second implementation of an existing
 * seam is a defect, not a feature).
 *
 * What is NOT here: the query builders. `exportQuery`/`exportPath` are shaped
 * by each app's own filter vocabulary and belong beside it.
 *
 * ⚠️ **The export is fetched, not navigated to.** Pointing `window.location` at
 * an export endpoint would download the file, but both servers REFUSE a filter
 * wider than their row cap (422 rather than a truncated file), and a navigation
 * turns that refusal into a tab full of JSON. Fetching it means the refusal
 * arrives as a message the app can show — which is the entire point of refusing
 * rather than truncating.
 */

/**
 * The filename the SERVER chose, off `Content-Disposition`.
 *
 * Read back rather than composed here so there is one of it. A client-side
 * name would be a second opinion about what the file is called, and the two
 * would drift the first time either side gained a project slug or an entity
 * name.
 *
 * `fallback` is required rather than defaulted: a default here would be one
 * app's filename living in a shared module, silently naming the other app's
 * downloads after it the day a proxy dropped the header.
 */
export function filenameFromDisposition(
  header: string | null,
  fallback: string
): string {
  const quoted = /filename="([^"]+)"/.exec(header ?? "");
  if (quoted) return quoted[1];
  const bare = /filename=([^;]+)/.exec(header ?? "");
  return bare ? bare[1].trim() : fallback;
}

/**
 * Hand a fetched CSV to the browser as a download.
 *
 * ⚠️ **It takes a `Blob`, and that is not a detail.** The obvious version reads
 * `await res.text()` and wraps the string — and `Response.text()` decodes UTF-8
 * with `TextDecoder`, which **strips a leading byte order mark**. The gateway
 * emits that BOM for exactly one reason: without it Excel reads the file as the
 * system code page and every non-ASCII name arrives mojibake. So the text path
 * silently undoes the server's fix, and the file saved from a running browser
 * is measurably different from the bytes the endpoint produced.
 *
 * ⚠️ **The rule binds at EVERY hop, not just this one.** Keeping a `Blob` here
 * bought nothing for the year both BFF proxies did `await res.text()` and
 * rebuilt the response from the decoded string: the bytes reaching this
 * function had already lost their BOM, so a correct client sat downstream of a
 * lossy relay. Measured on node v22 — upstream `EF BB BF 4E 61 6D`, relayed
 * `4E 61 6D 65`. Both proxies now read `res.arrayBuffer()`, and
 * `src/lib/export.test.ts` RUNS each of them over a BOM'd body and compares
 * bytes; anything new that fronts an export joins that list.
 *
 * ⚠️ This function itself is untested by construction: `vitest.config.ts` runs
 * `environment: "node"` with `include: ["src/**\/*.test.ts"]`, so there is no
 * `document` here and adding jsdom to cover six lines of DOM plumbing is a
 * larger change than the thing covered. Everything with a decision in it — the
 * query, the filename, the refusal path — is a pure function and IS tested; the
 * BOM is pinned at the gateway (`test_projects_export`, `test_crm_export`) and
 * through the proxies (`export.test.ts`), which are the two places it was lost.
 */
export function saveCsv(body: Blob, filename: string): void {
  saveBlob(body, filename);
}

/**
 * Hand any fetched file to the browser as a download. `saveCsv` is this with
 * a CSV's name. WS-27bm S8 added the second use: a report saved as Markdown or
 * as a PDF (`app/projects/lib/reportFiles.ts`). The `Blob` rule above binds
 * here too, and a PDF is the plainest case: a decode would break every byte.
 */
export function saveBlob(body: Blob, filename: string): void {
  const url = URL.createObjectURL(body);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
