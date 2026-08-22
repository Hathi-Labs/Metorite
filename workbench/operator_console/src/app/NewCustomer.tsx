"use client";

import { useState } from "react";

// The "provision a new customer" ACTION on the customers list. Create-only: it
// POSTs to the server-side `/api/operator/provision` BFF route, which holds the
// operator token — NOTHING here holds a credential. The Console is the authority
// on every refusal (400 missing deployment_label, 404 unknown label, 409 already
// placed elsewhere); this UI relays whatever it answers rather than pre-judging
// it. Activate / seats / lifecycle already live on the customer-detail page, so
// after a 200 we simply reload the list where the new org now appears (provision
// grants Core seats + a trial sub + placement, so it is immediately manageable
// via the existing detail page).

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

export default function NewCustomer({
  defaultDeploymentLabel,
}: {
  defaultDeploymentLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  // Named by the operator, and named by NOBODY else (D46.6 item 1): the operator
  // credential is cross-org and carries no deployment identity, so the box has to
  // be said out loud. Editable free-text, prefilled from
  // OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL (the box's value is `gateway`); a
  // wrong label is handled by the Console's relayed 404, never inferred here.
  const [deploymentLabel, setDeploymentLabel] = useState(defaultDeploymentLabel);
  const [gstin, setGstin] = useState("");
  const [billingState, setBillingState] = useState("");
  const [coreSeats, setCoreSeats] = useState("1");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body: Record<string, unknown> = {
      slug: slug.trim(),
      name: name.trim(),
      owner_email: ownerEmail.trim(),
      deployment_label: deploymentLabel.trim(),
      core_seats: Number(coreSeats),
    };
    if (gstin.trim()) body.gstin = gstin.trim();
    if (billingState.trim()) body.billing_state = billingState.trim();
    const r = await post("/api/operator/provision", body);
    setResult(r);
    setBusy(false);
    // On success the new org is placed + seated + trialling; reload the list so
    // it appears and is manageable via the existing detail page.
    if (r?.ok) window.location.reload();
  }

  const canSubmit =
    slug.trim().length > 0 &&
    name.trim().length > 0 &&
    ownerEmail.trim().length > 0 &&
    deploymentLabel.trim().length > 0 &&
    Number.isInteger(Number(coreSeats)) &&
    Number(coreSeats) >= 1;

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}>
        New customer
      </button>
    );
  }

  return (
    <form className="panel" onSubmit={submit} style={{ maxWidth: 480 }}>
      <h2 style={{ marginTop: 0 }}>Provision a new customer</h2>
      <p className="muted">
        Creates the organization, its owner and Core seats, places it on the
        named deployment and starts a trial. Idempotent on the slug.
      </p>
      <label>Slug</label>
      <input value={slug} onChange={(e) => setSlug(e.target.value)} />
      <label>Company name</label>
      <input value={name} onChange={(e) => setName(e.target.value)} />
      <label>Owner email</label>
      <input value={ownerEmail} onChange={(e) => setOwnerEmail(e.target.value)} />
      <label>Deployment label</label>
      <input
        value={deploymentLabel}
        onChange={(e) => setDeploymentLabel(e.target.value)}
      />
      <label>Core seats</label>
      <input
        type="number"
        min={1}
        value={coreSeats}
        onChange={(e) => setCoreSeats(e.target.value)}
      />
      <label>GSTIN (optional)</label>
      <input value={gstin} onChange={(e) => setGstin(e.target.value)} />
      <label>Billing state (optional)</label>
      <input
        value={billingState}
        onChange={(e) => setBillingState(e.target.value)}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" disabled={busy || !canSubmit}>
          Provision
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => setOpen(false)}
        >
          Cancel
        </button>
      </div>
      <ResultLine result={result} />
    </form>
  );
}
