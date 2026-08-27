import { redirect } from "next/navigation";
import { listOrganizations, ConsoleUnconfigured } from "@/lib/console";
import { staffSession } from "@/lib/session";
import {
  formatPaise,
  formatDate,
  partitionRoster,
  seatsTotals,
  trialHint,
  statusHelp,
  type OrgList,
  type OrgRow,
} from "@/lib/format";
import NewCustomer from "./NewCustomer";
import Header from "./Header";

export const dynamic = "force-dynamic";

// The deployment the operator most often provisions onto — a prefilled default
// for the "New customer" form's `deployment_label`, NOT a hardcode and NOT a
// sole-deployment inference (both forbidden by name, D46.6 items 1 & 3). Read
// server-side; when unset the field is empty and the operator types it (the
// box's value is `gateway`). A wrong label is caught by the Console's 404.
function defaultDeploymentLabel(): string {
  return (process.env.OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL ?? "").trim();
}

function StatusPill({ status }: { status: string }) {
  // title= carries the plain-language meaning so hovering a pill answers
  // "what does this mean?" without a docs page.
  return (
    <span className={`pill ${status}`} title={statusHelp(status)}>
      {status.replace("_", " ")}
    </span>
  );
}

function SeatsCell({ org }: { org: OrgRow }) {
  const totals = seatsTotals(org.seats);
  if (!totals) return <span className="muted">—</span>;
  const pct =
    totals.purchased > 0
      ? Math.min(100, Math.round((totals.assigned / totals.purchased) * 100))
      : 0;
  return (
    <div className="seatcell">
      <div>
        {totals.assigned} of {totals.purchased} used
        {totals.oversubscribed && (
          <span className="warnbadge" title="More seats assigned than purchased">
            over
          </span>
        )}
      </div>
      <div className="bar" aria-hidden="true">
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default async function CustomersPage() {
  const gate = await staffSession();
  if (!gate.configured) {
    return (
      <main className="wrap">
        <h1>Operator Console</h1>
        <div className="banner">
          The staff gate is not configured. Set{" "}
          <code>OPERATOR_CONSOLE_STAFF_SECRET</code> (interim) — the staff Entra
          directory is the owner follow-up (D35.3).
        </div>
      </main>
    );
  }
  if (!gate.ok) redirect("/login");

  let all: OrgRow[] = [];
  let error: string | null = null;
  try {
    // ⚠️ The CALLER's session, not the shared token. Without it this read
    // reaches the Console as `breakglass`, which bypasses the role matrix
    // and logs a warning on every page view.
    const res = await listOrganizations({ authToken: gate.authToken });
    if (res.status === 200) {
      all = (JSON.parse(res.body) as OrgList).organizations;
    } else {
      error = `Console returned ${res.status}: ${res.body}`;
    }
  } catch (e) {
    error =
      e instanceof ConsoleUnconfigured
        ? "Customer Console is not configured (CUSTOMER_CONSOLE_URL / token)."
        : `Could not reach the Customer Console: ${String(e)}`;
  }

  // Tombstones are bookkeeping, not customers — they leave the roster, the
  // headline numbers and the empty-state decision, and reappear only in the
  // collapsed section at the bottom.
  const { roster: rows, purged } = partitionRoster(all);

  const now = new Date();
  const count = (s: string) => rows.filter((o) => o.status === s).length;

  return (
    <main className="wrap">
      <Header />
      <div className="pagehead">
        <div>
          <h1>Customers</h1>
          <p className="muted">
            Every customer organization on Metorite. Click one to manage its
            plan, seats and AI credits.
          </p>
        </div>
        <NewCustomer defaultDeploymentLabel={defaultDeploymentLabel()} />
      </div>

      {error && <div className="banner">{error}</div>}

      {!error && rows.length > 0 && (
        <div className="stats">
          <div className="stat">
            <div className="num">{rows.length}</div>
            <div className="lbl">customers</div>
          </div>
          <div className="stat">
            <div className="num ok-t">{count("active")}</div>
            <div className="lbl">active</div>
          </div>
          <div className="stat">
            <div className="num accent-t">{count("trial")}</div>
            <div className="lbl">on trial</div>
          </div>
          <div className="stat">
            <div className="num warn-t">{count("suspended")}</div>
            <div className="lbl">suspended</div>
          </div>
        </div>
      )}

      {!error && rows.length === 0 && (
        <div className="empty">
          <h2>No customers yet</h2>
          <p className="muted">Create your first customer. What happens next:</p>
          <ol className="muted">
            <li>
              You enter their company name, the owner&apos;s email, and how many
              seats they get — a free trial starts immediately.
            </li>
            <li>
              The owner signs in at <strong>app.metorite.com</strong> with
              Google using that email — no invite link needed.
            </li>
            <li>
              When they&apos;ve paid, open the customer here and{" "}
              <strong>activate their plan</strong>.
            </li>
          </ol>
        </div>
      )}

      {!error && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Status</th>
              <th>Subscription</th>
              <th>MRR</th>
              <th>Seats</th>
              <th>AI credits</th>
              <th>Trial</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.slug}>
                <td>
                  <a href={`/customers/${encodeURIComponent(o.slug)}`}>
                    {o.name}
                  </a>
                  <div className="muted small">{o.slug}</div>
                </td>
                <td>
                  <StatusPill status={o.status} />
                </td>
                <td>{o.subscription_status ?? <span className="muted">none</span>}</td>
                <td>{formatPaise(o.mrr_paise)}</td>
                <td>
                  <SeatsCell org={o} />
                </td>
                <td>{o.credit_balance}</td>
                <td>
                  {formatDate(o.trial_ends_at)}
                  {o.status === "trial" && trialHint(o.trial_ends_at, now) && (
                    <div className="muted small">
                      {trialHint(o.trial_ends_at, now)}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!error && purged.length > 0 && (
        <details style={{ marginTop: 24 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>
            {purged.length} purged{" "}
            {purged.length === 1 ? "organization" : "organizations"} — data
            destroyed; the record is kept for billing history
          </summary>
          <table>
            <tbody>
              {purged.map((o) => (
                <tr key={o.slug}>
                  <td>
                    <a href={`/customers/${encodeURIComponent(o.slug)}`}>
                      {o.name}
                    </a>
                    <div className="muted small">{o.slug}</div>
                  </td>
                  <td className="muted">purged</td>
                  <td className="muted">{formatPaise(o.mrr_paise)} was MRR</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </main>
  );
}
