import { redirect } from "next/navigation";
import { listOrganizations, ConsoleUnconfigured } from "@/lib/console";
import { staffSession } from "@/lib/session";
import {
  formatPaise,
  partitionRoster,
  type OrgList,
  type OrgRow,
} from "@/lib/format";
import { rosterTotals } from "@/lib/roster";
import CustomerTable from "./CustomerTable";
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

// ⚠️ `StatusPill` and `SeatsCell` moved to `CustomerTable.tsx` with the rest of
// the row rendering. They are not duplicated — searching the roster needs
// client state, and a server component cannot hold a text box's value.

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

  const totals = rosterTotals(rows, new Date());

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
          {/* 🔴 MRR leads. It is the number the owner opens this page for and
              it was not on it — four lifecycle counts said how many customers
              exist and nothing said what they are worth. */}
          <div className="stat good">
            <div className="num">{formatPaise(totals.mrrPaise)}</div>
            <div className="lbl">MRR</div>
          </div>
          {/* Attention is a JOIN of facts the Console has no column for, so it
              cannot be a lifecycle count. See `lib/roster.ts`. */}
          <div className={`stat ${totals.needsAttention > 0 ? "caution" : ""}`}>
            <div className="num">{totals.needsAttention}</div>
            <div className="lbl">need attention</div>
          </div>
          <div className="stat">
            <div className="num">{totals.customers}</div>
            <div className="lbl">customers</div>
          </div>
          <div className="stat">
            <div className="num">{totals.active}</div>
            <div className="lbl">active</div>
          </div>
          <div className="stat">
            <div className="num">{totals.trial}</div>
            <div className="lbl">on trial</div>
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

      {!error && rows.length > 0 && <CustomerTable rows={rows} />}

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
