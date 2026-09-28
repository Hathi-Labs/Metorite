"use client";

// Balance & health — can each vendor account we call on still serve?
// Owner request, 2026-09-28.
//
// 🔴 **Why it sits at the TOP of the page.** DeepSeek held -0.05 USD for two
// days and every AI call failed. The accounts below it were all "armed", so
// the one screen an operator would check said everything was fine.
//
// ⚠️ **The rows are read on the SERVER with the caller's own token**, like the
// credential list, and passed down. This component only draws them and runs
// "Check now", which posts to the BFF and then refreshes the page.
//
// ⚠️ **`unknown` is a neutral chip, never a green one.** Most vendors expose
// no balance at all. For those the refusal count IS the alert, and the row
// says so.

import { useState } from "react";
import { useRouter } from "next/navigation";

import type { VendorHealth } from "@/lib/contract";
import { formatDateTime } from "@/lib/format";
import {
  describeBalance,
  describeDaysLeft,
  describeRefusals,
  healthLabel,
  healthTone,
} from "@/lib/providerHealth";
import type { Origin } from "@/lib/source";
import { chipClass } from "@/lib/tone";

export default function ProviderHealthPanel({
  rows,
  origin,
  note,
}: {
  rows: VendorHealth[];
  origin: Origin;
  note?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function checkNow() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operator/providers/health/check", { method: "POST" });
      if (!r.ok) {
        setError(`The Console refused: ${await r.text()}`);
      } else {
        router.refresh();
      }
    } catch {
      setError("The request did not complete. Nothing was checked.");
    } finally {
      setBusy(false);
    }
  }

  const invisible = rows.filter((r) => r.status === "unknown").length;

  return (
    <section className="panel" id="balance">
      <div className="pagehead">
        <div className="panel-head">
          <h2>Balance &amp; health</h2>
          <p>
            Whether each vendor account we call on can still serve. The balance
            is what the vendor reported. A vendor that shows no balance is
            watched by its refusals instead: a 402 means the account is empty,
            and a 401 or 403 means the key is refused.
          </p>
        </div>
        {origin === "live" && rows.length > 0 && (
          <button type="button" className="secondary" onClick={checkNow} disabled={busy}>
            {busy ? "Checking…" : "Check now"}
          </button>
        )}
      </div>

      {error && (
        <div className="banner danger" role="alert">
          {error}
        </div>
      )}

      {origin !== "live" ? (
        // ⚠️ Never a blank panel. A Console that predates the route says so
        // in blue, and one that refused says so in red.
        <div className={`banner ${origin === "error" ? "danger" : "info"}`} role="status">
          {note ?? "The provider health read did not load."}
        </div>
      ) : rows.length === 0 ? (
        <p className="field-hint">
          No platform vendor account is installed, so there is nothing to watch.
        </p>
      ) : (
        <>
          <div className="tablewrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Status</th>
                  <th>Balance</th>
                  <th>Days left</th>
                  <th>Last checked</th>
                  <th>Refusals</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.provider}>
                    <td className="mono">{r.provider}</td>
                    <td>
                      <span className={chipClass(healthTone(r.status))} title={r.reason}>
                        {healthLabel(r.status)}
                      </span>
                      {r.reason && <div className="field-hint">{r.reason}</div>}
                    </td>
                    <td className="mono">
                      {describeBalance(r)}
                      {r.threshold && r.balance !== null && (
                        <div className="field-hint">low under {r.threshold}</div>
                      )}
                    </td>
                    <td className="mono">{describeDaysLeft(r)}</td>
                    <td className="mono">
                      {formatDateTime(r.balanceCheckedAt)}
                      {r.probeError && <div className="field-hint">{r.probeError}</div>}
                    </td>
                    <td className="mono">
                      {describeRefusals(r)}
                      {r.lastRefusalAt && (
                        <div className="field-hint">{formatDateTime(r.lastRefusalAt)}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {invisible > 0 && (
            <p className="note">
              {invisible === 1 ? "One vendor does" : `${invisible} vendors do`} not
              expose a balance we can read. For those, a refusal is the first sign
              of an empty account, and it shows here within a minute.
            </p>
          )}
        </>
      )}
    </section>
  );
}
