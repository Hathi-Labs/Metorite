"use client";

// "N models are costs blind, and the feed can price them" — one click for all.
//
// 🔴 **The setup labour this page kept asking for.** Measured against the live
// feed on 2026-09-19: 31 of 43 declared models read "costs blind" while
// `vendor_price_feed` held a price for most of them. Each one was a card, a
// form, fifteen boxes and a save. The numbers were already in the database,
// under the same id the Router routes on.
//
// ⚠️ **It ADDS a price and never replaces one.** `blindButFillable` selects
// only `costblind` models — declared, callable, unpriced. A model somebody has
// already costed is not in the list and cannot be touched from here. A bulk
// overwrite with no diff shown is how a deliberate correction silently
// reverts, and it would be unreviewable across thirty models at once.
//
// ⚠️ **The same route and body as one card**, built by `declareBodies`. A bulk
// path that posted its own shape would grow its own rules about what a blank
// means, and the two would disagree within a month.
//
// ⚠️ **One request at a time, and it reports what actually happened.** A
// Promise.all over thirty writes hides which one failed, and the Console would
// see thirty concurrent writes from one click. The count below is what the
// Console confirmed, not what was attempted.

import { useState } from "react";
import { useRouter } from "next/navigation";

import type { CatalogModel, VendorFeed } from "@/lib/contract";
import { declareBodies, feedById } from "@/lib/feed";

export default function FillAllBlind({
  blind,
  feed,
}: {
  /** Declared, unpriced, and the feed can price them. */
  blind: CatalogModel[];
  feed: VendorFeed;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ ok: number; failed: string[] } | null>(
    null,
  );

  if (blind.length === 0) return null;

  async function fillAll() {
    setBusy(true);
    setDone(null);
    const byId = feedById(feed);
    let ok = 0;
    const failed: string[] = [];
    for (const m of blind) {
      const row = byId.get(m.id);
      if (!row) {
        failed.push(m.id);
        continue;
      }
      try {
        const res = await fetch("/api/operator/catalog/profiles", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(declareBodies(row).profile),
        });
        if (res.ok) ok++;
        else failed.push(m.id);
      } catch {
        failed.push(m.id);
      }
    }
    setDone({ ok, failed });
    setBusy(false);
    // ⚠️ Refresh even on a partial failure. The models that DID save are
    // costed now, and a page still showing them blind invites somebody to do
    // the work twice.
    router.refresh();
  }

  return (
    <div className="banner info" role="status">
      <strong>
        {blind.length} {blind.length === 1 ? "model is" : "models are"} costs
        blind
      </strong>{" "}
      — and the vendor feed already holds a price for{" "}
      {blind.length === 1 ? "it" : "every one of them"}. Until a model carries a
      price, its calls cannot be costed and its margin reads as unknown.
      <div className="rowline" style={{ marginTop: 10 }}>
        <button type="button" className="primary" disabled={busy} onClick={fillAll}>
          {busy
            ? `Filling ${blind.length}…`
            : `Fill ${blind.length === 1 ? "it" : `all ${blind.length}`} from the feed`}
        </button>
        {done && (
          <span className={done.failed.length === 0 ? "ok-t" : "warn-t"}>
            {done.failed.length === 0
              ? `Costed ${done.ok} from the feed.`
              : `Costed ${done.ok}. ${done.failed.length} refused — ${done.failed
                  .slice(0, 3)
                  .join(", ")}${done.failed.length > 3 ? "…" : ""}. Open those cards to see why.`}
          </span>
        )}
      </div>
      <p className="muted small" style={{ marginTop: 8 }}>
        This only adds a price where there is none. A model you have already
        costed is untouched — edit those on the card, where the feed&apos;s
        number is shown beside yours.
      </p>
    </div>
  );
}
