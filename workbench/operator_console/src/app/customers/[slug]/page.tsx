import { redirect } from "next/navigation";
import { categoricalBox, providerGlyph } from "@/lib/categorical";
import {
  listOrganizations,
  catalog,
  billingSummary,
  listKeys,
  creditLedger,
  ConsoleUnconfigured,
} from "@/lib/console";
import { staffSession } from "@/lib/session";
import {
  formatPaise,
  formatDate,
  seatsTotals,
  trialHint,
  statusHelp,
  plansNotice,
  lifecycleHint,
  readMembers,
  readKeys,
  readLedger,
  ledgerAdds,
  type MemberRow,
  type KeyRow,
  type LedgerRow,
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
  /** The org's roster with seat state (LS-9). Empty when none arrived. */
  members: MemberRow[];
  /**
   * Why the roster is empty, or null when it arrived.
   *
   * Kept SEPARATE from `error` for `plansError`'s reason: a failed summary read
   * must not blank the page — the org's numbers come from `/orgs` and the
   * by-email seat form still works — but it must not be silent either, or an
   * operator reads "no members" off a failed request.
   */
  membersError: string | null;
  /** The org's `cc_live_` keys, metadata only (CP-11 s1). Empty = none arrived. */
  keys: KeyRow[];
  /**
   * Why the key list is empty, or null when it arrived.
   *
   * ⚠️ Separate from `error` for the reason `membersError` is, with one extra
   * edge: `GET /keys` is `viewer`-readable, so a 403 here means the CONSOLE
   * refused the caller — not that the customer has no keys. Rendering those two
   * states the same way would tell an operator a leaked key does not exist.
   */
  keysError: string | null;
  /** The credit ledger, newest first - the rows a bank transfer is verified
   *  against BEFORE granting. Empty = none arrived. */
  ledger: LedgerRow[];
  /** Why the ledger is empty, or null when it arrived. A Console predating
   *  the read answers 404; that is "this build cannot show it", never
   *  "no entries". */
  ledgerError: string | null;
  error: string | null;
};

async function loadOrg(slug: string, authToken?: string): Promise<Loaded> {
  try {
    // Four reads in parallel, all operator-door: the cross-org list (this
    // org's numbers), the catalog (the plan pickers), the per-org summary
    // (the roster with seat state, LS-9) and the org's `cc_live_` keys (CP-11).
    // ⚠️ All four carry the CALLER's session. A read that dropped it would
    // reach the Console as `breakglass` — past the role matrix, and logged
    // as a break-glass event on every page view.
    const d = { authToken };
    const [listRes, catRes, sumRes, keysRes, ledgerRes] = await Promise.all([
      listOrganizations(d),
      catalog(d),
      billingSummary(slug, d),
      listKeys(slug, d),
      creditLedger(slug, d),
    ]);
    if (listRes.status !== 200) {
      return {
        org: null,
        plans: [],
        plansError: null,
        members: [],
        membersError: null,
        keys: [],
        keysError: null,
        ledger: [],
        ledgerError: null,
        error: `Console returned ${listRes.status}`,
      };
    }
    const orgs = (JSON.parse(listRes.body) as OrgList).organizations;
    const org = orgs.find((o) => o.slug === slug) ?? null;
    const plans =
      catRes.status === 200 ? (JSON.parse(catRes.body) as Catalog).plans : [];
    let members: MemberRow[] = [];
    let membersError: string | null = null;
    if (sumRes.status !== 200) {
      membersError = `The summary read returned ${sumRes.status}.`;
    } else {
      try {
        members = readMembers(JSON.parse(sumRes.body));
      } catch {
        membersError = "The summary read could not be parsed.";
      }
      // A Console predating LS-9 answers 200 with no `members` key. That is not
      // "this customer has no members" — say which it is.
      if (!membersError && members.length === 0) {
        membersError =
          "This Console build does not report members (it predates the seat roster).";
      }
    }
    let keys: KeyRow[] = [];
    let keysError: string | null = null;
    if (keysRes.status !== 200) {
      // ⚠️ Say WHICH failure this is. "No keys" and "the Console would not tell
      // me" look identical in an empty list, and the operator reading this
      // surface may be trying to revoke a key that has leaked.
      keysError = `The key list returned ${keysRes.status}.`;
    } else {
      try {
        keys = readKeys(JSON.parse(keysRes.body));
      } catch {
        keysError = "The key list could not be parsed.";
      }
    }

    let ledger: LedgerRow[] = [];
    let ledgerError: string | null = null;
    if (ledgerRes.status !== 200) {
      ledgerError = `The ledger read returned ${ledgerRes.status}.`;
    } else {
      try {
        ledger = readLedger(JSON.parse(ledgerRes.body));
      } catch {
        ledgerError = "The ledger read could not be parsed.";
      }
    }

    return {
      org,
      plans,
      plansError: plansNotice(catRes.status, plans.length),
      members,
      membersError,
      keys,
      keysError,
      ledger,
      ledgerError,
      error: null,
    };
  } catch (e) {
    return {
      org: null,
      plans: [],
      plansError: null,
      members: [],
      membersError: null,
      keys: [],
      keysError: null,
      ledger: [],
      ledgerError: null,
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
  const {
    org, plans, plansError, members, membersError, keys, keysError,
    ledger, ledgerError, error,
  } = await loadOrg(slug, gate.authToken);

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
        <div className="orghero">
          <span
            className={`${categoricalBox(org.name)} lg`}
            aria-hidden="true"
          >
            {providerGlyph(org.name)}
          </span>
          <div>
            <h1>{org.name}</h1>
            <div className="herochips">
              <span className={`pill ${org.status}`}>
                {org.status.replace("_", " ")}
              </span>
              <span className="chip mono">{org.slug}</span>
            </div>
          </div>
        </div>
      </div>

      {org.status === "suspended" && (
        <div className="banner danger">
          <strong>Suspended.</strong> Sign-in still works so they can pay —
          AI and seat changes are locked. Use “Resume access” below to
          restore them.
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

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Credit ledger</h2>
        <p className="muted">
          Every addition and draw, newest first. Verify a bank transfer HERE
          before granting - a reference already on this list is already
          credited, and the grant form will refuse it.
        </p>
        {ledgerError ? (
          <p className="muted small">{ledgerError}</p>
        ) : ledger.length === 0 ? (
          <p className="muted small">
            No entries yet. The first grant starts the history.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Change</th>
                <th>Reason</th>
                <th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((row, i) => (
                <tr key={`${row.created_at}-${i}`}>
                  <td className="muted small">{formatDate(row.created_at)}</td>
                  <td className={ledgerAdds(row) ? "ok-t" : ""}>
                    {ledgerAdds(row) ? `+${row.delta}` : row.delta}
                  </td>
                  <td>{row.reason}</td>
                  <td className="mono small">{row.ref ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Actions
        slug={org.slug}
        status={org.status}
        subscriptionStatus={org.subscription_status}
        plans={plans}
        members={members}
        membersError={membersError}
        keys={keys}
        keysError={keysError}
      />
    </main>
  );
}
