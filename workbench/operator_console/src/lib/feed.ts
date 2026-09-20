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
import { statusOf } from "./modelSearch";
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

/** Can this declared model be costed straight from the feed, with no typing?
 *
 * 🔴 **The question the Models page never asked.** A declared model with no
 * profile draws "costs blind" and offers a fifteen-box form. For most of them
 * the answer already sits in `vendor_price_feed`, under the SAME id the Router
 * routes on. Measured 2026-09-19: `deepseek/deepseek-chat` read costs-blind
 * while the feed held 0.28 in, 0.42 out and a 131072 window. Asking somebody
 * to type a number we already hold is the whole complaint.
 *
 * ⚠️ **TRUE only when the feed can actually answer.** A row carrying no usable
 * price fills nothing, and an offer that leaves the model still costs-blind is
 * worse than no offer: it spends a click and teaches that the button does not
 * work. `groq/whisper-large-v3-turbo` is the live example — declared, in the
 * feed, and priced by nobody.
 *
 * ⚠️ **Token OR per-unit, because a transcribe model has no token price.**
 * Either kind makes the call costable, so either is enough.
 */
export function canFillFromFeed(f: FeedModel | undefined): boolean {
  if (!f) return false;
  const usable = (v: string | null) =>
    v !== null && v.trim() !== "" && Number(v) > 0;
  return (
    usable(f.inputPer1M) ||
    usable(f.outputPer1M) ||
    usable(f.perMinuteUsd) ||
    usable(f.perCharacterUsd) ||
    usable(f.perImageUsd)
  );
}

/** What the feed says this model costs, in whatever unit it is sold by.
 *
 * 🔴 **The price column said "—" about models it HAD a price for.** It read
 * only the two token rates, so every per-unit model drew a dash — and a dash
 * means "we do not know". Measured 2026-09-19: `groq/whisper-large-v3` carries
 * a per-second rate and `groq/canopylabs/orpheus-v1-english` a per-character
 * one, and both showed as unpriced beside an Add button.
 *
 * ⚠️ **Returns `null` only when the feed truly knows nothing.** That is the
 * case worth a warning, and it is the same judgement `canFillFromFeed` makes,
 * so the dash and the warning can never disagree.
 *
 * ⚠️ **The unit is NAMED.** "$0.0000220" beside a token price is meaningless
 * without "per character" — three orders of magnitude separate them.
 */
export function feedPriceLabel(f: FeedModel): string | null {
  const num = (v: string | null) =>
    v !== null && v.trim() !== "" && Number(v) > 0 ? Number(v) : null;

  const inTok = num(f.inputPer1M);
  const outTok = num(f.outputPer1M);
  if (inTok !== null || outTok !== null) {
    const a = inTok === null ? "—" : `$${fixedDecimal(inTok)}`;
    const b = outTok === null ? "—" : `$${fixedDecimal(outTok)}`;
    return `${a} in / ${b} out`;
  }

  const perMin = num(f.perMinuteUsd);
  if (perMin !== null) return `$${fixedDecimal(perMin)} per minute`;
  const perChar = num(f.perCharacterUsd);
  if (perChar !== null) return `$${fixedDecimal(perChar)} per character`;
  const perImg = num(f.perImageUsd);
  if (perImg !== null) return `$${fixedDecimal(perImg)} per image`;

  return null;
}

/** Every declared model the feed could cost right now, and nobody has.
 *
 * 🔴 **The bulk of the setup work, and it is all copying.** Measured against
 * the live feed on 2026-09-19: 31 of 43 declared models read "costs blind"
 * while `vendor_price_feed` held a price for most of them. Filling those one
 * card at a time is the manual labour this page keeps asking for.
 *
 * ⚠️ **Built on `statusOf`, never a second rule.** The badge on the card and
 * the list behind the button must agree, or the count offers work the page
 * does not show. `costblind` already means declared, callable, and unpriced —
 * so a model with no vendor key is correctly NOT here: its problem is the key,
 * and a price would not fix it.
 *
 * ⚠️ **Only where the feed can ANSWER** (`canFillFromFeed`). A count that
 * includes models the feed cannot price promises work the click will not do.
 */
export function blindButFillable(
  models: CatalogModel[],
  feed: VendorFeed,
  armed: string[],
): CatalogModel[] {
  const byId = feedById(feed);
  return models.filter(
    (m) => statusOf(m, armed) === "costblind" && canFillFromFeed(byId.get(m.id)),
  );
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

// ── Picking a handful of models out of thousands (2026-09-21) ──────────────
//
// 🔴 **Why the selection lives here and not in the component.** This app's
// suite carries no React renderer, so anything written in JSX is untested by
// construction. The rules below have right and wrong answers — which rows a
// "select all" may tick, what a tick does to an id already ticked — so they
// are pure functions with `feed.test.ts` behind them, exactly like
// `declareBodies` and `blindButFillable` above.

/** Every offered model the Router could actually serve.
 *
 * ⚠️ **`task` is the test, not `mode`.** litellm offers modes we have no verb
 * for (`rerank`, `moderation`); the Console leaves `task` null for those, and
 * declaring one would create a row nothing can call. A bulk add that included
 * them would fail on exactly the rows nobody chose deliberately. */
export function servableFeedModels(feed: VendorFeed): FeedModel[] {
  return feed.available.filter((f) => f.task !== null && f.task !== "");
}

/** The ids of `rows` that a "select all" may tick — the servable ones. */
export function selectableIds(rows: FeedModel[]): string[] {
  return rows.filter((f) => f.task !== null && f.task !== "").map((f) => f.id);
}

/** A new set with `id` added or removed. Never mutates: React compares the
 *  reference, and a mutated Set re-renders nothing. */
export function togglePick(picked: Set<string>, id: string): Set<string> {
  const next = new Set(picked);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** A new set with `id` gone. */
export function without(picked: Set<string>, id: string): Set<string> {
  const next = new Set(picked);
  next.delete(id);
  return next;
}

/** Tick every id in `ids`, or untick them all when they are already ticked.
 *
 * ⚠️ **"All shown", never "all offered".** The header checkbox acts on the
 * rows on screen, so narrowing the search first is how an operator picks a
 * subset — and a click can never tick nine hundred models they cannot see. */
export function toggleAll(picked: Set<string>, ids: string[]): Set<string> {
  const next = new Set(picked);
  const allOn = ids.length > 0 && ids.every((id) => next.has(id));
  for (const id of ids) {
    if (allOn) next.delete(id);
    else next.add(id);
  }
  return next;
}
