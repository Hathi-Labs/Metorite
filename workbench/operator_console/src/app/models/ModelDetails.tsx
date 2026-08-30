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
    ];
    const bad = boxes.find(([, v]) => badNumber(v));
    if (bad) {
      setResult({
        ok: false,
        text: `"${bad[1].trim()}" is not a number (${bad[0]}). Fix the box or clear it — blank means unknown.`,
      });
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
    setReadsImages(v.readsImages);
    setThinksFirst(v.thinksFirst);
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
