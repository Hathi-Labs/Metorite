"use client";

// Taking a model back OUT of the catalog — the act the page never had.
//
// 🔴 **Why it was needed.** A model reaches this page, and every backup-chain
// picker on `/tiers`, because it has a `model_capability` row. One click on
// the vendor feed writes one, and the live feed holds about 4300 models. So
// the catalog could only ever grow, a mis-click was permanent, and the tier
// pickers filled with models nobody meant to sell. Owner report, 2026-09-21.
//
// ⚠️ **It refuses while a tier serves from the model, and the CONSOLE is the
// authority.** This component knows `used` and says so before the click, which
// is a courtesy — the page's tier data is a render old, and two operators can
// bind and remove at the same second. The Console re-checks under the same
// transaction and its 400 names the tiers. That refusal relays verbatim.
//
// ⚠️ **The prices are KEPT** (owner decision, 2026-09-21). `model_profile`
// survives, so a recorded cost still reconciles and re-adding the model
// restores the numbers instead of starting it costs-blind. The confirm says
// this out loud, because an operator who suspects "remove" also throws away
// an afternoon of price entry will not press it.
//
// ⚠️ **Its own file, so `ModelBrowser` stays free of `fetch(`** — the same
// split as `ModelDetails` and `FeedStrip`.

import { useState } from "react";
import { useRouter } from "next/navigation";

import type { CatalogModel } from "@/lib/contract";
import { HELP_REMOVE } from "@/lib/help";
import { rankWord, type TierUse } from "@/lib/modelSearch";
import { REMOVE } from "@/lib/words";

export default function RemoveModel({
  m,
  used,
}: {
  m: CatalogModel;
  /** The in-force bindings this page already computed for the card. */
  used: TierUse[];
}) {
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const blocked = used.length > 0;

  async function remove() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch("/api/operator/catalog/capabilities", {
        method: "DELETE",
        headers: { "content-type": "application/json" },
        // ⚠️ No `task`. An operator removing a model means the model, not one
        // of its jobs — and a half-removed model is a state nothing on this
        // page can draw.
        body: JSON.stringify({ model: m.id }),
      });
      if (!res.ok) {
        // Verbatim. The Console's refusal names the tiers and the ranks, and
        // a paraphrase here would give one 400 two vocabularies.
        setErr(`The Console refused: ${await res.text()}`);
        return;
      }
      setAsking(false);
      router.refresh();
    } catch {
      setErr(
        "The Console did not answer. Nothing was removed — check the " +
          "network, then reload to see where the catalog stands.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <button
        type="button"
        className="linklike danger"
        title={blocked ? HELP_REMOVE.blocked : HELP_REMOVE.open}
        onClick={() => setAsking(true)}
      >
        {REMOVE.one}
      </button>
    );
  }

  return (
    <div className="removeconfirm" role="group" aria-label={`Remove ${m.id}`}>
      <p>
        Remove <span className="mono small">{m.id}</span> from the catalog?
      </p>

      {/* 🔴 The blocking case, said BEFORE the click rather than as a 400
          after it. The operator's next act is on /tiers, so the link is here
          and not in a sentence telling them to go and find it. */}
      {blocked ? (
        <p className="result err">
          {used.length === 1 ? "A tier serves" : `${used.length} tiers serve`} from
          this model:{" "}
          {used.map((u) => `${u.tier} (${u.task}, ${rankWord(u.rank)})`).join(", ")}.
          Re-point {used.length === 1 ? "it" : "them"} first, then remove it.{" "}
          <a href="/tiers">Open Tiers →</a>
        </p>
      ) : (
        <p className="muted small">
          It leaves this page and every backup-chain picker on Tiers. The
          prices you recorded are kept, so a past cost still reconciles and
          re-adding it from your vendors restores your numbers.
        </p>
      )}

      <div className="rowline">
        {/* ⚠️ Drawn and DISABLED when a tier serves from the model, never
            hidden. A missing button cannot say why it is missing, and the
            reader is left wondering whether removal exists at all. */}
        <button
          type="button"
          className="danger"
          disabled={busy || blocked}
          title={blocked ? HELP_REMOVE.blocked : HELP_REMOVE.confirm}
          onClick={remove}
        >
          {busy ? REMOVE.busy : REMOVE.confirm}
        </button>
        <button
          type="button"
          className="linklike"
          disabled={busy}
          title={HELP_REMOVE.keep}
          onClick={() => {
            setAsking(false);
            setErr(null);
          }}
        >
          {REMOVE.keep}
        </button>
      </div>

      {err && <p className="result err">{err}</p>}
    </div>
  );
}
