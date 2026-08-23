import { redirect } from "next/navigation";
import { listOrganizations, catalog, ConsoleUnconfigured } from "@/lib/console";
import { staffSession } from "@/lib/session";
import {
  formatPaise,
  formatDate,
  seatsTotals,
  trialHint,
  statusHelp,
  plansNotice,
  lifecycleHint,
  type OrgList,
  type OrgRow,
  type Catalog,
  type CatalogPlan,
} from "@/lib/format";
import Actions from "./Actions";
import Header from "../../Header";

export const dynamic = "force-dynamic";

type Loaded = {
  org: OrgRow | null;
  plans: CatalogPlan[];
  // Why the Plan pickers are empty, or null when the ladder arrived. Kept
  // SEPARATE from `error`: a failed catalog read must not blank the page — the
  // org's numbers are fine and the credit/suspend actions still work — but it
  // must never be silent either, which is what folding it into `plans: []`
  // did (see `plansNotice`).
  plansError: string | null;
  error: string | null;
};

async function loadOrg(slug: string): Promise<Loaded> {
  try {
    const [listRes, catRes] = await Promise.all([
      listOrganizations(),
      catalog(),
    ]);
    if (listRes.status !== 200) {
      return {
        org: null,
        plans: [],
        plansError: null,
        error: `Console returned ${listRes.status}`,
      };
    }
    const orgs = (JSON.parse(listRes.body) as OrgList).organizations;
    const org = orgs.find((o) => o.slug === slug) ?? null;
    const plans =
      catRes.status === 200 ? (JSON.parse(catRes.body) as Catalog).plans : [];
    return {
      org,
      plans,
      plansError: plansNotice(catRes.status, plans.length),
      error: null,
    };
  } catch (e) {
    return {
      org: null,
      plans: [],
      plansError: null,
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
  const { org, plans, plansError, error } = await loadOrg(slug);

  if (error) {
    return (
      <main className="wrap">
        <Header />
        <p>
          <a href="/">← All customers</a>
        </p>
        <div className="banner">{error}</div>
      </main>
    );
  }
  if (!org) {
    return (
      <main className="wrap">
        <Header />
        <p>
          <a href="/">← All customers</a>
        </p>
        <div className="banner">No organization “{slug}”.</div>
      </main>
    );
  }

  const now = new Date();
  const totals = seatsTotals(org.seats);
  const seatPct =
    totals && totals.purchased > 0
      ? Math.min(100, Math.round((totals.assigned / totals.purchased) * 100))
      : 0;
  const hint = trialHint(org.trial_ends_at, now);
  // The trial banner's advice is WRONG once the subscription is already active
  // — "use Activate subscription" is the step they just did. See lifecycleHint.
  const lifeHint = lifecycleHint(org.status, org.subscription_status);

  return (
    <main className="wrap">
      <Header />
      <p>
        <a href="/">← All customers</a>
      </p>
      <div className="pagehead">
        <div>
          <h1>{org.name}</h1>
          <p className="muted">
            {org.slug} ·{" "}
            <span className={`pill ${org.status}`}>{org.status.replace("_", " ")}</span>
          </p>
        </div>
      </div>

      {org.status === "suspended" && (
        <div className="banner danger">
          <strong>Suspended.</strong> Every sign-in for this customer is refused.
          Use “Resume access” below to restore it.
        </div>
      )}
      {org.status === "trial" && (
        <div className="banner info">
          <strong>On free trial</strong>
          {hint ? ` — ${hint}` : ""}.{" "}
          {lifeHint ?? (
            <>
              When the customer has paid, use{" "}
              <strong>Activate subscription</strong> below to put them on their
              paid plan.
            </>
          )}
        </div>
      )}
      {statusHelp(org.status) &&
        org.status !== "suspended" &&
        org.status !== "trial" && (
          <p className="muted">{statusHelp(org.status)}</p>
        )}

      <div className="stats">
        <div className="stat">
          <div className="lbl">Subscription</div>
          <div className="num small-num">
            {org.subscription_status ?? "none"}
          </div>
          <div className="muted small">
            {org.provider ? `via ${org.provider} · ` : ""}
            {formatPaise(org.mrr_paise)}/month
          </div>
        </div>
        <div className="stat">
          <div className="lbl">Seats</div>
          <div className="num small-num">
            {totals ? `${totals.assigned} of ${totals.purchased}` : "—"}
          </div>
          <div className="bar" aria-hidden="true">
            <i style={{ width: `${seatPct}%` }} />
          </div>
          {totals?.oversubscribed && (
            <div className="warn-t small">More assigned than purchased</div>
          )}
        </div>
        <div className="stat">
          <div className="lbl">AI credits</div>
          <div className="num small-num">{org.credit_balance}</div>
        </div>
        <div className="stat">
          <div className="lbl">Dates</div>
          <div className="muted small">
            Trial ends: {formatDate(org.trial_ends_at)}
            <br />
            Period ends: {formatDate(org.current_period_end)}
          </div>
        </div>
      </div>

      {org.seats.length > 1 && (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Seats by plan</h2>
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
      )}

      {plansError && (
        <div className="banner danger">
          <strong>Plans unavailable.</strong> {plansError} Seats, AI credits and
          access still work below.
        </div>
      )}

      <Actions
        slug={org.slug}
        status={org.status}
        subscriptionStatus={org.subscription_status}
        plans={plans}
      />
    </main>
  );
}
