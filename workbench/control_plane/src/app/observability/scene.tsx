/**
 * Agent role mapping + avatar config type for the observability office.
 *
 * The office renders real Pixel Lab pixel-art from the single CHARACTER_LIBRARY
 * (character-library.generated) via office-topdown.tsx. `roleFor()` maps an agent name to a
 * role so a brand-new agent gets a fitting default character with zero config;
 * `AvatarConfig` is the shape of a per-agent avatar override (now just carries
 * `libraryId` — the assigned library character — plus legacy look fields kept for
 * back-compat with stored overrides).
 *
 * (The old procedural <AgentScene>/Avatar Studio was removed — agents are real
 * sprites now, and avatars are assigned from the character library on the Agents
 * page.)
 */

export interface RoomTheme {
  wall: string; wall2: string; floor: string; floor2: string; rug: string; rug2: string;
}

export interface AvatarConfig {
  skin: string;
  hair: { style: "spiky" | "bun" | "short" | "long"; color: string };
  outfit: { type: "hoodie" | "suit" | "sweater"; color: string; color2: string };
  accessory: "glasses" | "headset" | "beanie" | null;
  deskProps: string[];
  room: RoomTheme;
  wallProp: "window" | "board" | "whiteboard";
  screen: string; screen2: string;
  accentA: string; accentA2: string;
  desk: string; desk2: string;
  /** Optional real pixel-art sprite (data-URI) pinned per-agent (legacy). */
  sprite?: string | null;
  /** Id into the reusable CHARACTER_LIBRARY. When set, the office renders that
   *  library character's full animated set for the agent. */
  libraryId?: string | null;
}

export type Role =
  | "orchestrator" | "coder" | "sales" | "planner" | "triage" | "reconciler" | "default";

/** Map an agent name to a role by keyword — drives the office's default character
 *  (office-topdown ROLE_TO_CHAR) when no library character is assigned. */
export function roleFor(name: string): Role {
  const n = name.toLowerCase();
  if (/(orchestr)/.test(n)) return "orchestrator";
  if (/(cod|dev|apis|engineer|build)/.test(n)) return "coder";
  if (/(sales|biz|deal|crm|zoho)/.test(n)) return "sales";
  if (/(plan|strateg|project|task)/.test(n)) return "planner";
  if (/(triage|email|inbox|mail)/.test(n)) return "triage";
  if (/(reconcil|audit|ledger|finance)/.test(n)) return "reconciler";
  return "default";
}
