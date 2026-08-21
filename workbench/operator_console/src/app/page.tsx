import { redirect } from "next/navigation";
import { listOrganizations, ConsoleUnconfigured } from "@/lib/console";
import { staffSession } from "@/lib/session";
import {
  formatPaise,
  formatDate,
  seatsDigest,
  type OrgList,
  type OrgRow,
} from "@/lib/format";

export const dynamic = "force-dynamic";

function StatusPill({ status }: { status: string }) {
  return <span className={`pill ${status}`}>{status}</span>;
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

  let rows: OrgRow[] = [];
  let error: string | null = null;
  try {
    const res = await listOrganizations();
    if (res.status === 200) {
      rows = (JSON.parse(res.body) as OrgList).organizations;
    } else {
      error = `Console returned ${res.status}: ${res.body}`;
    }
  } catch (e) {
    error =
      e instanceof ConsoleUnconfigured
        ? "Customer Console is not configured (CUSTOMER_CONSOLE_URL / token)."
        : `Could not reach the Customer Console: ${String(e)}`;
  }

  return (
    <main className="wrap">
      <h1>Customers</h1>
      <p className="muted">
        Every organization on the platform — the §4.1a cross-org view. DARK: not
        deployed.
      </p>

      {error && <div className="banner">{error}</div>}

      {!error && (
        <table>
          <thead>
            <tr>
              <th>Company</th>
              <th>Status</th>
              <th>Subscription</th>
              <th>MRR</th>
              <th>Seats (assigned/purchased)</th>
              <th>Credits</th>
              <th>Trial ends</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  No organizations.
                </td>
              </tr>
            )}
            {rows.map((o) => (
              <tr key={o.slug}>
                <td>
                  <a href={`/customers/${encodeURIComponent(o.slug)}`}>
                    {o.name}
                  </a>
                  <div className="muted">{o.slug}</div>
                </td>
                <td>
                  <StatusPill status={o.status} />
                </td>
                <td>{o.subscription_status ?? "—"}</td>
                <td>{formatPaise(o.mrr_paise)}</td>
                <td>{seatsDigest(o.seats)}</td>
                <td>{o.credit_balance}</td>
                <td>{formatDate(o.trial_ends_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
