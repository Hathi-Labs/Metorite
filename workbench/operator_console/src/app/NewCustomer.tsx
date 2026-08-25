"use client";

import { useState } from "react";
import { suggestSlug } from "@/lib/format";

// The "create a new customer" ACTION on the customers list. Create-only: it
// POSTs to the server-side `/api/operator/provision` BFF route, which holds the
// operator token — NOTHING here holds a credential. The Console is the authority
// on every refusal (400 missing deployment_label, 404 unknown label, 409 already
// placed elsewhere); this UI relays whatever it answers rather than pre-judging
// it. On success we show the operator what to tell the customer, then reload
// the list where the new org now appears (provision grants Core seats + a trial
// sub + placement, so it is immediately manageable via the detail page).

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

export default function NewCustomer({
  defaultDeploymentLabel,
}: {
  defaultDeploymentLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
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
  const [coreSeats, setCoreSeats] = useState("5");
  const [result, setResult] = useState<Result>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  function setNameAndMaybeSlug(v: string) {
    setName(v);
    // Autofill the slug from the company name until the operator edits it
    // themselves — advisory only, the Console stays the authority on validity.
    if (!slugTouched) setSlug(suggestSlug(v));
  }

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
    if (r?.ok) setDone(true);
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
      <button type="button" className="primary-cta" onClick={() => setOpen(true)}>
        + New customer
      </button>
    );
  }

  if (done) {
    return (
      <div className="panel success-panel">
        <h2 style={{ marginTop: 0 }}>✓ Customer created</h2>
        <p>
          <strong>{name.trim()}</strong> is set up with {coreSeats} seat
          {Number(coreSeats) === 1 ? "" : "s"} on a free trial.
        </p>
        <p className="muted">What to tell them:</p>
        <ul className="muted">
          <li>
            <strong>{ownerEmail.trim()}</strong> signs in at{" "}
            <strong>https://app.metorite.com</strong> with Google — no invite
            link needed.
          </li>
          <li>
            They can add their own people from Settings → Billing inside the
            app, up to their seat count.
          </li>
        </ul>
        <p className="muted">
          When they&apos;ve paid, open the customer and{" "}
          <strong>activate their plan</strong>.
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          Done
        </button>
      </div>
    );
  }

  return (
    <form className="panel form-panel" onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>New customer</h2>
      <p className="muted">
        Creates the company with its owner and seats, and starts a free trial.
        The owner can sign in immediately. Safe to retry — creating the same
        company twice converges on one.
      </p>

      <label htmlFor="nc-name">Company name</label>
      <input
        id="nc-name"
        value={name}
        autoFocus
        placeholder="Fracktal Works"
        onChange={(e) => setNameAndMaybeSlug(e.target.value)}
      />

      <label htmlFor="nc-slug">Short ID (slug)</label>
      <input
        id="nc-slug"
        value={slug}
        placeholder="fracktal-works"
        onChange={(e) => {
          setSlugTouched(true);
          setSlug(e.target.value);
        }}
      />
      <div className="field-hint">
        Lowercase letters, numbers and hyphens. Used in URLs and reports —
        cannot be changed later.
      </div>

      <label htmlFor="nc-email">Owner&apos;s email</label>
      <input
        id="nc-email"
        type="email"
        value={ownerEmail}
        placeholder="admin@customer.com"
        onChange={(e) => setOwnerEmail(e.target.value)}
      />
      <div className="field-hint">
        The Google account your customer&apos;s admin will sign in with.
      </div>

      <label htmlFor="nc-seats">Seats</label>
      <input
        id="nc-seats"
        type="number"
        min={1}
        value={coreSeats}
        onChange={(e) => setCoreSeats(e.target.value)}
      />
      <div className="field-hint">
        How many people can use the app. You can change this later when they
        subscribe.
      </div>

      <details className="advanced">
        <summary>Billing &amp; advanced</summary>
        <label htmlFor="nc-gstin">GSTIN (optional)</label>
        <input
          id="nc-gstin"
          value={gstin}
          onChange={(e) => setGstin(e.target.value)}
        />
        <label htmlFor="nc-state">Billing state (optional)</label>
        <input
          id="nc-state"
          value={billingState}
          onChange={(e) => setBillingState(e.target.value)}
        />
        <label htmlFor="nc-depl">Deployment</label>
        <input
          id="nc-depl"
          value={deploymentLabel}
          onChange={(e) => setDeploymentLabel(e.target.value)}
        />
        <div className="field-hint">
          Which server this customer runs on. Leave the default unless you know
          otherwise.
        </div>
      </details>

      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" disabled={busy || !canSubmit}>
          {busy ? "Creating…" : "Create customer"}
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
      {result && !result.ok && (
        <div className="result err">
          Could not create the customer. The Console said:
          {"\n"}
          {result.text}
        </div>
      )}
    </form>
  );
}
