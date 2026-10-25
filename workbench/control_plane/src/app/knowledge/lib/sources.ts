// ── Knowledge sources (WS-61 KC-1b, `knowledge_curation.md` §7.1 and §7.2) ──
//
// The client of `gateway/routes/knowledge/sources.py`. A source is a drive
// that a member connected: Metorite reads the names and the folders of the
// drive, and no file text. This file holds the reads, the writes and the
// plain words of the Sources view. Every request goes through `call` in
// `api.ts`, the one way to the gateway. The key sits under `KNOWLEDGE_CACHE`,
// so one `invalidate(KNOWLEDGE_CACHE)` after a write also re-reads the list.

import { KnowledgeError, call } from "./api";
import { KNOWLEDGE_HOME } from "./links";

/** The Sources view. */
export const KNOWLEDGE_SOURCES = `${KNOWLEDGE_HOME}/sources`;

export const SOURCES_KEY = "knowledge/sources";

/** The poll while a drive is being read, in ms, and where it gives up. */
export const POLL_MS = 3_000;
export const POLL_LIMIT_MS = 5 * 60_000;

/** The parameters that the Google return puts on `/knowledge`. */
export const SOURCE_CONNECTED_PARAM = "source_connected";
export const SOURCE_ERROR_PARAM = "source_error";

export type SourceState = "discovering" | "ready" | "failed" | "needs_reconnect" | "disconnected";

export interface Source {
  id: string;
  provider: string;
  kind: string;
  owner_email: string;
  state: SourceState;
  account_email: string | null;
  node_count: number;
  discovered_at: string | null;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SourceList {
  sources: Source[];
  /** True when connected drives are off for the org: the gateway answered 404. */
  off: boolean;
}

/** One folder or file of a drive: metadata only. The gateway sends no text. */
export interface SourceNode {
  ext_id: string;
  parent_ext_id: string | null;
  drive_id: string | null;
  kind: "folder" | "file";
  name: string;
  mime: string | null;
  size: number | null;
  modified_at: string | null;
  web_url: string | null;
  explored: boolean;
  has_children: boolean;
}

export interface TreeAnswer {
  nodes: SourceNode[];
  truncated: boolean;
}

/**
 * The drives the member may see. A 404 means connected drives are off for the
 * org, and it is a state, not an error: the read cache keeps an error as words
 * only, so the status would be lost there.
 */
export async function listSources(): Promise<SourceList> {
  let out: { sources?: Source[] } | undefined;
  try {
    out = await call<{ sources?: Source[] }>("/sources");
  } catch (err) {
    if (err instanceof KnowledgeError && err.status === 404) return { sources: [], off: true };
    throw err;
  }
  if (!out || !Array.isArray(out.sources)) throw new Error("The sources answer has no list.");
  return { sources: out.sources, off: false };
}

/** The children of one folder, or the roots with no `parent`. */
export async function sourceTree(id: string, parent?: string | null): Promise<TreeAnswer> {
  const query = parent ? `?parent=${encodeURIComponent(parent)}` : "";
  const out = await call<{ nodes?: SourceNode[]; truncated?: boolean }>(
    `/sources/${encodeURIComponent(id)}/tree${query}`,
  );
  return { nodes: Array.isArray(out?.nodes) ? out.nodes : [], truncated: out?.truncated === true };
}

/** Ask the gateway for the Google consent address. The member has no body to send. */
export async function startConnect(): Promise<string> {
  const out = await call<{ url?: string }>("/sources/google_drive/connect", { method: "POST" });
  return typeof out?.url === "string" ? out.url : "";
}

/** Disconnect a drive. The gateway deletes its tree and leaves Gmail alone. */
export function disconnectSource(id: string): Promise<Source> {
  return call<Source>(`/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
}

function httpsHost(url: string | null | undefined): { host: string } | null {
  if (typeof url !== "string" || !url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" ? { host: parsed.hostname.toLowerCase() } : null;
  } catch {
    return null;
  }
}

/** The consent address, only when it is https on Google's sign-in host. */
export function connectTarget(url: string | null | undefined): string | null {
  return httpsHost(url)?.host === "accounts.google.com" ? (url as string) : null;
}

/** A file's link, only when it is https on a Google Drive or Docs host. */
export function driveLink(webUrl: string | null | undefined): string | null {
  const host = httpsHost(webUrl)?.host;
  return host === "drive.google.com" || host === "docs.google.com" ? (webUrl as string) : null;
}

/** The drives to draw: a disconnected row is history, and it is not drawn. */
export function visibleSources(list: SourceList | null | undefined): Source[] {
  return (list?.sources ?? []).filter((s) => s.state !== "disconnected");
}

const STATE_LABELS: Record<SourceState, string> = {
  discovering: "Reading your folders",
  ready: "Connected",
  failed: "Could not read the drive",
  needs_reconnect: "Needs to reconnect",
  disconnected: "Disconnected",
};

export function stateLabel(state: SourceState): string {
  return STATE_LABELS[state] ?? STATE_LABELS.ready;
}

/** The badge tone of a state. A tone is a word, and the Badge owns the colour. */
export function stateTone(state: SourceState): "neutral" | "primary" | "success" | "warning" | "destructive" {
  switch (state) {
    case "ready":
      return "success";
    case "discovering":
      return "primary";
    case "needs_reconnect":
      return "warning";
    case "failed":
      return "destructive";
    default:
      return "neutral";
  }
}

const RETURN_GENERIC = "Google Drive was not connected. Try again.";

const RETURN_MESSAGES: Record<string, string> = {
  invalid_state: "The sign-in link expired. Start again.",
  google_not_configured: "Google Drive is not set up for Metorite yet. Ask your administrator.",
  scope_missing: "Google did not give read access to Drive. Connect again and keep the Drive box ticked.",
  scope_extra: "Google gave more access than Metorite asks for. Nothing was connected. Try again.",
  no_refresh_token: "Google did not give a lasting access. Remove Metorite in your Google account settings, then connect again.",
  exchange_failed: "Google did not finish the sign-in. Try again in a moment.",
  account_unreadable: "Metorite could not read which Google account you chose. Try again.",
  no_code: "Google sent no sign-in code. Start again.",
  access_denied: "You did not allow access. Nothing was connected.",
  failed: RETURN_GENERIC,
};

/** One plain sentence for a failed return. Never the raw code alone. */
export function returnMessage(code: string | null | undefined): string {
  const key = (code ?? "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(RETURN_MESSAGES, key) ? RETURN_MESSAGES[key] : RETURN_GENERIC;
}

/** The words of the Disconnect dialog. The act deletes the tree and nothing else. */
export function disconnectCopy(
  source: Pick<Source, "account_email" | "provider" | "owner_email">,
  mine = true,
): {
  title: string;
  subject: string | null;
  body: string;
} {
  const account = source.account_email ?? "this account";
  const tail = mine
    ? "Your Gmail stays connected. You can connect the drive again later."
    : `${source.owner_email}'s Gmail stays connected. ${source.owner_email} can connect it again.`;
  return {
    title: "Disconnect Google Drive?",
    subject: source.account_email,
    body: `The folder tree of ${account} leaves Knowledge, and Metorite stops reading this drive. ${tail}`,
  };
}

/** True while any drive on screen is still being read. */
export function shouldPoll(sources: readonly Source[]): boolean {
  return sources.some((s) => s.state === "discovering");
}

/** "You" for the viewer's own drive, and the owner's address for another's. */
export function ownerLine(source: Pick<Source, "owner_email">, me: string): string {
  return source.owner_email.toLowerCase() === me.toLowerCase() ? "You" : source.owner_email;
}

/** True when the viewer owns the drive. Only the owner can connect it again. */
export function isMine(source: Pick<Source, "owner_email">, me: string): boolean {
  return source.owner_email.toLowerCase() === me.toLowerCase();
}

/** The count a rail row shows: the drives on screen, and none at 0. */
export function sourceCount(list: SourceList | null | undefined): string | null {
  const n = visibleSources(list).length;
  return n > 0 ? String(n) : null;
}

/** A file's type as one word, from its MIME type. */
export function typeWord(mime: string | null | undefined): string {
  const m = (mime ?? "").toLowerCase();
  if (m === "application/vnd.google-apps.document" || m.includes("wordprocessingml") || m === "application/msword") {
    return "Doc";
  }
  if (m === "application/vnd.google-apps.spreadsheet" || m.includes("spreadsheetml") || m === "application/vnd.ms-excel") {
    return "Sheet";
  }
  if (m === "application/vnd.google-apps.presentation" || m.includes("presentationml") || m === "application/vnd.ms-powerpoint") {
    return "Slide";
  }
  if (m === "application/pdf") return "PDF";
  if (m.startsWith("image/")) return "Image";
  return "File";
}

/** The glyph of a file, from its type word. */
export function typeIcon(word: string): string {
  switch (word) {
    case "Doc":
      return "FileText";
    case "Sheet":
      return "FileSpreadsheet";
    case "Slide":
      return "Presentation";
    case "Image":
      return "FileImage";
    default:
      return "File";
  }
}

/** The notice under a tree that the gateway cut short. */
export function truncatedNotice(shown: number): string {
  return `Showing the first ${shown.toLocaleString("en-US")} items. More items exist.`;
}

/** The words for a refused connect. A 404 and a missing client each get their own. */
export function connectFailure(err: unknown): string {
  if (err instanceof KnowledgeError) {
    if (err.status === 404) return "Connecting Google Drive is not on for your organization yet.";
    if (err.message === "google_not_configured") return returnMessage("google_not_configured");
  }
  return "Could not start the connection. Try again.";
}
