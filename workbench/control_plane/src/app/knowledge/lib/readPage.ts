// ── The one read of a page for the cache (WS-61) ────────────────────────────
//
// `pageKey(id)` holds a `PageRead<PageDetail>`. The page view and the hover
// preview of a page chip (KB-2k2) both fill that key, so they must write the
// same shape. This is the one fetcher for it.

import { getPage } from "./api";
import { pageRefusal, type PageRead } from "./phase";
import type { PageDetail } from "./types";

/** A page, or "not found" for a 404. Any other failure throws. */
export async function readPage(id: string): Promise<PageRead<PageDetail>> {
  try {
    return { kind: "page", page: await getPage(id) };
  } catch (err) {
    const refusal = pageRefusal<PageDetail>(err);
    if (refusal) return refusal;
    throw err;
  }
}
