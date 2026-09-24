/**
 * What the credits went ON — the judgements behind D66's two breakdowns.
 *
 * 🔴 **H-134.** `GET /my/usage/activity` and `GET /my/usage/members` were
 * built, tested and reachable, and nothing called them. The customer saw a
 * balance, a burn figure and a runway, and could not see what any of it went
 * on. A customer who cannot see the breakdown cannot manage the spend, so
 * every credit question became a support conversation.
 *
 * ⚠️ **Credits arrive as STRINGS and stay strings until they are drawn.** They
 * are money, the ledger stores `NUMERIC(14,4)`, and a float round-trip is the
 * standard way to make a total disagree with the sum of its rows. Only
 * `share` parses, because a bar width is not money.
 */

/** One activity — the agent, else the module, else "unattributed". */
export interface ActivityRow {
  activity: string;
  calls: number;
  credits: string;
}

/** One person's spend inside the organization. */
export interface MemberSpendRow {
  member: string;
  calls: number;
  credits: string;
}

export interface SpendPayload<Row> {
  rows: Row[];
  windowDays: number;
}

/** A row's numeric credits, for arithmetic that is not money. */
function num(credits: string): number {
  const n = Number(credits);
  return Number.isFinite(n) ? n : 0;
}

/**
 * 🔴 **Is the money real yet?**
 *
 * `billed_credits` is 0 on every row while the rate card is unpriced (H-42),
 * and that is the shipped state. The CALL COUNTS are real from the first
 * routed call. A page that led with credits would therefore show a busy month
 * as a column of zeros and read as broken.
 *
 * So the surface asks this, leads with calls when it is false, and says why.
 */
export function spendIsMeasured(rows: { credits: string }[]): boolean {
  return rows.some((r) => num(r.credits) > 0);
}

/**
 * Largest first — by credits when the card is priced, by calls when it is not.
 *
 * ⚠️ **The sort key follows the same fact the display does.** Sorting by
 * credits while showing calls would order a table by a column of zeros, and
 * the reader would see an arbitrary order presented as a ranking.
 */
export function sortSpend<Row extends { calls: number; credits: string }>(
  rows: Row[],
): Row[] {
  const byMoney = spendIsMeasured(rows);
  return [...rows].sort((a, b) => {
    const d = byMoney ? num(b.credits) - num(a.credits) : b.calls - a.calls;
    // A stable tiebreak, so two equal rows do not swap between renders.
    return d !== 0 ? d : a.calls === b.calls ? 0 : b.calls - a.calls;
  });
}

/**
 * Each row's share of the total, 0..1, on whichever measure is real.
 *
 * Returns 0 for every row when the total is zero — never NaN. A bar of width
 * NaN renders as nothing, which reads as "no usage" rather than as a defect.
 */
export function spendShare<Row extends { calls: number; credits: string }>(
  rows: Row[],
  row: Row,
): number {
  const byMoney = spendIsMeasured(rows);
  const value = byMoney ? num(row.credits) : row.calls;
  const total = rows.reduce(
    (sum, r) => sum + (byMoney ? num(r.credits) : r.calls),
    0,
  );
  return total > 0 ? value / total : 0;
}

/** The totals a heading states, so the table and its heading cannot differ. */
export function spendTotals(rows: { calls: number; credits: string }[]): {
  calls: number;
  credits: number;
} {
  return {
    calls: rows.reduce((n, r) => n + r.calls, 0),
    credits: rows.reduce((n, r) => n + num(r.credits), 0),
  };
}

/** What the Console calls an activity it could not attribute. */
export const UNATTRIBUTED = "unattributed";

/**
 * An activity slug in the customer's words.
 *
 * ⚠️ **Unknown slugs pass through UNCHANGED.** The Console groups by the
 * agent, else the module, and both are open vocabularies that grow without
 * this file. A map that replaced an unknown slug with "Other" would hide the
 * app a customer is actually paying for.
 */
export function activityLabel(activity: string): string {
  if (activity === UNATTRIBUTED) return "Not attributed";
  return activity;
}

// ── By app (usage slice 3) ──────────────────────────────────────────────────

/** One agent inside an app. */
export interface AgentSpendRow {
  agent: string;
  calls: number;
  credits: string;
}

/** One app, and the agents that spent inside it. `GET /my/usage/apps`. */
export interface AppSpendRow {
  app: string;
  calls: number;
  credits: string;
  agents: AgentSpendRow[];
}

/**
 * An app slug in the words the sidebar uses.
 *
 * 🔴 **The sidebar is the ONE source for an app's name.** The agents stamp the
 * navigation's own slug (`config.json`, usage slice 1), so the label is looked
 * up from the same `PANES` the sidebar draws. A second map would disagree the
 * first time a pane is renamed.
 *
 * ⚠️ **Unknown slugs pass through**, as `activityLabel` does. A Custom App
 * arrives as `app:<name>` and is shown by its own name, never as "Other".
 */
export function appLabel(
  app: string,
  panes: readonly { href: string; label: string }[],
): string {
  if (app === UNATTRIBUTED) return "Not attributed";
  if (app.startsWith("app:")) return app.slice(4);
  return panes.find((p) => p.href === `/${app}`)?.label ?? app;
}

/**
 * An agent's slug read aloud: `projects-assistant` becomes
 * `Projects assistant`. Only the first letter is raised, so a product name
 * inside the slug keeps the case the agent gave it.
 */
export function agentLabel(agent: string): string {
  if (agent === UNATTRIBUTED) return "Not attributed";
  const words = agent.replace(/[-_]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : agent;
}
