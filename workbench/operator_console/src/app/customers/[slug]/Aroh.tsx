"use client";

import { useState } from "react";
import {
  arohState,
  seatCountProblem,
  seatLine,
  type ArohApp,
} from "@/lib/aroh";
import { formatCredits, formatDate, formatUsd } from "@/lib/format";
import { chipClass } from "@/lib/tone";
import { post, ResultLine, type Result } from "./Actions";

// The "Aroh" tab of a customer page (CP-APP, Metorite task 1105).
//
// The operator gives this organization the Aroh app with N seats. A member
// takes a seat when they sign in to Aroh with Metorite: there is no second
// approval here. Aroh's AI use is charged to this organization's own credits,
// so this tab grants no credits. It shows the switch and the count, who holds
// a seat (with Release), and what Aroh spent.
//
// Every act POSTs to a BFF route. The Console is the authority on each
// refusal, and the panel relays it word for word (DESIGN.md §7 rule 4).

function reload() {
  window.location.reload();
}

export default function ArohPanel({
  slug,
  app,
  error,
}: {
  slug: string;
  app: ArohApp | null;
  error: string | null;
}) {
  if (error || !app) {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>Aroh</h2>
          <p>The Aroh app for this organization.</p>
        </div>
        <p className="field-hint warn">{error ?? "The Console did not send the Aroh state."}</p>
      </section>
    );
  }
  const state = arohState(app);
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>
            Aroh <span className={chipClass(state.tone)}>{state.label}</span>
          </h2>
          <p>
            {seatLine(app.seats)}. Members of this organization sign in to Aroh with
            Metorite and take a seat at once. Aroh&apos;s AI use comes from this
            organization&apos;s credits.
          </p>
        </div>
        {app.deployment ? (
          <p className="muted small">
            Served by the box <code>{app.deployment.label}</code> ({app.deployment.base_url}).
            {app.updated_by ? ` Last change by ${app.updated_by}, ${formatDate(app.updated_at)}.` : ""}
          </p>
        ) : (
          <p className="field-hint warn">
            No box serves Aroh yet. <a href="/aroh">Connect Aroh</a> first, then turn it on here.
          </p>
        )}
      </section>
      <div className="row">
        <ArohSettingsForm slug={slug} app={app} />
        <ArohUsage app={app} />
      </div>
      <ArohHolders slug={slug} app={app} />
    </>
  );
}

function ArohSettingsForm({ slug, app }: { slug: string; app: ArohApp }) {
  const [enabled, setEnabled] = useState(app.configured ? app.enabled : true);
  const [count, setCount] = useState(String(app.seats.purchased || ""));
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);
  const problem = seatCountProblem(count, app.seats.assigned, reason);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await post("/api/operator/apps", {
      org_slug: slug,
      app: "aroh",
      enabled,
      seats: Number(count.trim()),
      reason: reason.trim(),
    });
    setResult(r);
    setBusy(false);
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>Aroh for this organization</h2>
      <p className="muted">
        Turn Aroh on and set how many members may use it. Off keeps the seats, and
        on again gives each holder the same seat back.
      </p>
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Aroh is on for this organization
      </label>
      <label>Aroh seats</label>
      <input
        inputMode="numeric"
        value={count}
        placeholder="e.g. 2"
        onChange={(e) => setCount(e.target.value)}
      />
      <div className="field-hint">
        Now: {app.seats.purchased} seats, {app.seats.assigned} in use.
      </div>
      <label>Reason</label>
      <input
        value={reason}
        placeholder="e.g. Aroh pilot for two people"
        onChange={(e) => setReason(e.target.value)}
      />
      <p className="muted small">Needs an admin.</p>
      <button
        type="submit"
        title={problem ?? "Save the switch and the seat count. The change is logged."}
        disabled={busy || problem !== null}
      >
        {busy ? "Saving…" : "Save Aroh"}
      </button>
      <ResultLine result={result} />
    </form>
  );
}

function ArohHolders({ slug, app }: { slug: string; app: ArohApp }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<Result>(null);

  async function release(email: string) {
    setBusy(email);
    // The staff release door, the one every seat uses. Freeing is always safe.
    const r = await post("/api/operator/seats/release", {
      org_slug: slug,
      plan_slug: "aroh",
      email,
    });
    setResult(r);
    setBusy(null);
    if (r?.ok) reload();
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Who holds an Aroh seat</h2>
        <p>Release a seat to free it for another member. Their Metorite account stays.</p>
      </div>
      {app.holders.length === 0 ? (
        <p className="empty">
          No member holds an Aroh seat yet. A member takes one the first time they sign
          in to Aroh.
        </p>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Member</th>
                <th>Since</th>
                <th>Membership</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {app.holders.map((h) => (
                <tr key={h.email}>
                  <td>
                    {h.display_name ? `${h.display_name} · ` : ""}
                    <span className="mono">{h.email}</span>
                  </td>
                  <td className="muted small">{formatDate(h.assigned_at)}</td>
                  <td>{h.membership_status ?? "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy !== null}
                      onClick={() => release(h.email)}
                    >
                      {busy === h.email ? "Releasing…" : "Release seat"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ResultLine result={result} />
    </section>
  );
}

function ArohUsage({ app }: { app: ArohApp }) {
  const u = app.usage;
  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Aroh AI use, last {u.window_days} days</h2>
      <p className="muted">
        Calls that Aroh made for this organization&apos;s members, charged to its
        credits. The meter names them <code>aroh</code>.
      </p>
      <div className="stats">
        <div className="stat">
          <div className="lbl">Calls</div>
          <div className="num small-num">{u.calls}</div>
        </div>
        <div className="stat">
          <div className="lbl">Credits</div>
          <div className="num small-num">{formatCredits(u.credits)}</div>
        </div>
        <div className="stat">
          <div className="lbl">Our cost</div>
          <div className="num small-num">{formatUsd(u.cost_usd)}</div>
        </div>
      </div>
      {u.members.length === 0 ? (
        <p className="empty">No Aroh calls in this window.</p>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Member</th>
                <th>Calls</th>
                <th>Credits</th>
                <th>Our cost</th>
              </tr>
            </thead>
            <tbody>
              {u.members.map((m) => (
                <tr key={m.member}>
                  <td className="mono">{m.member}</td>
                  <td>{m.calls}</td>
                  <td className="mono">{formatCredits(m.credits)}</td>
                  <td className="mono">{formatUsd(m.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
