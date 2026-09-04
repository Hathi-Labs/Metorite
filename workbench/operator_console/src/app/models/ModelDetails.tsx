"use client";

// What a model IS — the editor behind each card's "Add details".
//
// 🔴 **Its own file, so `ModelBrowser` stays free of `fetch(`.** That fence is
// about READS: the catalog is read by the server component with the caller's
// own token, and a client GET would put a cross-tenant list on a path the
// browser can replay. A write to our own BFF route is a different thing, and
// separating them keeps the fence meaning what it says.
//
// ⚠️ **Empty means UNKNOWN and is sent as null.** A blank context window must
// not arrive as 0 — the database refuses a zero window on purpose, because "0
// tokens" reads as a broken model while a missing row reads as a missing row.
//
// ⚠️ **The price here is what the VENDOR charges US.** The rate card below the
// list is what a customer pays. Two numbers, two tables, and reading one as the
// other inverts a margin — so the label says "we pay" and the unit is on it.

import { useState } from "react";

import type { CatalogModel, FeedModel } from "@/lib/contract";
import { driftFor, prefillFrom } from "@/lib/feed";
import { windowProblem, wrapsMidnight } from "@/lib/window";

/** Blank means UNKNOWN and travels as null. A typed value travels as the
 *  TRIMMED STRING, verbatim — the wire rule for money ("0.280000" must not
 *  come back as 0.28) — and pydantic parses it into an exact number. */
function blankToNull(raw: string): string | null {
  const t = raw.trim();
  return t === "" ? null : t;
}

/** Typed but not a number. Refused HERE, loudly: Number() once turned
 *  "3,50" into null silently, and a green "Saved." un-costed the model. */
function badNumber(raw: string): boolean {
  const t = raw.trim();
  return t !== "" && !Number.isFinite(Number(t));
}

export default function ModelDetails({
  m,
  feedRow,
}: {
  m: CatalogModel;
  /** Upstream's claim about this model (014). Absent when the feed has
   *  never been fetched or does not know the model. */
  feedRow?: FeedModel;
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState(m.label === m.id ? "" : m.label);
  const [ctx, setCtx] = useState(m.contextWindow?.toString() ?? "");
  const [out, setOut] = useState(m.maxOutput?.toString() ?? "");
  const [vin, setVin] = useState(m.inputPer1M?.toString() ?? "");
  const [vout, setVout] = useState(m.outputPer1M?.toString() ?? "");
  const [vcached, setVcached] = useState(m.cachedInputPer1M?.toString() ?? "");
  // The three per-unit costs (019, H-78). Each box holds the TASK's own
  // unit — a minute of audio, a character of text, one image — and nothing
  // on this page converts anything.
  const [vmin, setVmin] = useState(m.perMinuteUsd?.toString() ?? "");
  const [vchar, setVchar] = useState(m.perCharacterUsd?.toString() ?? "");
  const [vimg, setVimg] = useState(m.perImageUsd?.toString() ?? "");
  // 023 — the OFF-PEAK rates and the window that selects them.
  //
  // ⚠️ The three boxes above hold the PEAK rate. They keep their plain labels
  // because that is what the vendor feed fills them with, and because R6
  // forbade renaming the columns underneath.
  //
  // 🔴 These change what a call COST us, never what a customer pays: D67 keys
  // the charge on the tier. And a tier PRICE still derives from the peak rate
  // alone (owner directive, 2026-09-04), so nothing on the Pricing board reads
  // an off-peak number.
  const [vinOff, setVinOff] = useState(m.inputOffpeakPer1M?.toString() ?? "");
  const [voutOff, setVoutOff] = useState(m.outputOffpeakPer1M?.toString() ?? "");
  const [vcachedOff, setVcachedOff] = useState(
    m.cachedInputOffpeakPer1M?.toString() ?? "",
  );
  const [offStart, setOffStart] = useState(m.offpeakStartUtc ?? "");
  const [offEnd, setOffEnd] = useState(m.offpeakEndUtc ?? "");
  // 023 — the long-context threshold and its rates. Without them a large
  // document under-bills by half, on exactly the calls that cost most.
  const [ctxThreshold, setCtxThreshold] = useState(
    m.contextTierThreshold?.toString() ?? "",
  );
  const [vinLong, setVinLong] = useState(m.inputLongPer1M?.toString() ?? "");
  const [voutLong, setVoutLong] = useState(m.outputLongPer1M?.toString() ?? "");
  const [vcachedLong, setVcachedLong] = useState(
    m.cachedInputLongPer1M?.toString() ?? "",
  );
  const [description, setDescription] = useState(m.description);
  const [readsImages, setReadsImages] = useState(m.kinds.includes("vision"));
  const [thinksFirst, setThinksFirst] = useState(m.kinds.includes("reasoning"));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const known =
    m.contextWindow !== null || m.inputPer1M !== null || m.description !== "";

  async function save() {
    const boxes: [string, string][] = [
      ["context window", ctx],
      ["max output", out],
      ["$ per 1M in", vin],
      ["$ per 1M out", vout],
      ["$ per 1M cached", vcached],
      ["$ per minute", vmin],
      ["$ per character", vchar],
      ["$ per image", vimg],
      ["$ per 1M in, off-peak", vinOff],
      ["$ per 1M out, off-peak", voutOff],
      ["$ per 1M cached, off-peak", vcachedOff],
      ["long-context threshold", ctxThreshold],
      ["$ per 1M in, long context", vinLong],
      ["$ per 1M out, long context", voutLong],
      ["$ per 1M cached, long context", vcachedLong],
    ];
    const bad = boxes.find(([, v]) => badNumber(v));
    if (bad) {
      setResult({
        ok: false,
        text: `"${bad[1].trim()}" is not a number (${bad[0]}). Fix the box or clear it — blank means unknown.`,
      });
      return;
    }

    // ⚠️ The judgement lives in `lib/window.ts`, not here — the repo's rule
    // (`priceboard.ts`, `pricing.ts`) is that anything with a right and a
    // wrong answer is a pure function with its own test. The database refuses
    // the same shape, so this only moves WHERE the operator finds out.
    const problem = windowProblem(offStart, offEnd);
    if (problem) {
      setResult({ ok: false, text: problem.message });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/profiles", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          model: m.id,
          label: label.trim() || null,
          context_window: blankToNull(ctx),
          max_output: blankToNull(out),
          vendor_input_per_1m_usd: blankToNull(vin),
          vendor_output_per_1m_usd: blankToNull(vout),
          // ⚠️ Without this, a cache-hitting call cannot be COSTED at all —
          // the metering write refuses to estimate (013).
          vendor_cached_input_per_1m_usd: blankToNull(vcached),
          // ⚠️ The per-unit costs (H-78). These are what makes an image,
          // transcribe or speak job costable at all — its token price is
          // null and always will be.
          vendor_per_minute_usd: blankToNull(vmin),
          vendor_per_character_usd: blankToNull(vchar),
          vendor_per_image_usd: blankToNull(vimg),
          // 023 — the off-peak rates, the window, and the long-context tier.
          // ⚠️ The Console's ProfileRequest is `extra="forbid"`, so a
          // misnamed key here answers 422 rather than storing nothing.
          vendor_input_offpeak_per_1m_usd: blankToNull(vinOff),
          vendor_output_offpeak_per_1m_usd: blankToNull(voutOff),
          vendor_cached_input_offpeak_per_1m_usd: blankToNull(vcachedOff),
          offpeak_start_utc: offStart.trim() || null,
          offpeak_end_utc: offEnd.trim() || null,
          context_tier_threshold: blankToNull(ctxThreshold),
          vendor_input_long_per_1m_usd: blankToNull(vinLong),
          vendor_output_long_per_1m_usd: blankToNull(voutLong),
          vendor_cached_input_long_per_1m_usd: blankToNull(vcachedLong),
          description: description.trim(),
          reads_images: readsImages,
          thinks_first: thinksFirst,
        }),
      });
      setResult({ ok: res.ok, text: await res.text() });
    } catch {
      setResult({
        ok: false,
        text: "The Console did not answer. Nothing saved — check the network and try again.",
      });
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="linklike add-job" onClick={() => setOpen(true)}>
        {known ? "Edit details" : "+ Add details"}
      </button>
    );
  }

  // The feed PREFILLS, the operator SAVES — the click below only fills the
  // boxes, and the write is still the same Save it always was.
  const drift = driftFor(m, feedRow);

  function copyFromFeed() {
    if (!feedRow) return;
    const v = prefillFrom(feedRow);
    setCtx(v.ctx);
    setOut(v.out);
    setVin(v.vin);
    setVout(v.vout);
    setVcached(v.vcached);
    setVmin(v.vmin);
    setVchar(v.vchar);
    setVimg(v.vimg);
    setReadsImages(v.readsImages);
    setThinksFirst(v.thinksFirst);
    // ⚠️ The off-peak and long-context boxes are DELIBERATELY not filled here,
    // and this is not an omission. `vendor_price_feed` has no window dimension
    // and no context tier — litellm publishes one rate per model — so there is
    // nothing upstream to copy. Prefilling them from the peak number would
    // write a fact nobody measured, and the operator would then see two
    // identical rates and reasonably believe the vendor charges one price.
  }

  return (
    <div className="job-edit">
      <p className="field-hint">
        Leave a box empty for &ldquo;we do not know&rdquo;. It shows as a dash,
        which is true — a zero would read as a broken model.
      </p>

      {feedRow && (
        <p className={drift.length > 0 ? "field-hint warn" : "field-hint"}>
          {drift.length > 0 ? (
            <>
              The vendor&apos;s published price moved:{" "}
              {drift
                .map((d) => `${d.label} is now $${d.upstream} (we say $${d.ours})`)
                .join(", ")}
              .{" "}
            </>
          ) : (
            <>Upstream knows this model. </>
          )}
          <button type="button" className="linklike" onClick={copyFromFeed}>
            Copy the vendor&apos;s facts into the boxes
          </button>{" "}
          — then check and save.
        </p>
      )}

      <label htmlFor={`lbl-${m.id}`}>Name</label>
      <input
        id={`lbl-${m.id}`}
        value={label}
        placeholder={m.id}
        onChange={(e) => setLabel(e.target.value)}
      />

      <label htmlFor={`d-${m.id}`}>What it is good at</label>
      <input
        id={`d-${m.id}`}
        value={description}
        placeholder="cheap and quick, weaker on long reasoning"
        onChange={(e) => setDescription(e.target.value)}
      />

      <div className="formrow">
        <div className="field">
          <label htmlFor={`c-${m.id}`}>Reads at most</label>
          <input
            id={`c-${m.id}`}
            inputMode="numeric"
            value={ctx}
            placeholder="200000"
            onChange={(e) => setCtx(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`o-${m.id}`}>Writes at most</label>
          <input
            id={`o-${m.id}`}
            inputMode="numeric"
            value={out}
            placeholder="64000"
            onChange={(e) => setOut(e.target.value)}
          />
        </div>
      </div>

      <div className="formrow">
        <div className="field">
          <label htmlFor={`vi-${m.id}`}>We pay, per 1M in</label>
          <input
            id={`vi-${m.id}`}
            inputMode="decimal"
            value={vin}
            placeholder="3"
            onChange={(e) => setVin(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vo-${m.id}`}>We pay, per 1M out</label>
          <input
            id={`vo-${m.id}`}
            inputMode="decimal"
            value={vout}
            placeholder="15"
            onChange={(e) => setVout(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vc-${m.id}`}>We pay, per 1M cached in</label>
          <input
            id={`vc-${m.id}`}
            inputMode="decimal"
            value={vcached}
            placeholder="0.3"
            onChange={(e) => setVcached(e.target.value)}
          />
        </div>
      </div>

      {/* 023 — the OFF-PEAK window.
          🔴 The three boxes above are the PEAK rate. DeepSeek charges less for
          part of the day, so one number per token kind recorded a cheap call
          and a dear one as costing the same.
          ⚠️ This changes what a call COST us and never what a customer pays —
          D67 keys the charge on the tier — and a tier PRICE still derives from
          the peak rate alone (owner directive, 2026-09-04). */}
      <p className="field-hint">
        Only for a vendor that charges less at certain hours. Leave every box
        empty and this model is priced the same all day, which is how almost
        every vendor works. The three boxes above are the <b>peak</b> rate.
      </p>
      <div className="formrow">
        <div className="field">
          <label htmlFor={`os-${m.id}`}>Off-peak starts (UTC)</label>
          <input
            id={`os-${m.id}`}
            value={offStart}
            placeholder="16:30"
            onChange={(e) => setOffStart(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`oe-${m.id}`}>Off-peak ends (UTC)</label>
          <input
            id={`oe-${m.id}`}
            value={offEnd}
            placeholder="00:30"
            onChange={(e) => setOffEnd(e.target.value)}
          />
        </div>
      </div>
      {/* The window MAY wrap midnight, and DeepSeek's does. Said out loud
          here because an operator reading "starts 16:30, ends 00:30" would
          otherwise reasonably wonder whether it means eight hours or none. */}
      <p className="field-hint">
        {wrapsMidnight(offStart, offEnd) ? (
          <>
            This window <b>crosses midnight</b> — it runs from {offStart.trim()}{" "}
            tonight to {offEnd.trim()} tomorrow. That is intended for a vendor
            like DeepSeek, whose cheap hours span the night.
          </>
        ) : (
          <>
            A window may cross midnight — <code>16:30</code> to{" "}
            <code>00:30</code> is eight hours over the night. Both times, or
            neither.
          </>
        )}
      </p>
      <div className="formrow">
        <div className="field">
          <label htmlFor={`vio-${m.id}`}>We pay, per 1M in (off-peak)</label>
          <input
            id={`vio-${m.id}`}
            inputMode="decimal"
            value={vinOff}
            placeholder="0.22"
            onChange={(e) => setVinOff(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`voo-${m.id}`}>We pay, per 1M out (off-peak)</label>
          <input
            id={`voo-${m.id}`}
            inputMode="decimal"
            value={voutOff}
            placeholder="0.66"
            onChange={(e) => setVoutOff(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vco-${m.id}`}>We pay, per 1M cached (off-peak)</label>
          <input
            id={`vco-${m.id}`}
            inputMode="decimal"
            value={vcachedOff}
            placeholder="0.007"
            onChange={(e) => setVcachedOff(e.target.value)}
          />
        </div>
      </div>

      {/* 023 — the LONG-CONTEXT tier. Without it a large document under-bills
          by half, on exactly the calls that already cost most. */}
      <p className="field-hint">
        Only for a vendor that charges more above a context size. Set the
        threshold and the rates that apply above it. Leave empty for one rate
        at every size.
      </p>
      <div className="formrow">
        <div className="field">
          <label htmlFor={`ct-${m.id}`}>Long context above (tokens)</label>
          <input
            id={`ct-${m.id}`}
            inputMode="numeric"
            value={ctxThreshold}
            placeholder="272000"
            onChange={(e) => setCtxThreshold(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vil-${m.id}`}>We pay, per 1M in (long)</label>
          <input
            id={`vil-${m.id}`}
            inputMode="decimal"
            value={vinLong}
            placeholder="8"
            onChange={(e) => setVinLong(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vol-${m.id}`}>We pay, per 1M out (long)</label>
          <input
            id={`vol-${m.id}`}
            inputMode="decimal"
            value={voutLong}
            placeholder="30"
            onChange={(e) => setVoutLong(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vcl-${m.id}`}>We pay, per 1M cached (long)</label>
          <input
            id={`vcl-${m.id}`}
            inputMode="decimal"
            value={vcachedLong}
            placeholder="0.8"
            onChange={(e) => setVcachedLong(e.target.value)}
          />
        </div>
      </div>

      {/* The per-unit costs (H-78). A transcribe, speak or image model has
          no token price, so without these the pricing board can cost it
          only from a figure somebody types by hand every time.
          ⚠️ The audio box is per MINUTE. litellm publishes per second, and
          the Console multiplied by 60 before this box ever saw it. */}
      <p className="field-hint">
        For a model that is not priced by the token. Fill in the one line its
        job uses, and leave the rest empty.
      </p>
      <div className="formrow">
        <div className="field">
          <label htmlFor={`vm-${m.id}`}>We pay, per minute of audio</label>
          <input
            id={`vm-${m.id}`}
            inputMode="decimal"
            value={vmin}
            placeholder="0.006"
            onChange={(e) => setVmin(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vch-${m.id}`}>We pay, per character spoken</label>
          <input
            id={`vch-${m.id}`}
            inputMode="decimal"
            value={vchar}
            placeholder="0.000015"
            onChange={(e) => setVchar(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`vim-${m.id}`}>We pay, per image</label>
          <input
            id={`vim-${m.id}`}
            inputMode="decimal"
            value={vimg}
            placeholder="0.04"
            onChange={(e) => setVimg(e.target.value)}
          />
        </div>
      </div>

      <label>
        <input
          type="checkbox"
          checked={readsImages}
          onChange={(e) => setReadsImages(e.target.checked)}
        />
        Reads images
      </label>
      <label>
        <input
          type="checkbox"
          checked={thinksFirst}
          onChange={(e) => setThinksFirst(e.target.checked)}
        />
        Thinks before answering
      </label>

      <div className="job-actions">
        <button type="button" disabled={busy} onClick={save}>
          Save
        </button>
        <button type="button" className="linklike" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>

      {result && (
        <p className={result.ok ? "result ok" : "result err"}>
          {result.ok
            ? "Saved. Reload to see it on the card."
            : `The Console refused: ${result.text}`}
        </p>
      )}
    </div>
  );
}
