/**
 * The pure half of `/studio/mcp-tools` (WS-STU B1b-4).
 *
 * An admin reads the MCP tools of the connected servers and approves the text
 * of each one. This file reads the catalog rows, cuts their text, builds the
 * path-safe URLs and holds the words. It draws nothing.
 *
 * ⚠️ The description of a tool comes from a server that the org does not own.
 * It is untrusted text. Each string here is cut and cleaned of control, bidi
 * and zero-width marks, and the page draws it as plain text. It is never HTML
 * and never markdown.
 *
 * ⚠️ `textHash` is the hash that the catalog row SHOWED. The browser never
 * computes a hash. The server compares it with the hash of the current text.
 *
 * Fences: `mcpToolsModel.test.ts`.
 */

/** The most characters of one description that the page shows. The gateway cuts at 1,000. */
export const MAX_DESCRIPTION_CHARS = 1_000;
/** The most characters of one name. */
export const MAX_NAME_CHARS = 120;
/** The grammar of a server key (`MCP_SERVER_NAME` of the gateway). */
export const SERVER_RE = /^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$/;
/** The grammar of a tool name (`MCP_TOOL_NAME` of the gateway). */
export const TOOL_RE = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$/;
/** A hash that the server sends and takes: 64 lowercase hex characters. */
export const HASH_RE = /^[0-9a-f]{64}$/;

/** The four hints, in the order of `MCP_HINT_NAMES`, with the words of the page. */
export const HINTS = [
  { key: "readOnlyHint", label: "Read only" },
  { key: "destructiveHint", label: "Destructive" },
  { key: "idempotentHint", label: "Idempotent" },
  { key: "openWorldHint", label: "Open world" },
] as const;

export const WAITS = "Waits for approval";
export const APPROVED = "Approved";
export const NO_SERVERS =
  "No MCP server with tools shows here. Connect a server in Integrations, or ask for the right to use one. Then refresh it.";
export const STALE_NOTE =
  "This list may be out of date. Refresh a server to read its tools again.";
export const NO_MANAGE =
  "Only a member with the right to manage integrations approves a tool. Ask the owner of your organization.";
export const CHANGED_AFTER_LOOK =
  "This tool changed after you looked at it. The page read it again. Read the new text, then approve it.";
export const LIST_STALE_SENTENCE =
  "The list of this server is out of date. Refresh the server, then approve the tool.";
export const NOT_AN_ACTION = "This tool has a name that the page cannot send. It cannot be approved here.";

/** One MCP row of `GET /studio/actions`, as the page keeps it. */
export interface McpTool {
  id: string;
  server: string;
  tool: string;
  klass: "A" | "B";
  changed: boolean;
  textHash: string;
  description: string;
  hints: Record<string, boolean | null>;
}

/** A text cut to *limit* characters. Control, bidi and zero-width marks become spaces. A cut ends with `...`. */
export function clip(value: unknown, limit: number = MAX_NAME_CHARS): string {
  const raw = typeof value === "string" ? value : value == null ? "" : String(value);
  const clean = raw.replace(
    /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069\u200b-\u200f\u2028\u2029\ufeff]/g,
    " ",
  );
  return clean.length > limit ? `${clean.slice(0, Math.max(0, limit - 3))}...` : clean;
}

/** A description for display: the cut text, one line of spaces for each break. */
export function clipDescription(value: unknown): string {
  return clip(value, MAX_DESCRIPTION_CHARS).replace(/ {2,}/g, " ").trim();
}

export const validServer = (s: unknown): s is string => typeof s === "string" && SERVER_RE.test(s);
export const validTool = (t: unknown): t is string => typeof t === "string" && TOOL_RE.test(t);
export const validHash = (h: unknown): h is string => typeof h === "string" && HASH_RE.test(h);

/** `/api/studio/mcp/<server>/refresh`, or null when the key breaks the grammar. */
export function refreshPath(server: string): string | null {
  return validServer(server) ? `/api/studio/mcp/${encodeURIComponent(server)}/refresh` : null;
}

/** `/api/studio/mcp/<server>/tools/<tool>/approve`, or null when a name breaks the grammar. */
export function approvePath(server: string, tool: string): string | null {
  if (!validServer(server) || !validTool(tool)) return null;
  return `/api/studio/mcp/${encodeURIComponent(server)}/tools/${encodeURIComponent(tool)}/approve`;
}

const asRec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/**
 * The MCP rows of the catalog answer. A row of another source, and a row
 * whose server, tool or hash the grammar refuses, give nothing. The text is
 * cut here, once, so no caller draws a raw one.
 */
export function mcpTools(actions: unknown): McpTool[] {
  if (!Array.isArray(actions)) return [];
  const out: McpTool[] = [];
  for (const a of actions) {
    const r = asRec(a);
    if (r.source !== "mcp") continue;
    const server = r.server;
    const tool = r.mcp_tool;
    if (!validServer(server) || !validTool(tool)) continue;
    const h = asRec(r.hints);
    const hints: Record<string, boolean | null> = {};
    for (const { key } of HINTS) hints[key] = typeof h[key] === "boolean" ? (h[key] as boolean) : null;
    out.push({
      id: `mcp:${server}.${tool}`,
      server,
      tool,
      klass: r.class === "A" ? "A" : "B",
      // Anything but a plain `false` is "waits": fail closed.
      changed: r.changed !== false,
      textHash: validHash(r.text_hash) ? r.text_hash : "",
      description: clipDescription(r.description),
      hints,
    });
  }
  return out;
}

export interface ServerGroup {
  server: string;
  tools: McpTool[];
}

/** The tools grouped by server. The servers sort by name. The tools keep the order of the list. */
export function groupByServer(tools: readonly McpTool[]): ServerGroup[] {
  const by = new Map<string, McpTool[]>();
  for (const t of tools) by.set(t.server, [...(by.get(t.server) ?? []), t]);
  return [...by.keys()].sort().map((server) => ({ server, tools: by.get(server) ?? [] }));
}

/** The plain state of a tool. */
export const stateWords = (t: Pick<McpTool, "changed">): string => (t.changed ? WAITS : APPROVED);

/** The words of one hint: `Read only: yes`, `no`, or `not said`. */
export function hintWords(hints: Record<string, boolean | null>): string[] {
  return HINTS.map(({ key, label }) => {
    const v = hints[key];
    return `${label}: ${v === true ? "yes" : v === false ? "no" : "not said"}`;
  });
}

/**
 * Who sees the Approve button. A tool that is approved, a tool with no shown
 * hash and a member without `integrations:manage` get none. The server
 * refuses the rest, so this is a courtesy and not the boundary.
 */
export function canApprove(
  tool: Pick<McpTool, "changed" | "textHash" | "server" | "tool">,
  capabilities: readonly string[] | null | undefined,
): boolean {
  return (
    tool.changed &&
    validHash(tool.textHash) &&
    validServer(tool.server) &&
    validTool(tool.tool) &&
    Array.isArray(capabilities) &&
    capabilities.includes("integrations:manage")
  );
}

/** The words of the confirm dialog. The text of the tool and its hints are in them. */
export function confirmCopy(tool: McpTool) {
  return {
    title: "Approve this tool?",
    subject: `${clip(tool.server, 60)} / ${clip(tool.tool, 64)}`,
    body: `This approves the text below. An app or a workflow may then run the tool.\n${hintWords(tool.hints).join("\n")}`,
    description: tool.description || "The tool has no description.",
    confirmLabel: "Approve",
  };
}

/** What the page does with a failed approve. */
export type ApproveOutcome =
  | { kind: "reread"; message: string }
  | { kind: "refresh"; message: string }
  | { kind: "show"; message: string };

/** The 409 codes decide the next step. Any other refusal shows the server's own sentence. */
export function approveOutcome(status: number, code: string | undefined, detail: string): ApproveOutcome {
  if (status === 409 && code === "mcp_tool_text_changed") return { kind: "reread", message: CHANGED_AFTER_LOOK };
  if (status === 409 && code === "mcp_list_stale") return { kind: "refresh", message: LIST_STALE_SENTENCE };
  return { kind: "show", message: clip(detail, 400) };
}
