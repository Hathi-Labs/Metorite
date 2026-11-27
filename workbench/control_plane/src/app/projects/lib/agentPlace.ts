/**
 * Where an agent session runs, and on which model (WS-MCP SW-106,
 * `agent_coordination.md` §3.12.3 and §3.12.7 rules 5 and 6).
 *
 * The Map and the run panel share these words and this check. The gateway
 * gives a machine name, a kind of machine (`computer`, `server` or `cloud`),
 * a source (`checked` or `reported`) and a model name. The agent chose the
 * name and the model, so each one is untrusted display text: the view draws it
 * as plain text, and it grants nothing. A box token alone proves a machine, and
 * the gateway marks that machine `checked`. Any other machine is `reported`.
 *
 * ⚠️ No network address of a machine is ever read or drawn here (§3.12.4 rule 5).
 */

export type HostKind = "computer" | "server" | "cloud";
export type HostSource = "checked" | "reported";

/** The words for a fact that the agent did not send (§3.12.3). */
export const NOT_REPORTED = "Not reported";

const KINDS: readonly HostKind[] = ["computer", "server", "cloud"];
const SOURCES: readonly HostSource[] = ["checked", "reported"];

/** The order of the machine groups on the map: the kinds, then no kind. */
export const KIND_RANK: Record<HostKind, number> = { computer: 0, server: 1, cloud: 2 };

const KIND_ICON: Record<HostKind, string> = { computer: "Laptop", server: "Server", cloud: "Cloud" };
const KIND_WORD: Record<HostKind, string> = { computer: "Computer", server: "Server", cloud: "Cloud" };

/** The icon of a kind of machine. A machine with no kind gets a question mark. */
export function hostKindIcon(kind: HostKind | null): string {
  return kind ? KIND_ICON[kind] : "CircleHelp";
}

/** "Computer", "Server" or "Cloud". Not reported for no kind. */
export function hostKindWord(kind: HostKind | null): string {
  return kind ? KIND_WORD[kind] : NOT_REPORTED;
}

/** The icon of a model line. */
export const MODEL_ICON = "Cpu";

/** The words of the mark of a machine, in the node and in the side sheet. */
export function hostSourceWord(source: HostSource | null): string {
  return source ?? "";
}

/** What the side sheet says about the source. */
export function hostSourceSentence(source: HostSource | null): string {
  if (source === "checked") return "Checked by a box token";
  if (source === "reported") return "Reported by the agent";
  return NOT_REPORTED;
}

/** One value of untrusted text, or `null` for a missing or bad value. */
export function placeText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  // A control or format character never reaches the screen.
  const clean = value.replace(/[\p{C}]/gu, " ").replace(/\s+/g, " ").trim();
  return clean ? clean : null;
}

export function placeKind(value: unknown): HostKind | null {
  return typeof value === "string" && (KINDS as readonly string[]).includes(value) ? (value as HostKind) : null;
}

export function placeSource(value: unknown): HostSource | null {
  return typeof value === "string" && (SOURCES as readonly string[]).includes(value) ? (value as HostSource) : null;
}

export interface Place {
  host: string | null;
  kind: HostKind | null;
  source: HostSource | null;
  model: string | null;
}

/** The place of a session, from one answer row. Each bad value drops to `null`. */
export function placeOfRow(row: Record<string, unknown>): Place {
  const host = placeText(row.host);
  const kind = placeKind(row.host_kind);
  const source = host || kind ? placeSource(row.host_source) : null;
  return { host, kind, source, model: placeText(row.model) };
}

/** True when the session has no machine, no kind and no model. */
export function noPlace(place: Pick<Place, "host" | "kind" | "model">): boolean {
  return !place.host && !place.kind && !place.model;
}

/** The sentence for a screen reader: "Machine VJ laptop, Computer, checked. Model Sonnet 5.5." */
export function placeSentence(place: Place): string {
  const machine = place.host ?? NOT_REPORTED;
  const kind = place.kind ? `, ${hostKindWord(place.kind)}` : "";
  const source = place.source ? `, ${hostSourceWord(place.source)}` : "";
  return `Machine ${machine}${kind}${source}. Model ${place.model ?? NOT_REPORTED}.`;
}
