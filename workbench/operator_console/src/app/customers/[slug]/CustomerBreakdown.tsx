// Where this customer's credits went: by app, by agent, by person. Usage slice 3.
//
// 🔴 **The panel above says HOW MUCH, and this one says ON WHAT.** H-133 put
// the total and the daily series on this page. When a customer asks where the
// credits went, the operator needs the split the customer's own admin sees,
// plus the one thing that admin never sees: what the vendor charged US, and
// the margin left over.
//
// ⚠️ **The rows are the customer's own.** `/admin/usage/breakdown` serves the
// same grouping as the customer's `/my/usage/apps` and `/my/usage/members`,
// with our cost joined on by key. So this table and the customer's page can
// never quote two totals for one app.
//
// ⚠️ **A server component.** It renders numbers the page already fetched
// with the caller's session, and it writes nothing.

import { marginPct } from "@/lib/priceboard";
import { formatCredits, formatUsd } from "@/lib/format";
import { chipClass } from "@/lib/tone";
import { breakdownCut, isLoss, type UsageBreakdown } from "@/lib/usage";

/** What the Console calls a call it could not attribute. */
const UNATTRIBUTED = "unattributed";

function Name({ value }: { value: string }) {
  // ⚠️ A gap is NAMED, never blank. "unattributed" rows are how the operator
  // sees that some caller is still not stamping who or which app.
  return value === UNATTRIBUTED ? (
    <span className="muted">not attributed</span>
  ) : (
    <span className="mono">{value}</span>
  );
}

function Margin({ value }: { value: string | null }) {
  // NULL is NEUTRAL: no saved credit price, so no margin. Never a zero.
  if (value === null) return <span className="muted">—</span>;
  // A LOSS wears the danger chip, the console's one colour for "look now".
  // No hand-rolled red: `lib/tone.ts` owns what danger looks like.
  return isLoss(value) ? (
    <span className={chipClass("danger")}>{marginPct(value)}</span>
  ) : (
    <span className="mono">{marginPct(value)}</span>
  );
}

export default function CustomerBreakdown({
  data,
  error,
}: {
  data: UsageBreakdown | null;
  /** The read failed or the body was not understood. Said, never blanked. */
  error: string | null;
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Where it went</h2>
        <p>
          By app, by agent and by person, over the last{" "}
          {data?.windowDays ?? 30} days. <b>Our cost</b> and <b>margin</b>{" "}
          are ours alone — the customer&apos;s admin sees the same rows with
          credits only.
        </p>
      </div>

      {error && <p className="result err">{error}</p>}

      {!error && data && data.apps.length === 0 && data.members.length === 0 && (
        <div className="empty">
          <h3>Nothing to break down</h3>
          <p className="muted">
            No metered calls in the window, so there is no app or person to
            split them by.
          </p>
        </div>
      )}

      {!error && data && data.apps.length > 0 && (
        <>
          <h3>By app</h3>
          {breakdownCut(data.apps.length, data.appsTotal) && (
            <p className="field-hint">{breakdownCut(data.apps.length, data.appsTotal)}</p>
          )}
          <div className="tablewrap">
            {/* ⚠️ Scrolls inside its own box, so a wide table never moves
                the page sideways (measured at 390px on 2026-09-20). */}
            <table className="grid">
              <thead>
                <tr>
                  <th>App · agent</th>
                  <th>Calls</th>
                  <th>Credits</th>
                  <th>Our cost</th>
                  <th>Margin</th>
                </tr>
              </thead>
              <tbody>
                {data.apps.flatMap((a) => [
                  <tr key={`app:${a.app}`}>
                    <td>
                      <strong>
                        <Name value={a.app} />
                      </strong>
                    </td>
                    <td className="mono">{a.calls.toLocaleString("en-IN")}</td>
                    <td className="mono">{formatCredits(a.credits)}</td>
                    <td className="mono">{formatUsd(a.costUsd)}</td>
                    <td>
                      <Margin value={a.realisedMargin} />
                    </td>
                  </tr>,
                  // The agents inside, indented. Omitted when one agent is the
                  // whole app, because its row would repeat the app's.
                  ...(a.agents.length > 1
                    ? a.agents.map((g) => (
                        <tr key={`agent:${a.app}:${g.agent}`}>
                          <td style={{ paddingLeft: "1.5rem" }}>
                            <Name value={g.agent} />
                          </td>
                          <td className="mono muted">{g.calls.toLocaleString("en-IN")}</td>
                          <td className="mono muted">{formatCredits(g.credits)}</td>
                          <td className="mono muted">{formatUsd(g.costUsd)}</td>
                          <td>
                            <Margin value={g.realisedMargin} />
                          </td>
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
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Calls</th>
                  <th>Credits</th>
                  <th>Our cost</th>
                  <th>Margin</th>
                </tr>
              </thead>
              <tbody>
                {data.members.map((m) => (
                  <tr key={m.member}>
                    <td>
                      <Name value={m.member} />
                    </td>
                    <td className="mono">{m.calls.toLocaleString("en-IN")}</td>
                    <td className="mono">{formatCredits(m.credits)}</td>
                    <td className="mono">{formatUsd(m.costUsd)}</td>
                    <td>
                      <Margin value={m.realisedMargin} />
                    </td>
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
