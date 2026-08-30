// The vendor feed's judgements — drift, freshness, and the copy bodies.
//
// ⚠️ Every judgement the feed UI draws is HERE, never inline in JSX. This
// app's suite carries no React renderer, so logic in a component is untested
// by construction — `feed.test.ts` is the fence for all of it.
//
// 🔴 **The one rule that matters: the feed never writes anything by itself.**
// Billing cost reads `model_profile`; the feed is upstream's claim. These
// helpers therefore produce COMPARISONS (drift) and PREFILL VALUES (copy) —
// the write is always the operator pressing the same Save that always
// existed, through the same `/catalog/profiles` seam.

import type { CatalogModel, FeedModel, VendorFeed } from "./contract";
import type { Tone } from "./tone";

/** Feed rows keyed by model id, for O(1) lookup per card. */
export function feedById(feed: VendorFeed): Map<string, FeedModel> {
  const m = new Map<string, FeedModel>();
  for (const r of [...feed.rows, ...feed.available]) m.set(r.id, r);
  return m;
}

export type Drift = {
  /** Operator words: "per 1M in", "per 1M out", "per 1M cached in". */
  label: string;
  /** What OUR profile says — the number billing cost is computed from. */
  ours: string;
  /** What upstream says now. */
  upstream: string;
};

/** Where the vendor's published price disagrees with the profile somebody
 *  typed. Only fields BOTH sides know can drift — a null on either side is
 *  "unknown", and unknown cannot disagree with anything.
 *
 * ⚠️ `Number()` here only to COMPARE (the strings differ in trailing zeros:
 *  profile "0.2800" vs feed "0.280000"). What renders is the original
 *  strings, never a reformatted float. */
export function driftFor(m: CatalogModel, f: FeedModel | undefined): Drift[] {
  if (!f) return [];
  const pairs: [string, number | null, string | null][] = [
    ["per 1M in", m.inputPer1M, f.inputPer1M],
    ["per 1M out", m.outputPer1M, f.outputPer1M],
    ["per 1M cached in", m.cachedInputPer1M, f.cachedInputPer1M],
  ];
  const out: Drift[] = [];
  for (const [label, ours, upstream] of pairs) {
    if (ours === null || upstream === null) continue;
    const up = Number(upstream);
    if (!Number.isFinite(up)) continue;
    if (Math.abs(ours - up) > 1e-9) {
      out.push({ label, ours: String(ours), upstream });
    }
  }
  return out;
}

/** How many facts upstream knows that the profile still shows a dash for.
 *  Drives the "the feed can fill N boxes" hint on an undertyped profile. */
export function fillCount(m: CatalogModel, f: FeedModel | undefined): number {
  if (!f) return 0;
  let n = 0;
  if (m.inputPer1M === null && f.inputPer1M !== null) n++;
  if (m.outputPer1M === null && f.outputPer1M !== null) n++;
  if (m.cachedInputPer1M === null && f.cachedInputPer1M !== null) n++;
  if (m.contextWindow === null && f.contextWindow !== null) n++;
  if (m.maxOutput === null && f.maxOutput !== null) n++;
  return n;
}

/** The values "Copy the vendor's facts" writes into the form boxes.
 *  Strings because that is what the inputs hold — empty means unknown. */
export function prefillFrom(f: FeedModel): {
  ctx: string; out: string; vin: string; vout: string; vcached: string;
  readsImages: boolean; thinksFirst: boolean;
} {
  return {
    ctx: f.contextWindow?.toString() ?? "",
    out: f.maxOutput?.toString() ?? "",
    vin: f.inputPer1M ?? "",
    vout: f.outputPer1M ?? "",
    vcached: f.cachedInputPer1M ?? "",
    readsImages: f.readsImages,
    thinksFirst: f.thinksFirst,
  };
}

/** The POST bodies that make an AVAILABLE model a declared one, in order:
 *  capability first (the Router's permission to route), then the profile
 *  (facts). `capability` is null when the feed's mode has no task — a model
 *  we cannot serve is shown, never declarable.
 *
 * ⚠️ `streams` mirrors the Console's STREAMABLE_TASKS (`catalog.py`) — chat
 *  and speak stream, nothing else does. `feed.test.ts` pins the pair. */
export function declareBodies(f: FeedModel): {
  capability: Record<string, unknown> | null;
  profile: Record<string, unknown>;
} {
  const capability =
    f.task && f.invocation
      ? {
          model: f.id,
          task: f.task,
          invocation: f.invocation,
          streams: f.task === "chat" || f.task === "speak",
        }
      : null;
  return {
    capability,
    profile: {
      model: f.id,
      label: null,
      context_window: f.contextWindow,
      max_output: f.maxOutput,
      // The STRINGS, verbatim — pydantic parses them into exact Decimals.
      vendor_input_per_1m_usd: f.inputPer1M,
      vendor_output_per_1m_usd: f.outputPer1M,
      vendor_cached_input_per_1m_usd: f.cachedInputPer1M,
      description: "",
      reads_images: f.readsImages,
      thinks_first: f.thinksFirst,
    },
  };
}

/** One line under the Models heading: how current the facts are, provably.
 *
 * ⚠️ "Never fetched" is `warn`, not `danger` — the console works without the
 *  feed, it just makes the operator type. Staleness past a week warns too:
 *  litellm updates near-daily, so a week-old sync means the button (or the
 *  autosync flag) stopped being pressed. */
export function freshness(
  feed: VendorFeed,
  now: Date,
): { label: string; tone: Tone } {
  if (!feed.syncedAt) {
    return {
      label:
        "Vendor facts have never been fetched. Every price and window on " +
        "this page was typed by hand.",
      tone: "warn",
    };
  }
  const synced = new Date(feed.syncedAt);
  const days = Math.floor((now.getTime() - synced.getTime()) / 86_400_000);
  const from =
    feed.source === "packaged:litellm"
      ? "the offline litellm snapshot"
      : "the live litellm feed";
  const when =
    days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
  if (days > 7) {
    return {
      label:
        `Vendor facts are ${days} days old (${feed.models} models, ${from}). ` +
        "Upstream prices may have moved — fetch again.",
      tone: "warn",
    };
  }
  return {
    label: `${feed.models} models fetched ${when} from ${from}.`,
    tone: "ok",
  };
}

/** Group the available list by vendor, filtered by the search box. */
export function availableByVendor(
  feed: VendorFeed,
  query: string,
): Map<string, FeedModel[]> {
  const q = query.trim().toLowerCase();
  const out = new Map<string, FeedModel[]>();
  for (const r of feed.available) {
    if (q && !r.id.toLowerCase().includes(q) && !r.mode.toLowerCase().includes(q)) {
      continue;
    }
    if (!out.has(r.provider)) out.set(r.provider, []);
    out.get(r.provider)?.push(r);
  }
  return out;
}
