"use client";

// The feed's one-line status and its button — WS-31, migration 014.
//
// 🔴 **Its own file so `ModelBrowser` stays free of `fetch(`** — same split
// as `ModelDetails`. The strip WRITES (a sync is a write to the feed cache),
// so it lives client-side against our own BFF route.
//
// ⚠️ The fetch fires on CLICK only, never on mount — `feed.test.ts` fences
// that. An auto-fetching strip would make page views write to the database.

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { VendorFeed } from "@/lib/contract";
import { freshness } from "@/lib/feed";
import { HELP_FEED } from "@/lib/help";
import { LIST } from "@/lib/words";
import { chipClass } from "@/lib/tone";

export default function FeedStrip({ feed }: { feed: VendorFeed }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const state = freshness(feed, new Date());
  // 🔴 **How many models you could add without typing anything.** The
  // "available from your vendors" list can sit thousands of pixels down, under
  // the declared catalog, and an operator who never scrolls there never learns
  // the one-click path exists. Measured 2026-09-19: it began at 5474px on a
  // 13257px page. This count and its jump are the cheap half of that fix.
  const available = feed.available.length;

  async function sync() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch("/api/operator/catalog/feed", { method: "POST" });
      if (!res.ok) {
        setErr(`The Console refused: ${await res.text()}`);
        return;
      }
      router.refresh();
    } catch {
      setErr("The Console did not answer. The feed did not refresh — check the network and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="feedstrip">
      <span className={chipClass(state.tone)} title={HELP_FEED.freshness}>
        {state.label}
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={sync}
        title={HELP_FEED.fetch}
      >
        {busy ? LIST.fetchBusy : LIST.fetchFeed}
      </button>
      {available > 0 && (
        <a className="linklike" href="#available" title={HELP_FEED.readyToAdd}>
          {LIST.readyToAdd(available)}
        </a>
      )}
      <span className="muted small">
        Prices and limits come from litellm&apos;s maintained price map — the
        same ids the Router calls. Fetching changes no price a customer pays.
      </span>
      {err && <p className="result err">{err}</p>}
    </div>
  );
}
