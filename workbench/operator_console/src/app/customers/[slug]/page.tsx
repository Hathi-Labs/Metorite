import { redirect } from "next/navigation";
import { categoricalBox, providerGlyph } from "@/lib/categorical";
import {
  listOrganizations,
  catalog,
  billingSummary,
  listKeys,
  creditLedger,
  orgUsage,
  usageBreakdown,
  usageDaily,
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
  readMembers,
  readKeys,
  readLedger,
  ledgerAdds,
  readCreditLots,
  LOT_SOURCE_LABEL,
  LEDGER_REASON_WORDS,
  subscriptionWords,
  type CreditLot,
  type MemberRow,
  type KeyRow,
  type LedgerRow,
  type OrgList,
  type OrgRow,
  type Catalog,
  type CatalogPlan,
} from "@/lib/format";
import { customerMoney, formatCr, formatInr, priceFrom, type Price } from "@/lib/money";
import { lifecycleSteps, nextStep, tabFrom, TABS, type TabKey } from "@/lib/lifecycle";
import { consoleQuery, rangeFrom, type UsageRange } from "@/lib/range";
import RangePicker from "../../RangePicker";
import {
  readBreakdown,
  type OrgUsageRow,
  type OrgUsageView,
  type UsageBreakdown,
  type UsageDay,
} from "@/lib/usage";
import { AccessActions, BillingActions, PeopleActions } from "./Actions";
import CustomerBreakdown from "./CustomerBreakdown";
import CustomerUsage from "./CustomerUsage";
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
  /** What the balance is MADE OF (migration 028), in the order lots burn.
   *
   * ⚠️ **`undefined` means the Console sent no `credit_lots` key**, which is a
   *  Console predating the migration. That is NOT "this customer has no lots",
   *  and the panel says which it is rather than drawing an empty table over a
   *  missing feature. */
  lots?: CreditLot[];
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
  /** H-133 — what this customer SPENT, judged by `lib/usage.ts`.
   *
   * ⚠️ `null` means the window holds no metered traffic for them, which is
   *  NOT the same as a failed read. `usageError` carries that. */
  usageRow: OrgUsageRow | null;
  usageDays: UsageDay[];
  usageError: string | null;
  /** WS-50: the saved credit price and when draws began, from the same read. */
  price: Price | null;
  /** False when the Console sent no credit-price field (a build before it). */
  priceReported: boolean;
  drawsSince: string | null;
  /** Usage slice 3 — by app, agent and person, with our cost. `null` with no
   *  error means the read was not attempted; with an error, it failed. */
  breakdown: UsageBreakdown | null;
  breakdownError: string | null;
  error: string | null;
};

/** The window the fleet board uses. One number, so the two pages cannot
 *  quote different periods for the same customer. */
const USAGE_WINDOW_DAYS = 30;

async function loadOrg(slug: string, authToken?: string, range?: UsageRange): Promise<Loaded> {
  try {
    // Four reads in parallel, all operator-door: the cross-org list (this
    // org's numbers), the catalog (the plan pickers), the per-org summary
    // (the roster with seat state, LS-9) and the org's `cc_live_` keys (CP-11).
    // ⚠️ All four carry the CALLER's session. A read that dropped it would
    // reach the Console as `breakglass` — past the role matrix, and logged
    // as a break-glass event on every page view.
    const d = { authToken };
    // 🔴 Seven now. H-133 added the two usage reads — the fleet row for this
    // organization, and its daily series. Both are `admin` reads the /usage
    // board already makes, so nothing new is exposed; this page simply
    // stopped being the only place that could not answer "spent on what".
    // Eight with usage slice 3: the breakdown, the same `admin` door.
    const [listRes, catRes, sumRes, keysRes, ledgerRes, usageRes, daysRes, brkRes] =
      await Promise.all([
        listOrganizations(d),
        catalog(d),
        billingSummary(slug, d),
        listKeys(slug, d),
        creditLedger(slug, d),
        orgUsage(USAGE_WINDOW_DAYS, d, range && consoleQuery(range)),
        usageDaily(USAGE_WINDOW_DAYS, slug, d, range && consoleQuery(range)),
        usageBreakdown(USAGE_WINDOW_DAYS, slug, d, range && consoleQuery(range)),
      ]);
    if (listRes.status !== 200) {
      return {
        org: null,
        plans: [],
        plansError: null,
        members: [],
        membersError: null,
        lots: undefined,
        keys: [],
        keysError: null,
        ledger: [],
        ledgerError: null,
        usageRow: null,
        usageDays: [],
        usageError: null,
        price: null,
        priceReported: true,
        drawsSince: null,
        breakdown: null,
        breakdownError: null,
        error: `Console returned ${listRes.status}`,
      };
    }
    const orgs = (JSON.parse(listRes.body) as OrgList).organizations;
    const org = orgs.find((o) => o.slug === slug) ?? null;
    const plans =
      catRes.status === 200 ? (JSON.parse(catRes.body) as Catalog).plans : [];
    let members: MemberRow[] = [];
    let membersError: string | null = null;
    // 022/027 — where the balance came from. `undefined` means a Console that
    // predates migration 028 and is NOT the same as "this customer has none".
    let lots: CreditLot[] | undefined;
    if (sumRes.status !== 200) {
      membersError = `The summary read returned ${sumRes.status}.`;
    } else {
      try {
        const body = JSON.parse(sumRes.body);
        members = readMembers(body);
        lots = readCreditLots(body);
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

    // ⚠️ A usage read that fails must NOT blank the page. Same rule as the
    // catalog and the roster above: the org's numbers are fine and every
    // action still works, so the panel says what it could not read.
    let usageRow: OrgUsageRow | null = null;
    let usageDays: UsageDay[] = [];
    let usageError: string | null = null;
    let price: Price | null = null;
    let priceReported = true;
    let drawsSince: string | null = null;
    if (usageRes.status !== 200) {
      usageError = `The usage read returned ${usageRes.status}.`;
    } else {
      try {
        const view = JSON.parse(usageRes.body) as Partial<OrgUsageView>;
        usageRow = (view.rows ?? []).find((r) => r.slug === slug) ?? null;
        price = priceFrom(view.inrPerCredit, view.usdToInr);
        priceReported = "inrPerCredit" in view;
        drawsSince = view.drawsSince ?? null;
      } catch {
        usageError = "The usage read could not be parsed.";
      }
    }
    if (usageError === null && daysRes.status === 200) {
      try {
        usageDays = (JSON.parse(daysRes.body) as { days?: UsageDay[] }).days ?? [];
      } catch {
        // ⚠️ The series is the DECORATION and the row is the answer. A
        // sparkline that will not parse must not hide the numbers beside it.
        usageDays = [];
      }
    }

    // ⚠️ Like every read above: a failure says so in its own panel and never
    // blanks the page. A 404 is a Console that predates the route.
    let breakdown: UsageBreakdown | null = null;
    let breakdownError: string | null = null;
    if (brkRes.status !== 200) {
      breakdownError =
        brkRes.status === 404
          ? "This Console build cannot break usage down yet (it predates usage slice 2)."
          : `The breakdown read returned ${brkRes.status}.`;
    } else {
      try {
        breakdown = readBreakdown(JSON.parse(brkRes.body));
        if (breakdown === null) breakdownError = "The breakdown read was not understood.";
      } catch {
        breakdownError = "The breakdown read could not be parsed.";
      }
    }

    return {
      org,
      plans,
      plansError: plansNotice(catRes.status, plans.length),
      members,
      membersError,
      lots,
      keys,
      keysError,
      ledger,
      ledgerError,
      usageRow,
      usageDays,
      usageError,
      price,
      priceReported,
      drawsSince,
      breakdown,
      breakdownError,
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
      usageRow: null,
      usageDays: [],
      usageError: null,
      price: null,
      priceReported: true,
      drawsSince: null,
      breakdown: null,
      breakdownError: null,
      error:
        e instanceof ConsoleUnconfigured
          ? "Customer Console is not configured."
          : `Could not reach the Customer Console: ${String(e)}`,
    };
  }
}

export default async function CustomerDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ tab?: string | string[]; range?: string; from?: string; to?: string }>;
}) {
  const gate = await staffSession();
  if (!gate.configured) redirect("/");
  if (!gate.ok) redirect("/login");

  const { slug } = await params;
  // WS-50 slice 3: one tab at a time. A link, so a tab can be bookmarked and
  // sent to a colleague, and the page stays a server component.
  const sp = await searchParams;
  const tab = tabFrom(sp.tab);
  // WS-50 slice 7: the period for the money on the Overview tab.
  const range = rangeFrom(sp, new Date());
  const {
    org, plans, plansError, members, membersError, lots, keys, keysError,
    usageRow, usageDays, usageError, price, priceReported, drawsSince,
    breakdown, breakdownError,
    ledger, ledgerError, error,
  } = await loadOrg(slug, gate.authToken, range);

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
  // WS-50: every money figure for this customer, computed once.
  const money = usageRow
    ? customerMoney({
        row: usageRow,
        price,
        seatsMonthlyInr: Number.isFinite(org.mrr_paise) ? org.mrr_paise / 100 : null,
        seatsBought: totals?.purchased ?? null,
        windowDays: range.days,
        drawsSince,
        now,
      })
    : null;

  const steps = lifecycleSteps(org.status);
  const next = nextStep(org.status, org.subscription_status);
  const tabHref = (t: TabKey) =>
    t === "overview" ? `/customers/${encodeURIComponent(org.slug)}` : `/customers/${encodeURIComponent(org.slug)}?tab=${t}`;

  return (
    <main className="wrap">
      <Header />
      <p>
        <a href="/">← All customers</a>
      </p>
      <div className="pagehead">
        <div className="orghero">
          <span className={`${categoricalBox(org.name)} lg`} aria-hidden="true">
            {providerGlyph(org.name)}
          </span>
          <div>
            <h1>{org.name}</h1>
            <div className="herochips">
              <span className={`pill ${org.status}`} title={statusHelp(org.status) || undefined}>
                {org.status.replace("_", " ")}
              </span>
              <span className="chip mono">{org.slug}</span>
            </div>
          </div>
        </div>
      </div>

      {/* WS-50 slice 3: where this customer is in its life, and the ONE next
          act, with a link to the tab that holds it. */}
      <section className="panel lifecycle">
        <ol className="lifebar" aria-label="Account life">
          {steps.map((st) => (
            <li key={st.key} className={`lifestep ${st.state}`} aria-current={st.state === "current" ? "step" : undefined}>
              <span className="lifedot" aria-hidden="true" />
              {st.label}
            </li>
          ))}
        </ol>
        {next && (
          <p className="lifenext">
            <strong>Next:</strong> {next.text}
            {org.status === "trial" && hint ? ` Trial: ${hint}.` : ""}{" "}
            {next.tab !== tab && <a href={tabHref(next.tab)}>Go there →</a>}
          </p>
        )}
      </section>

      <nav className="tabs customertabs" aria-label="Customer sections">
        {TABS.map((t) => (
          <a key={t.key} href={tabHref(t.key)} aria-current={t.key === tab ? "page" : undefined}>
            {t.label}
          </a>
        ))}
      </nav>

      {tab === "overview" && (
        <>
          <div className="stats">
            <div className="stat">
              <div className="lbl">Subscription</div>
              <div className="num small-num">{subscriptionWords(org.subscription_status)}</div>
              <div className="muted small">
                {formatPaise(org.mrr_paise)} a month{org.provider ? ` · paid via ${org.provider}` : ""}
              </div>
            </div>
            <div className="stat">
              <div className="lbl">Seats used</div>
              <div className="num small-num">
                {totals ? `${totals.assigned} of ${totals.purchased}` : "—"}
              </div>
              <div className="bar" aria-hidden="true">
                <i style={{ width: `${seatPct}%` }} />
              </div>
              {totals?.oversubscribed && (
                <div className="warn-t small">More people seated than seats bought</div>
              )}
            </div>
            {/* Only when the money strip below cannot show the balance: one
                figure, one place. */}
            {!money && (
              <div className="stat">
                <div className="lbl">Credits left</div>
                <div className="num small-num">{formatCr(Number(org.credit_balance) || 0)}</div>
                <div className="muted small">
                  <a href={tabHref("billing")}>Where they came from →</a>
                </div>
              </div>
            )}
            <div className="stat">
              <div className="lbl">Dates</div>
              <div className="muted small">
                Trial ends: {formatDate(org.trial_ends_at)}
                <br />
                Paid period ends: {formatDate(org.current_period_end)}
              </div>
            </div>
          </div>

          {/* 🔴 WS-50: money FIRST. The owner opens a customer to learn what we
              charge them and what they cost us. */}
          <RangePicker path={`/customers/${encodeURIComponent(org.slug)}`} range={range} />
          <CustomerUsage
            periodLabel={range.label}
            row={usageRow}
            days={usageDays}
            windowDays={range.days}
            error={usageError}
            money={money}
            price={price}
            priceReported={priceReported}
          />

          <CustomerBreakdown data={breakdown} error={breakdownError} money={money} price={price} />
        </>
      )}

      {tab === "billing" && (
        <>
          {plansError && (
            <div className="banner danger">
              <strong>Plans unavailable.</strong> {plansError} Credits still work.
            </div>
          )}
          <BillingActions
            slug={org.slug}
            status={org.status}
            subscriptionStatus={org.subscription_status}
            plans={plans}
            price={price}
          />

          {/* ── Where the credits came from (migration 028, §6) ─────────── */}
          <section className="panel">
            <div className="panel-head">
              <h2>Credits on hand</h2>
              {/* Credits do not expire (owner decision, 2026-09-21, H-135).
                  Free credits burn before paid ones. */}
              <p>
                What the balance of {formatCr(Number(org.credit_balance) || 0)} credits is
                made of, in the order it is spent. Free credits burn first, so a
                customer never loses credits they bought. Credits do not expire.
              </p>
            </div>
            {lots === undefined ? (
              <p className="field-hint warn">
                This Console build does not report where the credits came from yet.
                The balance is still correct.
              </p>
            ) : lots.length === 0 ? (
              <p className="field-hint">
                No credits left from any recorded grant. Credits granted before lots
                were recorded have no row here. The history below starts from the
                ledger.
              </p>
            ) : (
              <div className="tablewrap">
                <table className="grid">
                  <thead>
                    <tr>
                      <th>Where they came from</th>
                      <th>Left</th>
                      <th>Of</th>
                      <th>They paid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lots.map((lot) => (
                      <tr key={lot.id}>
                        <td>{LOT_SOURCE_LABEL[lot.source] ?? lot.source}</td>
                        <td className="mono">{formatCr(Number(lot.remaining) || 0)}</td>
                        <td className="mono muted">{formatCr(Number(lot.credits) || 0)}</td>
                        {/* NULL and "0" are DIFFERENT facts: nobody paid, versus
                            somebody paid nothing. */}
                        <td className={lot.pricePaidInr === null ? "muted" : "mono"}>
                          {lot.pricePaidInr === null
                            ? lot.source === "purchase"
                              ? "not recorded"
                              : "free"
                            : formatInr(Number(lot.pricePaidInr))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="panel">
            <h2 style={{ marginTop: 0 }}>Credit history</h2>
            <p className="muted">
              Every addition and every charge, newest first. Check a bank transfer
              here before you add credits: a reference already on this list was
              already credited, and the form refuses it.
            </p>
            {ledgerError ? (
              <p className="muted small">{ledgerError}</p>
            ) : ledger.length === 0 ? (
              <p className="muted small">No entries yet. The first grant starts the history.</p>
            ) : (
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Credits</th>
                      <th>What happened</th>
                      <th>Reference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.map((row, i) => (
                      <tr key={`${row.created_at}-${i}`}>
                        <td className="muted small">{formatDate(row.created_at)}</td>
                        <td className={`mono ${ledgerAdds(row) ? "ok-t" : ""}`}>
                          {ledgerAdds(row) ? "+" : ""}
                          {formatCr(Number(row.delta) || 0)}
                        </td>
                        <td>{LEDGER_REASON_WORDS[row.reason] ?? row.reason}</td>
                        <td className="mono small">{row.ref ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {tab === "people" && (
        <>
          {plansError && (
            <div className="banner danger">
              <strong>Plans unavailable.</strong> {plansError} The seat pickers below
              cannot list plans until it loads.
            </div>
          )}
          {org.seats.length > 1 && (
            <div className="panel">
              <h2 style={{ marginTop: 0 }}>Seats by plan</h2>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Plan</th>
                      <th>Bought</th>
                      <th>Seated</th>
                      <th>Free</th>
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
            </div>
          )}
          <PeopleActions
            slug={org.slug}
            plans={plans}
            members={members}
            membersError={membersError}
            seats={org.seats}
          />
        </>
      )}

      {tab === "access" && (
        <AccessActions slug={org.slug} status={org.status} keys={keys} keysError={keysError} />
      )}
    </main>
  );
}
