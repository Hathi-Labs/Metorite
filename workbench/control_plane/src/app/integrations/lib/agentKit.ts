// ── The agent kit download, as pure decisions (KIT-4b) ─────────────────────
//
// Spec: `project-docs/specs/orchestration_playbook.md` §6a and KIT-4.
// `components/AgentKitDownload.tsx` only draws what this module decides.
// The steps repeat `_STEP` of `packages/acb_playbook/acb_playbook/kit_zip.py`.
// The kit holds no token. The member signs in with OAuth or mints a box token.

import { MCP_URL } from "./agents";

export interface KitClient {
  id: string;
  label: string;
  /** Short install lines. Org-neutral. */
  steps: string[];
}

export const KIT_MERGE_NOTE = "If AGENTS.md, CLAUDE.md or GEMINI.md exists, merge the two files.";
const UNZIP = "Unzip the files into your project.";
const SIGN_IN = "Sign in with OAuth, or mint a box token. The kit holds no token.";

/** The six tabs. The ids are the six of the gateway. */
export const CLIENT_TABS: KitClient[] = [
  {
    id: "claude",
    label: "Claude Code",
    steps: [UNZIP, `Run: claude mcp add --transport http metorite ${MCP_URL}`, SIGN_IN],
  },
  {
    id: "codex",
    label: "OpenAI Codex",
    steps: [UNZIP, "Add .codex/config.toml.example to .codex/config.toml.", "Run: codex mcp login metorite", SIGN_IN],
  },
  {
    id: "copilot",
    label: "GitHub Copilot",
    steps: [
      UNZIP,
      "Rename .vscode/mcp.json.example to .vscode/mcp.json.",
      "For GitHub.com, use coding-agent-mcp.json.example.",
      SIGN_IN,
    ],
  },
  {
    id: "gemini",
    label: "Gemini CLI",
    steps: [UNZIP, "Merge .gemini/settings.json.example into .gemini/settings.json.", "Run: /mcp auth metorite", SIGN_IN],
  },
  {
    id: "antigravity",
    label: "Google Antigravity",
    steps: [UNZIP, "Rename .agents/mcp_config.json.example to .agents/mcp_config.json.", "Restart Antigravity.", SIGN_IN],
  },
  {
    id: "generic",
    label: "Other",
    steps: [UNZIP, `Add mcp.json.example to the MCP settings of your client. The address is ${MCP_URL}`, SIGN_IN],
  },
];

export interface KitIndex {
  kit_version: number;
  clients: string[];
}

/** Validate the gateway body. `null` for a bad shape. Unknown clients drop out. */
export function parseIndex(json: unknown): KitIndex | null {
  if (typeof json !== "object" || json === null) return null;
  const o = json as Record<string, unknown>;
  if (typeof o.kit_version !== "number" || !Number.isInteger(o.kit_version) || o.kit_version < 0) return null;
  if (!Array.isArray(o.clients) || !o.clients.every((c) => typeof c === "string")) return null;
  const known = new Set(CLIENT_TABS.map((t) => t.id));
  return { kit_version: o.kit_version, clients: (o.clients as string[]).filter((c) => known.has(c)) };
}

export function kitFileName(client: string, version: number): string {
  return `metorite-agent-kit-${client}-v${version}.zip`;
}

/** The same-origin address of the zip. The session cookie rides along. */
export function kitHref(client: string): string {
  return `/api/agent-kit/${encodeURIComponent(client)}.zip`;
}

/** The text of the copy button. */
export function installText(client: KitClient, version: number): string {
  const lines = client.steps.map((s, i) => `${i + 1}. ${s}`);
  return [`Metorite agent kit for ${client.label}: ${kitFileName(client.id, version)}`, ...lines, KIT_MERGE_NOTE].join("\n");
}

/** `null` when the control must draw nothing: 404, 401, a bad body or a failure. */
export async function readKitIndex(): Promise<KitIndex | null> {
  try {
    const res = await fetch("/api/agent-kit", { cache: "no-store" });
    if (!res.ok) return null;
    const index = parseIndex(await res.json());
    return index && index.clients.length > 0 ? index : null;
  } catch {
    return null;
  }
}
