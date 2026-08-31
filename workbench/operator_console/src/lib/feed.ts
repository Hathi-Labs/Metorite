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
import { fixedDecimal } from "./pricing";
import type { Tone } from "./tone";

/** Feed rows keyed by model id, for O(1) lookup per card. */
export function feedById(feed: VendorFeed): Map<string, FeedModel> {
  const m = new Map<string, FeedModel>();
  for (const r of [...feed.rows, ...feed.available]) m.set(r.id, r);
  return m;
}

export type Drift = {
  /** Operator words: "per 1M in", "per 1M out", "per 1M cached in", plus the
   *  three per-unit labels "per minute", "per character" and "per image".
   *
   * ⚠️ **Each per-unit label NAMES ITS UNIT, and that is load-bearing**
   *  (H-78). litellm prices transcription per second and we price it per
   *  minute. A drift row that reads "$0.006 against $0.0001" with no unit
   *  invites the very mistake this feature exists to prevent. */
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
 *  strings, never a reformatted float.
 *
 * 🔴 **Both sides speak the PROFILE's unit, and NOTHING here converts** (H-78).
 *  The Console's feed read already multiplied the per-second transcription
 *  price by 60, so `f.perMinuteUsd` and `m.perMinuteUsd` are the same kind of
 *  number and a direct compare is the correct compare. */
export function driftFor(m: CatalogModel, f: FeedModel | undefined): Drift[] {
  if (!f) return [];
  //  Each pair carries the RULE it is compared under, because the two kinds
  //  of price live at different magnitudes.
  const pairs: [string, number | null, string | null, "abs" | "rel"][] = [
    // Per-MILLION-token prices are dollar-scale. 1e-9 is far below the
    // NUMERIC(12,4) the profile stores, so absolute is right and unchanged.
    ["per 1M in", m.inputPer1M, f.inputPer1M, "abs"],
    ["per 1M out", m.outputPer1M, f.outputPer1M, "abs"],
    ["per 1M cached in", m.cachedInputPer1M, f.cachedInputPer1M, "abs"],
    // 🔴 Per-UNIT prices are not. These columns are NUMERIC(18,10) exactly
    // so a tiny price fits, and 019's own header cites 0.000015 as a real
    // one. Under the absolute rule a vendor DOUBLING 3e-10 to 6e-10 reports
    // no drift at all, because the gap is smaller than the epsilon. So they
    // compare RELATIVELY: a pair differing by more than one part in a
    // million drifts, at any magnitude.
    ["per minute", m.perMinuteUsd, f.perMinuteUsd, "rel"],
    ["per character", m.perCharacterUsd, f.perCharacterUsd, "rel"],
    ["per image", m.perImageUsd, f.perImageUsd, "rel"],
  ];
  const out: Drift[] = [];
  for (const [label, ours, upstream, rule] of pairs) {
    if (ours === null || upstream === null) continue;
    const up = Number(upstream);
    if (!Number.isFinite(up)) continue;
    const gap = Math.abs(ours - up);
    // Two zeros are equal, and a relative test on them divides by zero.
    const scale = Math.max(Math.abs(ours), Math.abs(up));
    const drifted =
      rule === "abs" ? gap > 1e-9 : scale > 0 && gap / scale > 1e-6;
    if (drifted) {
      // ⚠️ `String(ours)` rendered a per-unit price as "3e-10" in the drift
      // sentence. `upstream` is the wire's own fixed-point string already.
      out.push({ label, ours: fixedDecimal(ours), upstream });
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
  // The three per-unit costs (H-78). A transcribe or image model has no
  // token price at all, so without these its hint always read "0 boxes"
  // while upstream held the one number the board needs.
  if (m.perMinuteUsd === null && f.perMinuteUsd !== null) n++;
  if (m.perCharacterUsd === null && f.perCharacterUsd !== null) n++;
  if (m.perImageUsd === null && f.perImageUsd !== null) n++;
  return n;
}

/** The values "Copy the vendor's facts" writes into the form boxes.
 *  Strings because that is what the inputs hold — empty means unknown.
 *
 * ⚠️ **Every value is copied, and none is computed** (H-78). `vmin` holds a
 *  per-MINUTE price because the Console served one. Multiplying here would
 *  be a float multiply, and a float rewrites the number it copies. */
export function prefillFrom(f: FeedModel): {
  ctx: string; out: string; vin: string; vout: string; vcached: string;
  vmin: string; vchar: string; vimg: string;
  readsImages: boolean; thinksFirst: boolean;
} {
  return {
    ctx: f.contextWindow?.toString() ?? "",
    out: f.maxOutput?.toString() ?? "",
    vin: f.inputPer1M ?? "",
    vout: f.outputPer1M ?? "",
    vcached: f.cachedInputPer1M ?? "",
    vmin: f.perMinuteUsd ?? "",
    vchar: f.perCharacterUsd ?? "",
    vimg: f.perImageUsd ?? "",
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
      // The three per-unit costs (H-78), also verbatim. The wire name and
      // the profile column agree, so this is a copy and not a rename.
      vendor_per_minute_usd: f.perMinuteUsd,
      vendor_per_character_usd: f.perCharacterUsd,
      vendor_per_image_usd: f.perImageUsd,
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
