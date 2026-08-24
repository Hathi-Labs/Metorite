"use client";

import { useState } from "react";
import {
  canActivate,
  isSeated,
  lifecycleActions,
  formatPaise,
  memberTally,
  type CatalogPlan,
  type MemberRow,
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
  members,
  membersError,
}: {
  slug: string;
  status: string;
  subscriptionStatus: string | null;
  plans: CatalogPlan[];
  /** The customer's roster with seat state (LS-9). Empty = none arrived. */
  members: MemberRow[];
  /** Why the roster is empty, or null when it arrived. */
  membersError: string | null;
}) {
  return (
    <>
      <h2>Manage this customer</h2>
      {/* The roster comes FIRST, above the by-email form. An operator should be
          able to see who is unseated and click, rather than being asked for an
          address they have to be told (launch_surface.md §7). */}
      <MembersPanel
        slug={slug}
        plans={plans}
        members={members}
        membersError={membersError}
      />
      <div className="row">
        {canActivate(subscriptionStatus) && (
          <ActivatePanel slug={slug} plans={plans} />
        )}
        <SeatsPanel slug={slug} plans={plans} />
        <CreditsPanel slug={slug} />
        <LifecyclePanel slug={slug} status={status} />
      </div>
      {status === "deleted" && !/-purged-[0-9a-f]{6}$/.test(slug) && (
        <DangerPanel slug={slug} />
      )}
    </>
  );
}

/**
 * The customer's members, with seat state and both controls (LS-9 · §7).
 *
 * The gap this closes: the console could already assign and release a seat, but
 * only for an address an operator had typed — so "who in this org has no seat"
 * was a question we could not answer for a customer who asked it. The roster
 * and its seat state come from `GET /billing/summary`, i.e. from the SAME pair
 * of store reads the customer's own Organisation surface uses, so the two
 * cannot disagree.
 *
 * ⚠️ **Inviting a member stays customer-side, deliberately.** Seating somebody
 * who is not already a member is refused Console-side (no identity, no
 * membership), and we are not adding a door that creates people inside a
 * customer's directory with no customer in the loop.
 */
function MembersPanel({
  slug,
  plans,
  members,
  membersError,
}: {
  slug: string;
  plans: CatalogPlan[];
  members: MemberRow[];
  membersError: string | null;
}) {
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState("");
  // The one sellable plan under D49; read from the catalog rather than
  // hardcoded, so this follows the catalog if a second plan ever returns.
  const plan = plans[0]?.slug ?? "core";
  const counts = memberTally(members);

  async function act(email: string, path: string) {
    setBusy(email);
    const r = await post(path, { org_slug: slug, plan_slug: plan, email });
    setResult(r);
    setBusy("");
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={(e) => e.preventDefault()}>
      <h2 style={{ marginTop: 0 }}>Members &amp; seats</h2>
      {membersError ? (
        <p className="muted">
          <strong>Roster unavailable.</strong> {membersError} Seat assignment by
          email still works below.
        </p>
      ) : members.length === 0 ? (
        <p className="muted">This organization has no members yet.</p>
      ) : (
        <>
          <p className="muted">
            {counts.seated} of {counts.total} people seated ·{" "}
            {counts.unassigned} unassigned. Releasing a seat leaves the person on
            the roster so they can be reassigned.
          </p>
          <table>
            <tbody>
              {members.map((m) => {
                const seated = isSeated(m);
                return (
                  <tr key={m.email}>
                    <td>
                      {m.email}
                      {m.status !== "active" ? ` (${m.status})` : ""}
                    </td>
                    <td>{m.role}</td>
                    <td>
                      <strong>{seated ? "Seated" : "Unassigned"}</strong>
                      {seated && m.seats.length > 1
                        ? ` · ${m.seats.join(", ")}`
                        : ""}
                    </td>
                    <td>
                      <button
                        type="button"
                        className={seated ? "secondary" : undefined}
                        disabled={busy === m.email}
                        onClick={() =>
                          act(
                            m.email,
                            seated
                              ? "/api/operator/seats/release"
                              : "/api/operator/seats",
                          )
                        }
                      >
                        {busy === m.email
                          ? "…"
                          : seated
                            ? "Release"
                            : "Assign seat"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      <ResultLine result={result} />
    </form>
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

  // The two offboarding edges get their own confirm copy — each states what
  // the move does and does NOT do, because "cancel" destroying data is the
  // misread the export window exists to prevent.
  const CONFIRMS: Record<string, string> = {
    suspended:
      `Suspend ${slug}?\n\nEvery sign-in for this customer will be refused ` +
      `until you resume them. Their data is untouched.`,
    cancelled:
      `Cancel ${slug}?\n\nThis opens their export window: sign-in keeps ` +
      `working so they can export, features are locked, and NO data is ` +
      `deleted. You can reinstate them at any time inside the window.`,
    deleted:
      `Mark ${slug} deleted?\n\nThis ends the export window and refuses ` +
      `every sign-in. Their data still exists until you run the purge — ` +
      `which becomes available after this step and is IRREVERSIBLE.`,
  };

  async function move(target: string) {
    if (CONFIRMS[target] && !window.confirm(CONFIRMS[target])) {
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
        Whether this customer&apos;s account is live. Activating takes them off
        trial; suspending blocks every sign-in (e.g. non-payment). Their data is
        kept either way, and resuming restores access instantly.
      </p>
      <p className="muted">
        Separate from their subscription — activating a plan records the money
        and leaves the account where it is.
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
            className={
              ["suspended", "cancelled", "deleted"].includes(a.target)
                ? "danger"
                : undefined
            }
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

/**
 * The purge — the one control in this console that destroys data. (CP-2g)
 *
 * Rendered ONLY in the `deleted` state, which the lifecycle graph makes
 * reachable only through `cancelled` (the export window) — so by the time
 * this panel exists, the doctrine's steps have all been walked — and NEVER
 * for a tombstone row (a purged org stays listed at `status=deleted`; the
 * Console door also refuses tombstones server-side). The typed slug is
 * carried to the server as `confirm` and re-checked at every layer (BFF,
 * gateway door, Console door): the confirmation is a protocol, not a UI
 * nicety.
 *
 * The receipt is RENDERED, not collapsed to "✓ Done" — the deleted/scrubbed/
 * kept counts are the operator's only record of what happened, and the two
 * silent-no-op failure modes review round 1 found (wrong box, case-mismatched
 * slug) are visible only in that receipt.
 */
function DangerPanel({ slug }: { slug: string }) {
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(acceptAbsent: boolean) {
    setBusy(true);
    const r = await post("/api/operator/purge", {
      org_slug: slug,
      confirm: typed,
      ...(acceptAbsent ? { accept_absent: true } : {}),
    });
    setBusy(false);
    if (r?.ok) {
      setResult(null);
      try {
        setReceipt(JSON.stringify(JSON.parse(r.text), null, 2));
      } catch {
        setReceipt(r.text);
      }
      return;
    }
    // The wrong-box / already-purged fork: the server refuses to finish the
    // registry half until a human decides which case this is.
    if (!acceptAbsent && r && r.text.includes('"needs_accept_absent"')) {
      if (
        window.confirm(
          `The tenant plane reports NO organization "${slug}".\n\n` +
            `If a previous purge already destroyed it, OK finishes the ` +
            `registry half.\n\nIf you are not CERTAIN of that, Cancel and ` +
            `check which deployment this organization lives on first — ` +
            `continuing would erase the record of where its data is.`,
        )
      ) {
        await run(true);
        return;
      }
    }
    setResult(r);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (typed !== slug) return;
    if (
      !window.confirm(
        `Purge ${slug} permanently?\n\nThis destroys the customer's ` +
          `organization data: memberships and people, projects and tasks, ` +
          `roles, settings and integrations — and strips their people from ` +
          `the registry. Billing history is kept. There is NO undo and NO ` +
          `backup restore for this.\n\nNot yet covered (apps that predate ` +
          `per-organization scoping): chat, email, WhatsApp, meetings, GTD ` +
          `and CRM data.`,
      )
    ) {
      return;
    }
    await run(false);
  }

  if (receipt !== null) {
    return (
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Purged</h2>
        <p className="muted">
          Keep this receipt — it is the record of what was destroyed, what was
          scrubbed and what the books kept. <strong>{slug}</strong> is free
          for reuse.
        </p>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{receipt}</pre>
      </div>
    );
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>Purge data permanently</h2>
      <p className="muted">
        The export window is over and sign-in is refused. This last step
        destroys the customer&apos;s organization data and frees{" "}
        <strong>{slug}</strong> for reuse. Billing history is kept.
      </p>
      <label>Type the organization slug to arm the button</label>
      <input
        value={typed}
        placeholder={slug}
        onChange={(e) => setTyped(e.target.value)}
      />
      <button
        type="submit"
        className="danger"
        disabled={busy || typed !== slug}
      >
        {busy ? "Purging…" : "Purge data permanently"}
      </button>
      <ResultLine result={result} />
    </form>
  );
}
