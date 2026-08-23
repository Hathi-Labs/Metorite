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

// A refusal is relayed VERBATIM (the Console is the authority); the framing
// line just tells the operator whose voice the raw text is in.
function ResultLine({ result }: { result: Result }) {
  if (!result) return null;
  if (result.ok) return <div className="result ok">✓ Done</div>;
  return (
    <div className="result err">
      The Console refused:
      {"\n"}
      {result.text}
    </div>
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
      <h2>Manage this customer</h2>
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

// The Plan picker, or — when the catalog did not arrive — a line saying so
// instead of an empty dropdown. The BANNER above carries the reason; this says
// only that the control is unusable, so the two cannot contradict each other.
function PlanPicker({
  plans,
  value,
  onChange,
  showPrice = false,
}: {
  plans: CatalogPlan[];
  value: string;
  onChange: (v: string) => void;
  // Only the ACTIVATE form prices the ladder. Assigning a seat spends capacity
  // that is already bought, so quoting a monthly price there would read as a
  // charge this action does not make.
  showPrice?: boolean;
}) {
  if (plans.length === 0) {
    return (
      <div className="field-hint">
        No plans loaded — see “Plans unavailable” above.
      </div>
    );
  }
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {plans.map((p) => (
        <option key={p.slug} value={p.slug}>
          {p.name}
          {showPrice ? ` (${formatPaise(p.price_paise)}/seat/month)` : ""}
        </option>
      ))}
    </select>
  );
}

function reload() {
  // Re-read the server-rendered numbers after a successful write.
  window.location.reload();
}

function ActivatePanel({ slug, plans }: { slug: string; plans: CatalogPlan[] }) {
  const [plan, setPlan] = useState(plans[0]?.slug ?? "");
  const [seats, setSeats] = useState("5");
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
      <p className="muted">
        The customer has paid you (e.g. bank transfer) — record it and switch
        them from trial to their paid plan.
      </p>
      <label>Plan</label>
      <PlanPicker plans={plans} value={plan} onChange={setPlan} showPrice />
      <label>Seats</label>
      <input
        type="number"
        min={1}
        value={seats}
        onChange={(e) => setSeats(e.target.value)}
      />
      <div className="field-hint">How many people they are paying for.</div>
      <label>AI credits to include (optional)</label>
      <input
        value={credits}
        placeholder="e.g. 250"
        onChange={(e) => setCredits(e.target.value)}
      />
      <label>Payment reference (optional)</label>
      <input
        value={reference}
        placeholder="bank ref / invoice no."
        onChange={(e) => setReference(e.target.value)}
      />
      <button type="submit" disabled={busy || !plan}>
        {busy ? "Activating…" : "Activate"}
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
      <p className="muted">
        Assign a seat so a specific person can sign in; release it to free the
        seat for someone else. The customer&apos;s own admin can also do this
        inside the app.
      </p>
      <label>Plan</label>
      <PlanPicker plans={plans} value={plan} onChange={setPlan} />
      <label>Person&apos;s email</label>
      <input
        type="email"
        value={email}
        placeholder="person@customer.com"
        onChange={(e) => setEmail(e.target.value)}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          disabled={busy || !email}
          onClick={() => act("/api/operator/seats")}
        >
          Assign seat
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy || !email}
          onClick={() => act("/api/operator/seats/release")}
        >
          Release seat
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
      <p className="muted">
        Top up the balance the customer&apos;s AI usage draws from. Additions
        are logged; a correction is another entry, never an edit.
      </p>
      <label>Credits to add</label>
      <input
        value={credits}
        placeholder="e.g. 100"
        onChange={(e) => setCredits(e.target.value)}
      />
      <label>Reason</label>
      <select value={reason} onChange={(e) => setReason(e.target.value)}>
        <option value="grant">grant — included with their plan</option>
        <option value="manual">manual — they bought a top-up</option>
        <option value="adjustment">adjustment — correcting a mistake</option>
      </select>
      <label>Reference (optional)</label>
      <input
        value={ref}
        placeholder="invoice / note"
        onChange={(e) => setRef(e.target.value)}
      />
      <button type="submit" disabled={busy || !credits.trim()}>
        {busy ? "Adding…" : "Add credits"}
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
    if (
      target === "suspended" &&
      !window.confirm(
        `Suspend ${slug}?\n\nEvery sign-in for this customer will be refused until you resume them. Their data is untouched.`,
      )
    ) {
      return;
    }
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
      <h2 style={{ marginTop: 0 }}>Access</h2>
      <p className="muted">
        Suspending blocks every sign-in for this customer (e.g. non-payment).
        Their data is kept, and resuming restores access instantly.
      </p>
      <label>Reason (optional, kept in the log)</label>
      <input
        value={reason}
        placeholder="e.g. invoice 42 unpaid"
        onChange={(e) => setReason(e.target.value)}
      />
      {actions.length === 0 && (
        <p className="muted">No change available from this state.</p>
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
            {a.target === "suspended" ? "Suspend access" : "Resume access"}
          </button>
        ))}
      </div>
      <ResultLine result={result} />
    </form>
  );
}
