// Reading the minted organization key out of a provision response.
//
// 🔴 **A customer with no `cc_live_` key cannot be served AT ALL.** Their
// deployment presents it to the Router on every AI call. `hathi-labs-llp` was
// provisioned and ran for weeks with zero keys, because minting one was a
// separate thing an operator had to remember. Measured 2026-09-21.
//
// `POST /orgs/provision` now mints it on the operator arm, so the key arrives
// in the create response — **once**. Only its hash is stored, so if this
// screen does not show it, it is gone.

/** The half of a provision response this module cares about. */
export type MintedKey = { prefix: string; token: string };

/** Pull the minted key out of a provision response body.
 *
 * ⚠️ **Returns null rather than throwing, on every unhappy shape** — bad JSON,
 * no `key` at all, a `key` missing either half. The caller renders a success
 * panel around this, and an exception there would replace a created customer
 * with a blank screen.
 *
 * ⚠️ **A missing `key` is NORMAL and is not an error.** A re-provision of an
 * organization that already holds a live key answers without one, on purpose:
 * that guard is what stops a retried form issuing a pile of credentials. So
 * the caller shows the key panel when this returns a key, and says nothing at
 * all when it does not.
 *
 * ⚠️ Both halves are required. A `token` with no `prefix` cannot be named in
 * the key list, the revoke call or the audit trail, so half a key is treated
 * as no key rather than shown as one.
 */
export function mintedKeyFrom(text: string): MintedKey | null {
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null) return null;
  const key = (body as { key?: unknown }).key;
  if (typeof key !== "object" || key === null) return null;
  const { prefix, token } = key as { prefix?: unknown; token?: unknown };
  if (typeof prefix !== "string" || prefix.length === 0) return null;
  if (typeof token !== "string" || token.length === 0) return null;
  return { prefix, token };
}
