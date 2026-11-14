/**
 * Connect in place (WS-STU I8a, `ai_studio.md` §8.1 and §14 I8a): the pure model of the lines that the
 * Studio IDE draws for the connections of a draft. The guard of the return path is `returnPath.ts`.
 *
 * The gateway owns every fact. `GET /api/studio/packages/{id}/connections` answers one row for each
 * name of the STORED draft: `name`, `label`, `met`, `checkable`, the `setup_href` of the owning app and
 * `can_connect`. A 404 is the gate off, and the IDE then draws nothing.
 *
 * A name that the gateway cannot check (`checkable: false`: a mailbox, a WhatsApp number, a key of the
 * console, the bare id `mcp`, an id off the connector list) never meets, so the action door refuses each
 * action of the app while the draft names it. Its line says so, offers no Connect, and counts in the
 * header with each unmet name. A read that failed
 * keeps the rows of the last answer and their count, and adds one line with Retry. With no rows, the
 * header reads "connections not checked".
 *
 * Studio copies no connect flow (§8.1 rule 1). Connect sends the browser to the server's
 * `setup_href`, with `return` set to the IDE of this app. The owning app runs its own flow. The
 * console reads `return` back through `safeStudioReturn`, so only a path of this site under
 * `/studio/` can bring the member back.
 */
import { safeLocalPath } from "@/lib/orgLink";

import { buildHref } from "./packageDraft";
import { RETURN_PARAM, STUDIO_PREFIX, safeStudioReturn } from "./returnPath";

export { RETURN_PARAM, STUDIO_PREFIX, safeStudioReturn };

/** One connection of the draft, as the gateway answers it. */
export interface ConnectionRow {
  /** The name in the draft: `mcp:<server>` or a provider id. */
  name: string;
  label: string;
  /** The caller has a live connection of this name now. */
  met: boolean;
  /**
   * The gateway can check this name. False for a provider whose connection lives outside the registry
   * (a mailbox and a WhatsApp number until I9, a key of the console until I8e). It never turns met, so
   * the app does not run while the draft names it, and its line offers no Connect.
   */
  checkable: boolean;
  /** The connect flow of the owning app. Null when the server sent no path of this site. */
  setupHref: string | null;
  /** The caller may start the connect (an org connection needs `integrations:manage`). */
  canConnect: boolean;
}

/** One line of the IDE. */
export type ConnectLine =
  | { kind: "met"; row: ConnectionRow; text: string }
  | { kind: "connect"; row: ConnectionRow; text: string; href: string | null }
  | { kind: "ask"; row: ConnectionRow; text: string }
  | { kind: "blocked"; row: ConnectionRow; text: string };

/** The prefix of an MCP server in a draft (the I1a grammar). */
export const MCP_PREFIX = "mcp:";
/** The bare id of the family row "Another system". No server row has it, so it never meets. */
export const BARE_MCP = "mcp";

/** The words of the read that failed. They say what happened and the next step. */
export const CONNECTIONS_READ_FAILED =
  "Metorite could not check the connections of this app. Try again in a moment.";

/** The header of the panel while the read failed and it holds no answer. */
export const CONNECTIONS_NOT_CHECKED = "connections not checked";

/** A base that no real link can name, to read a relative path against. */
const BASE = "https://studio-connect.invalid";

/** An encoded slash or backslash. A server or a proxy can decode it into a second slash. */
const ENCODED_SLASH = /%(2f|5c)/i;

/**
 * The server's link with the return path of this app's IDE. Null for a link that is not of this site.
 *
 * The link is read as the browser reads it, so `/.//host` becomes `//host`. That is why the OUTPUT goes
 * through `safeLocalPath` again, and an encoded slash is refused in the input.
 */
export function connectHref(row: Pick<ConnectionRow, "setupHref">, pkg: string): string | null {
  const href = safeLocalPath(row.setupHref);
  if (href === null || ENCODED_SLASH.test(href)) return null;
  const back = safeStudioReturn(buildHref(pkg));
  const url = new URL(href, BASE);
  if (url.origin !== BASE) return null;
  if (back !== null) url.searchParams.set(RETURN_PARAM, back);
  return safeLocalPath(`${url.pathname}${url.search}${url.hash}`);
}

/** How a line names the connection. A bare server name reads as "the MCP server <name>". */
export function subject(row: Pick<ConnectionRow, "name" | "label">): string {
  return row.name.startsWith(MCP_PREFIX) ? `the MCP server ${row.label}` : row.label;
}

const capital = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/** The line of each row, in the order of the draft. */
export function connectLines(rows: readonly ConnectionRow[], pkg: string): ConnectLine[] {
  return rows.map((row): ConnectLine => {
    const what = subject(row);
    if (row.met) return { kind: "met", row, text: `${capital(what)} is connected.` };
    if (!row.checkable) {
      // The bare id `mcp` names the family. A server is `mcp:<server>`, so that is the next step.
      const next = row.name === BARE_MCP ? "Name the server, as mcp:<server>." : "Remove the name until Metorite can check it.";
      return {
        kind: "blocked",
        row,
        text: `${capital(what)} cannot be connected from Studio yet. This app will not run while the draft names it. ${next}`,
      };
    }
    if (!row.canConnect) {
      return { kind: "ask", row, text: `An admin must connect ${what}. Ask one, then come back.` };
    }
    return { kind: "connect", row, text: `This app needs ${what}.`, href: connectHref(row, pkg) };
  });
}

/**
 * The count of names that stop the app now: each name that is not met. A blocked name counts too, because
 * the action door refuses each action while the draft names it.
 */
export function unmetCount(rows: readonly ConnectionRow[]): number {
  return rows.filter((r) => !r.met).length;
}

/** The words of the panel header for the names that stop the app, or `null` when each one is met. */
export function missingLine(rows: readonly ConnectionRow[]): string | null {
  const n = unmetCount(rows);
  if (n === 0) return null;
  return n === 1 ? "1 connection needs attention" : `${n} connections need attention`;
}

/** What the Problems panel draws from the read. */
export interface ConnectPanel {
  lines: ConnectLine[];
  /** The header words: "1 connection needs attention", "connections not checked", or null. */
  missing: string | null;
  /** The line of a read that failed, with Retry. Null after a read that worked. */
  failure: string | null;
}

/**
 * The panel from the cached read: the rows it holds and the error of its last read. A failed read keeps the
 * rows that the cache holds, and adds the failure line. The header never reads as "nothing missing" while
 * the read failed: with no count to show, it says the connections were not checked.
 */
export function connectPanel(rows: readonly ConnectionRow[] | undefined, error: string | null, pkg: string): ConnectPanel {
  const held = rows ?? [];
  const failure = error !== null ? CONNECTIONS_READ_FAILED : null;
  const missing = missingLine(held) ?? (failure !== null ? CONNECTIONS_NOT_CHECKED : null);
  return { lines: connectLines(held, pkg), missing, failure };
}

/** The count on the phone Problems tab: each problem and each missing connection. Undefined for none. */
export function phoneProblemsCount(problems: number, rows: readonly ConnectionRow[] | undefined): number | undefined {
  const n = problems + unmetCount(rows ?? []);
  return n > 0 ? n : undefined;
}

/** The words of the Connect sheet for one row. */
export function sheetCopy(row: ConnectionRow): { title: string; body: string; action: string } {
  if (row.name.startsWith(MCP_PREFIX)) {
    return {
      title: `Connect ${row.label}`,
      body:
        `Metorite opens Integrations with the server name ${row.label} filled in. ` +
        "Add the address of the server there, and Metorite brings you back to this app.",
      action: "Open Integrations",
    };
  }
  return {
    title: `Connect ${row.label}`,
    body: `Metorite opens the page where ${row.label} connects. When you finish there, come back to this app.`,
    action: "Open the connect page",
  };
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** The rows of an answer. A row with no name is dropped, and a value of another type reads as false. */
export function parseConnections(body: unknown): ConnectionRow[] {
  const list = isObject(body) && Array.isArray(body.connections) ? body.connections : [];
  const out: ConnectionRow[] = [];
  for (const item of list) {
    if (!isObject(item) || typeof item.name !== "string" || item.name === "") continue;
    out.push({
      name: item.name,
      label: typeof item.label === "string" && item.label.trim() ? item.label : item.name,
      met: item.met === true,
      // An older gateway sends no key, and its rows were all checkable.
      checkable: item.checkable !== false,
      setupHref: safeLocalPath(item.setup_href),
      canConnect: item.can_connect === true,
    });
  }
  return out;
}

/** The cache key of the read of one package. */
export function connectionsKey(pkg: string): string {
  return `studio/connections/${pkg}`;
}

/**
 * Read the connections of a draft. A 404 (the gate off, or not the owner) gives no row. The path is a
 * literal, so the chat parity fence can read it.
 */
export async function getConnections(pkg: string, fetchImpl: typeof fetch = fetch): Promise<ConnectionRow[]> {
  const res = await fetchImpl(`/api/studio/packages/${encodeURIComponent(pkg)}/connections`);
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(CONNECTIONS_READ_FAILED);
  return parseConnections(await res.json().catch(() => ({})));
}
