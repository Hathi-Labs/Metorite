// Where this customer's credits went: by app, by agent, by person. Usage
// slice 3, rewritten for WS-50 (decision D94).
//
// 🔴 **"Our cost" read as the customer's price.** The owner asked whether
// "Our cost" was what the vendor charges us or what we charge the customer.
// The columns now say it in words: "We charged" and "AI cost", both in
// rupees, and each heading has an ⓘ.
//
// ⚠️ **We charged, per row, is an ESTIMATE, and the table says so.** The
// Console records which lots paid for each CHARGE, not for each app. A row is
// valued at this customer's average rupees per credit used in the window.
// The AI cost per row is exact.
//
// ⚠️ **The rows are the customer's own.** `/admin/usage/breakdown` serves the
// same grouping as the customer's `/my/usage/apps` and `/my/usage/members`.
//
// ⚠️ **A server component.** It renders numbers the page already fetched.

import Explain from "../../Explain";
import {
  earnedPerCredit,
  formatCr,
  formatInr,
  formatPct,
  rowMoney,
  type CustomerMoney,
  type Price,
} from "@/lib/money";
import { chipClass } from "@/lib/tone";
import { breakdownCut, type UsageBreakdown } from "@/lib/usage";

/** What the Console calls a call it could not attribute. */
const UNATTRIBUTED = "unattributed";

function Name({ value }: { value: string }) {
  // A gap is NAMED, never blank: the operator sees that some caller is still
  // not recording who or which app.
  return value === UNATTRIBUTED ? (
    <span className="muted">
      not attributed
      <Explain term="notAttributed" />
    </span>
  ) : (
    <span className="mono">{value}</span>
  );
}

function MarginCell({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>;
  return value < 0 ? (
    <span className={chipClass("danger")}>{formatPct(value)}</span>
  ) : (
    <span className="mono">{formatPct(value)}</span>
  );
}

function Head({ first }: { first: string }) {
  return (
    <thead>
      <tr>
        <th>{first}</th>
        <th>
          AI calls
          <Explain term="calls" />
        </th>
        <th>
          Credits used
          <Explain term="creditsUsed" />
        </th>
        <th>
          Charged for AI
          <Explain term="aiRevenue" />
        </th>
        <th>
          AI cost
          <Explain term="aiCost" />
        </th>
        <th>
          AI margin
          <Explain term="aiMargin" />
        </th>
      </tr>
    </thead>
  );
}

type Part = { calls: number; credits: string; costUsd: string };

function Cells({
  part,
  money,
  price,
  quiet,
}: {
  part: Part;
  money: CustomerMoney | null;
  price: Price | null;
  quiet?: boolean;
}) {
  const m = money
    ? rowMoney(part, money, price)
    : { charged: null, aiCost: null, profit: null, margin: null };
  const cls = quiet ? "mono muted" : "mono";
  return (
    <>
      <td className={cls}>{part.calls.toLocaleString("en-IN")}</td>
      <td className={cls}>{formatCr(Number(part.credits) || 0)}</td>
      <td className={cls}>{formatInr(m.charged)}</td>
      <td className={cls}>{formatInr(m.aiCost)}</td>
      <td>
        <MarginCell value={m.margin} />
      </td>
    </>
  );
}

export default function CustomerBreakdown({
  data,
  error,
  money,
  price,
}: {
  data: UsageBreakdown | null;
  /** The read failed or the body was not understood. Said, never blanked. */
  error: string | null;
  /** The customer's whole-window figures, which value each row. */
  money: CustomerMoney | null;
  price: Price | null;
}) {
  const perCredit = money ? earnedPerCredit(money) : null;
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Where it went</h2>
        <p>
          {`The same ${data?.windowDays ?? 30} days, split by app, by agent and by person. `} The customer&apos;s own admin sees these rows with credits
          only. They never see the AI cost or the margin.
        </p>
      </div>

      {error && <p className="result err">{error}</p>}

      {!error && data && data.apps.length === 0 && data.members.length === 0 && (
        <div className="empty">
          <h3>Nothing to split</h3>
          <p className="muted">
            No AI calls in this period, so there is no app or person to split
            them by.
          </p>
        </div>
      )}

      {!error && data && (data.apps.length > 0 || data.members.length > 0) && (
        <p className="field-hint">
          {perCredit !== null
            ? `"Charged for AI" for each row is estimated: its credits used × ${formatInr(
                perCredit,
              )}, this customer's average earned per credit in the period. Free credits ` +
              `bring that average down. Seats are not split by app or person, so they are ` +
              `not in these rows. "AI cost" is exact.`
            : `"Charged for AI" needs the credit price, which is not saved yet. "AI cost" is exact.`}
        </p>
      )}

      {!error && data && data.apps.length > 0 && (
        <>
          <h3>By app</h3>
          {breakdownCut(data.apps.length, data.appsTotal) && (
            <p className="field-hint">{breakdownCut(data.apps.length, data.appsTotal)}</p>
          )}
          <div className="tablewrap">
            {/* Scrolls inside its own box, so a wide table never moves the
                page sideways (measured at 390px on 2026-09-20). */}
            <table className="grid">
              <Head first="App · agent" />
              <tbody>
                {data.apps.flatMap((a) => [
                  <tr key={`app:${a.app}`}>
                    <td>
                      <strong>
                        <Name value={a.app} />
                      </strong>
                    </td>
                    <Cells part={a} money={money} price={price} />
                  </tr>,
                  // The agents inside, indented. Omitted when one agent is the
                  // whole app, because its row would repeat the app's.
                  ...(a.agents.length > 1
                    ? a.agents.map((g) => (
                        <tr key={`agent:${a.app}:${g.agent}`}>
                          <td style={{ paddingLeft: "1.5rem" }}>
                            <Name value={g.agent} />
                          </td>
                          <Cells part={g} money={money} price={price} quiet />
                        </tr>
                      ))
                    : []),
                ])}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!error && data && data.members.length > 0 && (
        <>
          <h3>By person</h3>
          {breakdownCut(data.members.length, data.membersTotal) && (
            <p className="field-hint">
              {breakdownCut(data.members.length, data.membersTotal)}
            </p>
          )}
          <div className="tablewrap">
            <table className="grid">
              <Head first="Person" />
              <tbody>
                {data.members.map((m) => (
                  <tr key={m.member}>
                    <td>
                      <Name value={m.member} />
                    </td>
                    <Cells part={m} money={money} price={price} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
