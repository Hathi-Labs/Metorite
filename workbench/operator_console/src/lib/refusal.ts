// Reading a refusal row out of the activity trail.
//
// 🔴 **The trail recorded successes only, until 2026-09-22.** `_audit` runs
// after a write lands, so a refused write left nothing at all. Measured on
// production: `control_audit` held no `catalog.binding` row ever, meaning not
// one tier save had succeeded — while the owner had been trying for days and
// reported it as *"when I delete or change a particular model it does not
// change it"*. The console could not answer "why did nothing happen", which
// is the only question anybody asks an audit trail under pressure.
//
// The Console now records `action: "refused"` with `{method, path, status,
// why}`. This turns that into the sentence the reader needs.

/** What the Console puts in a refusal row's `detail`. Every field is optional
 *  at the reader: the row is written by a server that may be older than this
 *  code, and a blank cell beats a crash on the one page you open when
 *  something is already wrong. */
export type RefusalDetail = {
  method?: unknown;
  path?: unknown;
  status?: unknown;
  why?: unknown;
};

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

/** One line saying what was refused and why.
 *
 * ⚠️ **Never throws, and never returns an empty string.** This renders inside
 * a table on the page an operator opens when something is already broken. A
 * thrown error there replaces the evidence with a blank screen, and an empty
 * cell reads as "nothing happened" — the exact wrong conclusion.
 */
export function refusalTitle(detail: unknown): string {
  if (typeof detail !== "object" || detail === null) {
    return "A write was refused. The reason was not recorded.";
  }
  const d = detail as RefusalDetail;
  const method = str(d.method);
  const path = str(d.path);
  const why = str(d.why);
  const status =
    typeof d.status === "number" && Number.isFinite(d.status)
      ? String(d.status)
      : str(d.status);

  const act = method && path ? `${method} ${path}` : path ?? "A write";
  const code = status ? ` answered ${status}` : " was refused";
  // ⚠️ The `why` is the Console's OWN message, never the caller's input — the
  // handler records no request body, because a refusal is exactly when
  // somebody retries with a credential in hand.
  return why ? `${act}${code}: ${why}` : `${act}${code}.`;
}
