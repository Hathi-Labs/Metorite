"use client";

import { useState } from "react";
import {
  canActivate,
  lifecycleActions,
  formatPaise,
  type CatalogPlan,
} from "@/lib/format";

// The management ACTIONS for one customer. Every action POSTs to a server-side
// `/api/operator/*` BFF route that holds the operator token; NOTHING here holds
// a credential. The Console is the authority on every refusal (the seat cap
// 409, the double-grant 409, an illegal lifecycle transition 409) — this UI
// relays whatever it answers rather than pre-judging it.

type Result = { ok: boolean; text: string } | null;

async function post(path: string, body: unknown): Promise<Result> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  return { ok: res.ok, text };
}

function ResultLine({ result }: { result: Result }) {
  if (!result) return null;
  return (
    <div className={`result ${result.ok ? "ok" : "err"}`}>{result.text}</div>
  );
}

export default function Actions({
  slug,
  status,
  subscriptionStatus,
  plans,
}: {
  slug: string;
  status: string;
  subscriptionStatus: string | null;
  plans: CatalogPlan[];
}) {
  return (
    <>
      <h2>Manage</h2>
      <div className="row">
        {canActivate(subscriptionStatus) && (
          <ActivatePanel slug={slug} plans={plans} />
        )}
        <SeatsPanel slug={slug} plans={plans} />
        <CreditsPanel slug={slug} />
        <LifecyclePanel slug={slug} status={status} />
      </div>
    </>
  );
}

function reload() {
  // Re-read the server-rendered numbers after a successful write.
  window.location.reload();
}

function ActivatePanel({ slug, plans }: { slug: string; plans: CatalogPlan[] }) {
  const [plan, setPlan] = useState(plans[0]?.slug ?? "");
  const [seats, setSeats] = useState("1");
  const [credits, setCredits] = useState("");
  const [reference, setReference] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body: Record<string, unknown> = {
      org_slug: slug,
      plan_slug: plan,
      seats: Number(seats),
    };
    if (credits.trim()) body.credits = credits.trim();
    if (reference.trim()) body.reference = reference.trim();
    const r = await post("/api/operator/activate", body);
    setResult(r);
    setBusy(false);
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>Activate subscription</h2>
      <p className="muted">Manual / bank-transfer activation of a paid plan.</p>
      <label>Plan</label>
      <select value={plan} onChange={(e) => setPlan(e.target.value)}>
        {plans.map((p) => (
          <option key={p.slug} value={p.slug}>
            {p.name} ({formatPaise(p.price_paise)})
          </option>
        ))}
      </select>
      <label>Seats</label>
      <input
        type="number"
        min={1}
        value={seats}
        onChange={(e) => setSeats(e.target.value)}
      />
      <label>AI credits (optional)</label>
      <input value={credits} onChange={(e) => setCredits(e.target.value)} />
      <label>Bank reference (optional)</label>
      <input
        value={reference}
        onChange={(e) => setReference(e.target.value)}
      />
      <button type="submit" disabled={busy || !plan}>
        Activate
      </button>
      <ResultLine result={result} />
    </form>
  );
}

function SeatsPanel({ slug, plans }: { slug: string; plans: CatalogPlan[] }) {
  const [plan, setPlan] = useState(plans[0]?.slug ?? "core");
  const [email, setEmail] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);

  async function act(path: string) {
    setBusy(true);
    const r = await post(path, { org_slug: slug, plan_slug: plan, email });
    setResult(r);
    setBusy(false);
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={(e) => e.preventDefault()}>
      <h2 style={{ marginTop: 0 }}>Seats</h2>
      <label>Plan</label>
      <select value={plan} onChange={(e) => setPlan(e.target.value)}>
        {plans.map((p) => (
          <option key={p.slug} value={p.slug}>
            {p.name}
          </option>
        ))}
      </select>
      <label>Member email</label>
      <input value={email} onChange={(e) => setEmail(e.target.value)} />
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          disabled={busy || !email}
          onClick={() => act("/api/operator/seats")}
        >
          Assign
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy || !email}
          onClick={() => act("/api/operator/seats/release")}
        >
          Release
        </button>
      </div>
      <ResultLine result={result} />
    </form>
  );
}

function CreditsPanel({ slug }: { slug: string }) {
  const [credits, setCredits] = useState("");
  const [reason, setReason] = useState("grant");
  const [ref, setRef] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body: Record<string, unknown> = {
      org_slug: slug,
      credits: credits.trim(),
      reason,
    };
    if (ref.trim()) body.ref = ref.trim();
    const r = await post("/api/operator/credits", body);
    setResult(r);
    setBusy(false);
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>Add AI credits</h2>
      <p className="muted">Append-only; a correction is another row.</p>
      <label>Credits</label>
      <input value={credits} onChange={(e) => setCredits(e.target.value)} />
      <label>Reason</label>
      <select value={reason} onChange={(e) => setReason(e.target.value)}>
        <option value="grant">grant</option>
        <option value="manual">manual</option>
        <option value="adjustment">adjustment</option>
      </select>
      <label>Reference (optional)</label>
      <input value={ref} onChange={(e) => setRef(e.target.value)} />
      <button type="submit" disabled={busy || !credits.trim()}>
        Grant
      </button>
      <ResultLine result={result} />
    </form>
  );
}

function LifecyclePanel({ slug, status }: { slug: string; status: string }) {
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);
  const actions = lifecycleActions(status);

  async function move(target: string) {
    setBusy(true);
    const r = await post("/api/operator/lifecycle", {
      org_slug: slug,
      target,
      reason: reason.trim() || undefined,
    });
    setResult(r);
    setBusy(false);
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={(e) => e.preventDefault()}>
      <h2 style={{ marginTop: 0 }}>Lifecycle</h2>
      <p className="muted">
        Current: <span className={`pill ${status}`}>{status}</span>
      </p>
      <label>Reason (optional)</label>
      <input value={reason} onChange={(e) => setReason(e.target.value)} />
      {actions.length === 0 && (
        <p className="muted">No transition available.</p>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        {actions.map((a) => (
          <button
            key={a.target}
            type="button"
            className={a.target === "suspended" ? "danger" : undefined}
            disabled={busy}
            onClick={() => move(a.target)}
          >
            {a.label}
          </button>
        ))}
      </div>
      <ResultLine result={result} />
    </form>
  );
}
