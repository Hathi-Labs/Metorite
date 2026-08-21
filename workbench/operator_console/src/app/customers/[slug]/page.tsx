import { redirect } from "next/navigation";
import { listOrganizations, catalog, ConsoleUnconfigured } from "@/lib/console";
import { staffSession } from "@/lib/session";
import {
  formatPaise,
  formatDate,
  type OrgList,
  type OrgRow,
  type Catalog,
  type CatalogPlan,
} from "@/lib/format";
import Actions from "./Actions";

export const dynamic = "force-dynamic";

async function loadOrg(
  slug: string,
): Promise<{ org: OrgRow | null; plans: CatalogPlan[]; error: string | null }> {
  try {
    const [listRes, catRes] = await Promise.all([
      listOrganizations(),
      catalog(),
    ]);
    if (listRes.status !== 200) {
      return { org: null, plans: [], error: `Console returned ${listRes.status}` };
    }
    const orgs = (JSON.parse(listRes.body) as OrgList).organizations;
    const org = orgs.find((o) => o.slug === slug) ?? null;
    const plans =
      catRes.status === 200 ? (JSON.parse(catRes.body) as Catalog).plans : [];
    return { org, plans, error: null };
  } catch (e) {
    return {
      org: null,
      plans: [],
      error:
        e instanceof ConsoleUnconfigured
          ? "Customer Console is not configured."
          : `Could not reach the Customer Console: ${String(e)}`,
    };
  }
}

export default async function CustomerDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const gate = await staffSession();
  if (!gate.configured) redirect("/");
  if (!gate.ok) redirect("/login");

  const { slug } = await params;
  const { org, plans, error } = await loadOrg(slug);

  if (error) {
    return (
      <main className="wrap">
        <p>
          <a href="/">← Customers</a>
        </p>
        <div className="banner">{error}</div>
      </main>
    );
  }
  if (!org) {
    return (
      <main className="wrap">
        <p>
          <a href="/">← Customers</a>
        </p>
        <div className="banner">No organization {slug}.</div>
      </main>
    );
  }

  return (
    <main className="wrap">
      <p>
        <a href="/">← Customers</a>
      </p>
      <h1>{org.name}</h1>
      <p className="muted">{org.slug}</p>

      <div className="row">
        <div className="panel">
          <div>
            Lifecycle: <span className={`pill ${org.status}`}>{org.status}</span>
          </div>
          <div>Subscription: {org.subscription_status ?? "—"}</div>
          <div>Provider: {org.provider ?? "—"}</div>
          <div>MRR: {formatPaise(org.mrr_paise)}</div>
          <div>Credit balance: {org.credit_balance}</div>
          <div>Trial ends: {formatDate(org.trial_ends_at)}</div>
          <div>Period ends: {formatDate(org.current_period_end)}</div>
        </div>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Seats</h2>
          <table>
            <thead>
              <tr>
                <th>Plan</th>
                <th>Purchased</th>
                <th>Assigned</th>
                <th>Available</th>
              </tr>
            </thead>
            <tbody>
              {org.seats.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No seats.
                  </td>
                </tr>
              )}
              {org.seats.map((s) => (
                <tr key={s.plan_slug}>
                  <td>{s.plan_slug}</td>
                  <td>{s.purchased}</td>
                  <td>
                    {s.assigned}
                    {s.oversubscribed ? " ⚠" : ""}
                  </td>
                  <td>{s.available}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Actions
        slug={org.slug}
        status={org.status}
        subscriptionStatus={org.subscription_status}
        plans={plans}
      />
    </main>
  );
}
