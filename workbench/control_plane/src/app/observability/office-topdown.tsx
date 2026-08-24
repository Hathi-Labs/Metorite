"use client";

/**
 * Top-down RPG office — every agent is a real Pixel Lab character SEATED at a desk
 * in one shared, tiled room. Working agents play the seated typing animation; idle
 * ones dim and doze; errors flash red. Sprites come from the single pre-generated
 * CHARACTER_LIBRARY (character-library.generated.ts) — selection only, no runtime
 * generation.
 *
 * The ROOM is self-scaling: the desk grid uses `auto-fill` so it reflows to any agent
 * count AND any viewport (2 columns on a phone → 6+ on a wide desktop) with no JS.
 * The floor is a seamless Pixel Lab tile (office-env.generated.ts) that repeats to
 * fill whatever size the room grows to; props are anchored to the room's corners and
 * wall so they stay correctly placed as it expands/contracts.
 */

import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import React from "react";


// Granular observability: map the tool an agent is CURRENTLY calling to a lucide icon
// shown on the agent (top-right badge). Ordered rules — first match wins, most
// specific first — so any tool name (read_email, gtd_add_task, web_search, git_push…)
// resolves to a sensible icon, falling back to a generic wrench.
type IconType = ThemedIcon;
const TOOL_ICON_RULES: Array<[RegExp, IconType]> = [
  [/send|dispatch|deliver/, themedIcon("Send")],
  [/mail|email|inbox|reply|thread|message/, themedIcon("Mail")],
  [/web|search|browse|google|lookup|find_/, themedIcon("Search")],
  [/diagnos|lint|error|test|verify|check/, themedIcon("Stethoscope")],
  [/git|commit|push|pull|branch|\bpr\b|merge/, themedIcon("GitBranch")],
  [/diagram|draw|chart|render|image|figure/, themedIcon("PenTool")],
  [/artifact|share|export|upload|publish/, themedIcon("Share2")],
  [/task|gtd|todo|ticket|reclarify/, themedIcon("ListTodo")],
  [/calendar|schedule|meeting|event|remind/, themedIcon("Calendar")],
  [/ask|question|clarify|confirm/, themedIcon("MessageCircleQuestion")],
  [/sql|database|postgres|\bdb\b|record/, themedIcon("Database")],
  [/http|fetch|\bapi\b|url|request/, themedIcon("Globe")],
  [/write|edit|create_|update_|save|note|draft/, themedIcon("PencilLine")],
  [/shell|bash|exec|run_|command|terminal|code|python/, themedIcon("Terminal")],
  [/read|get_|open|view|list|file|doc|load|query/, themedIcon("FileText")],
];
function toolIcon(tool: string): IconType {
  const t = tool.toLowerCase();
  for (const [re, Icon] of TOOL_ICON_RULES) if (re.test(t)) return Icon;
  return themedIcon("Wrench");
}

import { CHARACTER_LIBRARY } from "./character-library.generated";
import { OFFICE_ENV } from "./office-env.generated";
import { OBJ_SIZES } from "./office-object-sizes.generated";
import { OFFICE_OBJECTS, type Dir } from "./office-objects.generated";
import { roleFor } from "./scene";

export type OfficeState = "working" | "idle" | "error";

interface OfficeAgent {
  /** "agent" (default) | "app" — a Custom App currently consuming AI tokens
   *  (RFC docs/app-workshop/README.md §4.6). Renders as a terminal kiosk
   *  (AppSeat), not a character sprite — an app has no human to animate. */
  kind?: "agent" | "app" | string;
  name: string;
  /** Friendly alias for the visible nameplate; `name` stays the avatar key. */
  display_name?: string;
  description?: string;
  status?: string;
  source?: string | null;
  // An operator can assign a reusable library character (Avatar Studio) via the
  // avatar override; when set, the office uses that character's full animated set
  // (seated / typing / sleeping / breathing) instead of the role default.
  avatar?: { config?: { libraryId?: string | null } | null } | null;
  /** kind="app" only — the app's emoji icon (from the apps table). */
  icon?: string;
}

// Every FLOOR object is drawn at native_size * ONE scale, so all objects share the
// same pixel density and keep their TRUE relative sizes (this is what makes a small
// plant vs a big bookshelf read correctly instead of hand-tuned per object). Height
// comes from the object's native cropped size for the rendered direction.
const OBJ_SCALE = 0.92;
function objHeight(obj: string, dir: string): number {
  const s = OBJ_SIZES[obj]?.[dir];
  return Math.round((s ? s[1] : 60) * OBJ_SCALE);
}
// Wall-mounted fixtures are sized to the wall band explicitly (not floor-scaled).
const WALL_FIX_H: Record<string, number> = {
  blackboard: 36, "notice-board": 36, "tv-screen": 32, "wall-clock": 32,
  window: 44,
};
// Optional explicit width override (else width follows the aspect). The window is
// kept wide but flatter — same width as before, shorter.
const WALL_FIX_W: Record<string, number> = { window: 61 };

// Furniture is grouped into wall-hugging CLUSTERS, each rendered as a flex box so its
// members auto-space with a fixed gap (no manual overlap math) and sit on a common
// baseline. Sprites are pre-cropped to content (crop_objects.py) so the flex box sits
// FLUSH against its wall. Top/bottom clusters are horizontal rows; left/right clusters
// are vertical columns centered on the wall. Each item faces sensibly for its wall:
// top -> south (front), left -> east (faces right), right -> west (faces left), round
// tables use south (a round table reads the same from any side, front is cleanest),
// bottom -> north / diagonals. Missing objects (not yet generated) are skipped.
type ClItem = { obj: string; dir: Dir };
type Cluster = { id: string; cls: string; items: ClItem[] };
const CLUSTERS: Cluster[] = [
  // TOP-LEFT (BACK) — library + coffee counter. Front-facing pieces live on the back
  // wall (the coffee counter is front-facing, so it belongs here, not on a side wall).
  {
    id: "library",
    cls: "oc-cl-tl",
    items: [
      { obj: "bookshelf", dir: "south" },
      { obj: "bookshelf-wide", dir: "south" },
      { obj: "shelf-files", dir: "south" },
      { obj: "counter-coffee", dir: "south" },
      { obj: "water-cooler", dir: "south" }, // next to the coffee counter, front view
    ],
  },
  // TOP-RIGHT (BACK) — workstation corner (computer sits ON a desk)
  {
    id: "workstation",
    cls: "oc-cl-tr",
    items: [
      { obj: "desk-computer", dir: "south" },
      { obj: "printer-office", dir: "south" },
    ],
  },
  // LEFT WALL — lounge (couch + round coffee table + armchair)
  {
    id: "lounge",
    cls: "oc-cl-lm",
    items: [
      { obj: "couch", dir: "east" },
      { obj: "side-table", dir: "south" },
      { obj: "armchair", dir: "east" },
    ],
  },
  // RIGHT WALL — supply column
  {
    id: "supply",
    cls: "oc-cl-rm",
    items: [
      { obj: "filing-cabinet", dir: "west" },
      { obj: "plant-monstera", dir: "west" },
    ],
  },
  // BOTTOM-LEFT — chill nook + greenery (plant sits ON a table)
  {
    id: "grove-l",
    cls: "oc-cl-bl",
    items: [
      { obj: "beanbag", dir: "north-east" },
      { obj: "plant-cactus", dir: "north" },
      { obj: "table-plant", dir: "south" },
    ],
  },
  // BOTTOM-RIGHT — plant grove
  {
    id: "grove-r",
    cls: "oc-cl-br",
    items: [
      { obj: "plant-tall", dir: "north-west" },
      { obj: "plant-palm", dir: "north" },
      { obj: "plant-small", dir: "north" },
      { obj: "plant-hanging", dir: "north-east" },
    ],
  },
];

// Back-wall mounted fixtures (front-facing), laid along the top wall band. The window
// (scenery) sits among them.
const WALL_FIXTURES: ClItem[] = [
  { obj: "window", dir: "south" },
  { obj: "blackboard", dir: "south" },
  { obj: "notice-board", dir: "south" },
  { obj: "tv-screen", dir: "south" },
  { obj: "wall-clock", dir: "south" },
];
// Pool of fixtures for the conference-room wall; each room picks 2 (deterministically
// from its members, so it's stable per room across renders but varies between rooms).
const CR_FIX_POOL = ["tv-screen", "wall-clock", "notice-board", "blackboard"];
const CR_PLANT_POOL = ["plant-tall", "plant-palm", "plant-monstera", "plant-cactus"];
function _hash(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function crFixturesFor(members: OfficeAgent[]): string[] {
  const start = _hash(members.map((m) => m.name).join("|")) % CR_FIX_POOL.length;
  return [CR_FIX_POOL[start], CR_FIX_POOL[(start + 1) % CR_FIX_POOL.length]];
}
// Two (different) plants to flank the conference table, deterministic per room.
function crPlantsFor(members: OfficeAgent[]): string[] {
  const start = _hash(members.map((m) => m.name).join("#")) % CR_PLANT_POOL.length;
  return [CR_PLANT_POOL[start], CR_PLANT_POOL[(start + 2) % CR_PLANT_POOL.length]];
}

/** Map an agent to a seated-character key: exact match wins, else its role's. */
const ROLE_TO_CHAR: Record<string, string> = {
  orchestrator: "orchestrator",
  coder: "apis-config",
  sales: "sales",
  planner: "task-manager",
  triage: "email-assistant",
  reconciler: "reconciler",
  default: "strategy",
};
export function characterFor(name: string): string {
  if (CHARACTER_LIBRARY[name]) return name;
  return ROLE_TO_CHAR[roleFor(name)] ?? "strategy";
}

// Resolve the sprite set an agent renders with: a character explicitly assigned in
// the Agent Settings picker wins (its full seated/typing/sleeping/breathing set);
// otherwise fall back to the agent's default character. Everything — the role library
// AND the original per-agent avatars — lives in the ONE CHARACTER_LIBRARY.
type CastEntry = Partial<(typeof CHARACTER_LIBRARY)[string]>;
function castFor(agent: OfficeAgent): CastEntry | undefined {
  const libId = agent.avatar?.config?.libraryId;
  if (libId && CHARACTER_LIBRARY[libId]) return CHARACTER_LIBRARY[libId];
  return CHARACTER_LIBRARY[characterFor(agent.name)];
}

// A small potted plant tucked beside a desk (front-facing sprite), used to green up
// the desk grid. Rotates through the small-plant variants so the room isn't uniform.
const DESK_PLANTS = ["plant-small", "plant-cactus", "plant-monstera"];

function Seat({
  agent,
  state,
  onOpen,
  plant,
  tool,
}: {
  agent: OfficeAgent;
  state: OfficeState;
  onOpen: (name: string) => void;
  plant?: string | null;
  tool?: string | null;
}) {
  const c = castFor(agent);
  // Working agents play the seated TYPING animation (custom v3 — keeps the seated
  // pose). Idle/error use the static seated sprite with CSS breathe/shake. (The
  // breathing-idle TEMPLATE animates a standing skeleton, so it isn't used.)
  const playTyping = state === "working" && Boolean(c?.working && c?.workingFrames);
  return (
    <button
      onClick={() => onOpen(agent.name)}
      className={`oc-seat oc-${state}`}
      title={agent.description || agent.name}
    >
      <div className="oc-figure">
        {playTyping ? (
          <span
            className="oc-anim"
            style={{ "--sheet": `url(${c?.working})`, "--n": c?.workingFrames } as React.CSSProperties}
          />
        ) : (
          // idle → asleep-at-desk sprite (falls back to the plain seated sprite)
          // eslint-disable-next-line @next/next/no-img-element
          <img
            className="oc-static"
            src={state === "idle" && c?.sleeping ? c.sleeping : c?.seated}
            alt={agent.name}
          />
        )}
        {/* Status is shown ONLY by a small icon at the top-right that floats up and
            fades (inspired by the sleep "z"): z when sleeping, a cog when working, an
            alert triangle when errored. No text pill — the agent's pose says the rest. */}
        {state === "idle" && <span className="oc-badge oc-b-idle">z</span>}
        {state === "working" && (
          // The badge is the CURRENT TOOL's icon (granular observability); a generic
          // cog when the tool is unknown. createElement (not JSX) so the dynamic
          // lucide component isn't flagged as a render-local component.
          <span className="oc-badge oc-b-working" title={tool ? `Using ${tool}` : "working"}>
            {React.createElement(tool ? toolIcon(tool) : themedIcon("Cog"), { size: 12, strokeWidth: 2.5 })}
          </span>
        )}
        {state === "error" && (
          <span className="oc-badge oc-b-error">
            <AppIcon name="TriangleAlert" size={12} strokeWidth={2.5} />
          </span>
        )}
        {plant && (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="oc-seat-plant" src={plant} alt="" aria-hidden />
        )}
      </div>
      <div className="oc-plate">
        {/* the agent NAME now lives in the pill (styled like the old status indicator) */}
        <span className="oc-name">{agent.display_name || agent.name}</span>
      </div>
    </button>
  );
}

/**
 * A Custom App currently consuming AI tokens, rendered as a small glowing
 * terminal kiosk (its emoji icon on screen) rather than a character sprite —
 * an app has no human to animate, and forcing it into the seated-worker
 * sprite pipeline would need pixel-art assets that don't exist for it. Fits
 * the SAME `.oc-seat`/`.oc-figure`/`.oc-plate` grid cell as `Seat` so it
 * drops into the desk grid without any layout changes.
 */
function AppSeat({
  agent,
  onOpen,
}: {
  agent: OfficeAgent;
  onOpen: (name: string) => void;
}) {
  return (
    <button
      onClick={() => onOpen(agent.name)}
      className="oc-seat oc-working"
      title={agent.description || agent.display_name || agent.name}
    >
      <div className="oc-figure">
        <div className="oc-kiosk">
          <span className="oc-kiosk-screen">{agent.icon || "🧩"}</span>
          <span className="oc-kiosk-stand" aria-hidden />
        </div>
        <span className="oc-badge oc-b-app" title="Consuming AI tokens">
          <AppIcon name="Coins" size={12} strokeWidth={2.5} />
        </span>
      </div>
      <div className="oc-plate">
        <span className="oc-name">{agent.display_name || agent.name}</span>
      </div>
    </button>
  );
}

/** Rotating "who's speaking" index for a room — cycles every ~1.9s (client only, so
 *  starts at 0 on the server to avoid a hydration mismatch). */
function useSpeaker(count: number): number {
  const [i, setI] = React.useState(0);
  React.useEffect(() => {
    if (count < 2) return;
    const id = setInterval(() => setI((n) => n + 1), 1900);
    return () => clearInterval(id);
  }, [count]);
  return count ? i % count : 0;
}

/** One agent STANDING at the conference table, facing inward, gently breathing. The
 *  current speaker leans in and pops a chat bubble — the discussion passing around. */
function CrStandee({
  agent,
  dir,
  speaking,
  onOpen,
}: {
  agent: OfficeAgent;
  dir: "south" | "north" | "east" | "west";
  speaking?: boolean;
  onOpen: (name: string) => void;
}) {
  const c = castFor(agent);
  const stand = c?.standing as Record<string, string | undefined> | undefined;
  // Prefer the real per-direction breathing spritesheet; fall back to the static
  // standing sprite (with a CSS breathe) until the frames are generated.
  const breathe = c?.breathing?.[dir as "south" | "north"];
  const nFrames = c?.breathingFrames;
  const staticSrc = stand?.[dir] ?? c?.seated;
  if (!breathe && !staticSrc) return null;
  return (
    <button
      className={`oc-cr-standee${speaking ? " oc-cr-speaking" : ""}`}
      onClick={() => onOpen(agent.name)}
      title={agent.description || agent.name}
    >
      {speaking && (
        <span className="oc-cr-bubble">
          <AppIcon name="MessageCircle" size={11} strokeWidth={2.6} />
        </span>
      )}
      {breathe && nFrames ? (
        <span
          className="oc-cr-standanim"
          style={{ "--sheet": `url(${breathe})`, "--n": nFrames } as React.CSSProperties}
        />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="oc-cr-standimg" src={staticSrc} alt={agent.name} />
      )}
    </button>
  );
}

/**
 * A conference room that spawns dynamically when 2+ agents collaborate: a smaller
 * room sharing the office aesthetic, with the collaborating agents STANDING around a
 * centred conference table (breathing). Far-side agents face down (south), near-side
 * agents face up (north). Renders as a separate card below the office.
 */
function ConferenceRoom({
  members,
  onOpen,
}: {
  members: OfficeAgent[];
  onOpen: (name: string) => void;
}) {
  const speaker = useSpeaker(members.length);
  const idx = members.map((a, i) => ({ a, i }));
  const top = idx.filter(({ i }) => i % 2 === 0);
  const bottom = idx.filter(({ i }) => i % 2 === 1);
  return (
    <div className="oc-cr">
      <div className="oc-cr-wall">
        <span className="oc-cr-sign">{members.map((m) => m.display_name || m.name).join(" + ")}</span>
        <div className="oc-cr-fix">
          {crFixturesFor(members).map((obj) => {
            const src = OFFICE_OBJECTS[obj]?.south;
            if (!src) return null;
            return (
              // eslint-disable-next-line @next/next/no-img-element
              <img key={obj} className={`oc-cr-fiximg oc-fix-${obj}`} src={src} alt="" aria-hidden />
            );
          })}
        </div>
      </div>
      <div className="oc-cr-floor">
        <div className="oc-cr-row oc-cr-row-top">
          {top.map(({ a, i }) => (
            <CrStandee key={a.name} agent={a} dir="south" speaking={i === speaker} onOpen={onOpen} />
          ))}
        </div>
        {/* plants flanking the table, matching the office aesthetic */}
        {crPlantsFor(members).map((obj, i) => {
          const src = OFFICE_OBJECTS[obj]?.[i === 0 ? "north-east" : "north-west"] ?? OFFICE_OBJECTS[obj]?.south;
          if (!src) return null;
          return (
            <img
              key={`${obj}-${i}`}
              className={`oc-cr-plant ${i === 0 ? "oc-cr-plant-l" : "oc-cr-plant-r"}`}
              src={src}
              alt=""
              aria-hidden
              // eslint-disable-next-line @next/next/no-img-element
            />
          );
        })}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          className="oc-cr-table"
          src={OFFICE_OBJECTS["conference-table"]?.south ?? "/office-props/conference-table.png"}
          alt=""
          aria-hidden
        />
        {/* the shared topic on the table — a soft pulse the discussion revolves around */}
        <span className="oc-cr-focus" aria-hidden />
        <div className="oc-cr-row oc-cr-row-bottom">
          {bottom.map(({ a, i }) => (
            <CrStandee key={a.name} agent={a} dir="north" speaking={i === speaker} onOpen={onOpen} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function TopDownOffice({
  roster,
  hotAgents,
  agentTools,
  todayCost,
  fmtCost,
  onOpen,
}: {
  roster: OfficeAgent[];
  hotAgents: Set<string>;
  /** name -> the tool that agent is CURRENTLY calling (granular observability). */
  agentTools?: Record<string, string>;
  todayCost: number;
  fmtCost: (n?: number | null) => string;
  onOpen: (name: string) => void;
}) {
  const stateOf = (a: OfficeAgent): OfficeState => {
    if (a.status === "error") return "error";
    if (a.status === "working" || hotAgents.has(a.name)) return "working";
    return "idle";
  };
  const workingCount = roster.filter((a) => stateOf(a) === "working").length;

  // Collaborations → conference rooms. When 2+ agents work concurrently they pair up
  // (~2-3 per room) and are shown meeting in a separate conference-room card instead
  // of at their desks; nothing shows when nobody is collaborating. (A real backend
  // collaboration signal can replace this heuristic later.) Apps never join a
  // conference room — CrStandee needs a standing/breathing sprite set that only
  // characters have; an app just keeps consuming tokens at its desk.
  const workingAgents = roster
    .filter((a) => stateOf(a) === "working" && a.kind !== "app")
    .sort((a, b) => a.name.localeCompare(b.name));
  const collabs: OfficeAgent[][] = [];
  if (workingAgents.length >= 2) {
    for (let i = 0; i < workingAgents.length; i += 2) collabs.push(workingAgents.slice(i, i + 2));
    const last = collabs[collabs.length - 1];
    if (collabs.length > 1 && last.length === 1) collabs[collabs.length - 2].push(collabs.pop()![0]);
  }
  // Agents are not mutually exclusive: every agent always keeps its desk in the office
  // AND also appears in a conference room while collaborating (multiple instances).

  // Seamless generated floor tile (repeats to fill any room size); undefined until
  // the Pixel Lab tileset is built, in which case CSS falls back to a procedural floor.
  const roomStyle = OFFICE_ENV.floor
    ? ({
        "--oc-floor": `url(${OFFICE_ENV.floor})`,
        "--oc-floor-bg": OFFICE_ENV.floorBg ?? "auto",
        ...(OFFICE_ENV.wall
          ? { "--oc-wall": `url(${OFFICE_ENV.wall})`, "--oc-wall-bg": OFFICE_ENV.wallBg ?? "auto" }
          : {}),
        ...(OFFICE_ENV.lane
          ? { "--oc-lane": `url(${OFFICE_ENV.lane})`, "--oc-lane-bg": OFFICE_ENV.laneBg ?? "auto" }
          : {}),
      } as React.CSSProperties)
    : undefined;

  return (
    // pb clears the fixed mobile bottom-nav bar (+ iOS safe area) so the last row
    // of desks isn't hidden behind it; no extra padding on desktop.
    <div className="h-full min-h-0 overflow-y-auto pb-24 sm:pb-2">
      <div className="flex items-center justify-between mb-3">
        <div className="oc-mono text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <AppIcon name="Building2" size={13} /> The Office · {workingCount}/{roster.length} at work
        </div>
        <div className="oc-mono text-[11px] text-emerald-500 flex items-center gap-1">
          <AppIcon name="Coins" size={12} /> Today {fmtCost(todayCost)}
        </div>
      </div>
      {roster.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 text-center gap-2">
          <AppIcon name="Building2" size={30} className="text-muted-foreground/50" />
          <p className="oc-mono text-sm text-muted-foreground">No agents registered yet.</p>
        </div>
      ) : (
        <div className="oc-office" style={roomStyle}>
          <div className="oc-room">
          <div className="oc-wall">
            <span className="oc-sign">THE OFFICE</span>
            <div className="oc-wall-fix">
              {WALL_FIXTURES.map((it) => {
                const src = OFFICE_OBJECTS[it.obj]?.[it.dir];
                if (!src) return null;
                return (
                  <span
                    key={it.obj}
                    className={`oc-fix oc-fix-${it.obj}`}
                    style={{ height: WALL_FIX_H[it.obj] ?? 34, width: WALL_FIX_W[it.obj] }}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={src}
                      alt=""
                      aria-hidden
                      style={{ height: WALL_FIX_H[it.obj] ?? 34, width: WALL_FIX_W[it.obj] }}
                    />
                  </span>
                );
              })}
            </div>
          </div>
          <div className="oc-floor">
            {CLUSTERS.map((cl) => (
              <div key={cl.id} className={`oc-cluster ${cl.cls}`}>
                {cl.items.map((it, i) => {
                  const src = OFFICE_OBJECTS[it.obj]?.[it.dir];
                  if (!src) return null;
                  return (
                    <img
                      key={`${it.obj}-${i}`}
                      className="oc-obj"
                      style={{ height: objHeight(it.obj, it.dir) }}
                      src={src}
                      alt=""
                      aria-hidden
                      // eslint-disable-next-line @next/next/no-img-element
                    />
                  );
                })}
              </div>
            ))}
            <div className="oc-grid">
              {roster.map((a, i) => {
                if (a.kind === "app") {
                  return <AppSeat key={a.name} agent={a} onOpen={onOpen} />;
                }
                // Green up ~every 3rd desk with a small plant (variant rotates).
                const plantKey = i % 3 === 1 ? DESK_PLANTS[(i >> 1) % DESK_PLANTS.length] : null;
                const plant = plantKey ? OFFICE_OBJECTS[plantKey]?.south ?? null : null;
                return (
                  <Seat
                    key={a.name}
                    agent={a}
                    state={stateOf(a)}
                    onOpen={onOpen}
                    plant={plant}
                    tool={agentTools?.[a.name] ?? null}
                  />
                );
              })}
            </div>
          </div>
          </div>
          {collabs.length > 0 && (
            <div className="oc-confs">
              <div className="oc-confs-head">
                <AppIcon name="Building2" size={12} /> {collabs.length === 1 ? "Conference room" : "Conference rooms"} ·{" "}
                {collabs.length} in session
              </div>
              <div className="oc-confs-grid">
                {collabs.map((members, i) => (
                  <ConferenceRoom
                    key={members.map((m) => m.name).join("|") || `cr-${i}`}
                    members={members}
                    onOpen={onOpen}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Injected once by the page. */
export const TOPDOWN_STYLE = `
.oc-mono { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; letter-spacing:.02em; }

/* Office wrapper — carries the shared floor/wall CSS vars so both the main room and
   the conference-room cards inherit the same tileset. Capped + centred so on a wide
   monitor it stays a RECTANGLE (~6 desks across) instead of stretching into a strip. */
.oc-office { display:block; max-width:900px; margin-inline:auto; }

/* Outer room = the walls. Bright startup-office palette (light wood + beige). */
.oc-room {
  position:relative; border-radius:16px; overflow:hidden;
  background:linear-gradient(#efe9dd,#e6ddcd);
  border:5px solid #cbbfa4;
  box-shadow: inset 0 0 0 3px #f5f0e4, 0 10px 30px rgba(0,0,0,.28);
}
/* Top wall band — the room sign plus the mounted fixtures (blackboard, notice board,
   TV, clock). */
.oc-wall {
  position:relative; height:58px; z-index:3;
  background:
    linear-gradient(rgba(255,255,255,.22), rgba(0,0,0,.10)),
    var(--oc-wall, linear-gradient(#e7dbc2,#d7c7a6));
  background-size: cover, var(--oc-wall-bg,auto);
  background-repeat: no-repeat, repeat;
  image-rendering:pixelated;
  border-bottom:3px solid #b6a67f;
  display:flex; align-items:center; gap:14px; padding:0 18px;
  box-shadow: inset 0 -6px 12px rgba(0,0,0,.10);
}
.oc-sign { flex-shrink:0; font-family:ui-monospace,monospace; font-size:12px; letter-spacing:.28em;
  color:#6f5f3c; text-shadow:0 1px 0 rgba(255,255,255,.5); }
/* fixtures hang on the wall, right-aligned, mounted HIGH on the band so floor
   furniture can sit against the wall below them */
.oc-wall-fix { margin-left:auto; display:flex; align-items:flex-start; gap:20px; height:100%;
  padding-top:3px; }
.oc-fix { display:inline-flex; align-items:flex-end; image-rendering:pixelated;
  filter:drop-shadow(0 2px 2px rgba(0,0,0,.30)); }
.oc-fix img { display:block; image-rendering:pixelated; }
/* the TV is "on": a gentle screen-glow pulse */
.oc-fix-tv-screen img { animation: oc-tv 2.6s ease-in-out infinite; }
@keyframes oc-tv { 0%,100%{filter:brightness(1)} 50%{filter:brightness(1.22) saturate(1.25)} }

/* Floor — the darker lane tile frames the office as a thin BORDER (matching the
   conference-room cards); an inset ::before field carries the main floor tile. */
.oc-floor {
  position:relative; padding:56px 82px 46px;
  background-color:#cbb89a;
  background-image: var(--oc-lane, none);
  background-size: var(--oc-lane-bg, auto);
  background-repeat: repeat;
  image-rendering:pixelated;
}
.oc-floor::before {
  content:''; position:absolute; inset:16px; z-index:0; pointer-events:none;
  background-image: var(--oc-floor, none);
  background-size: var(--oc-floor-bg, auto);
  background-repeat: repeat; image-rendering:pixelated; border-radius:4px;
  box-shadow: inset 0 0 0 2px rgba(0,0,0,.10), inset 0 10px 18px rgba(0,0,0,.05);
}

/* Furniture clusters — each is a flex box anchored to a wall/corner so its members
   auto-space (fixed gap, no overlap) and sit on a common baseline. Each object's height
   is its native cropped size * OBJ_SCALE, so pixel density is uniform across objects. */
.oc-cluster { position:absolute; z-index:1; display:flex; align-items:flex-end; gap:6px;
  pointer-events:none; }
.oc-obj { display:block; image-rendering:pixelated;
  filter:drop-shadow(0 4px 3px rgba(0,0,0,.30)); }
/* Back (top) clusters overlap the wall band slightly and sit ABOVE it (z-index > the
   wall's) so they render IN FRONT of the wall for a 3D "standing against the wall"
   effect (not clipped behind it). The floor has no stacking context, so this z-index
   competes directly with the wall band. Side/bottom clusters stay flush + in front. */
.oc-cl-tl { top:-8px; left:10px; z-index:5; }
.oc-cl-tr { top:-8px; right:10px; z-index:5; }
.oc-cl-bl { bottom:4px; left:10px; }
.oc-cl-br { bottom:4px; right:10px; }
.oc-cl-lm { top:47%; left:6px; transform:translateY(-50%);
  flex-direction:column; align-items:flex-start; gap:5px; }
.oc-cl-rm { top:47%; right:6px; transform:translateY(-50%);
  flex-direction:column; align-items:flex-end; gap:5px; }
/* small potted plant tucked beside a desk to green up the grid */
.oc-seat-plant { position:absolute; right:2px; bottom:12px; height:38px; z-index:2;
  image-rendering:pixelated; filter:drop-shadow(0 3px 2px rgba(0,0,0,.45)); }

/* Conference rooms — separate cards below the office that spawn dynamically when
   2+ agents collaborate. Each is a smaller room sharing the office tiles/walls, with
   the collaborating agents seated together across a shared table. */
.oc-confs { margin-top:18px; }
.oc-confs-head { font-family:ui-monospace,monospace; font-size:11px; letter-spacing:.04em;
  text-transform:uppercase; color:#8a7c5c; display:flex; align-items:center; gap:6px;
  margin:0 2px 8px; }
.oc-confs-grid { display:flex; flex-wrap:wrap; gap:16px; }
/* three rooms per row, filling the office width with the row gaps between them */
.oc-cr { flex:1 1 220px; max-width:calc(33.333% - 11px); border-radius:14px;
  overflow:hidden; border:4px solid #cbbfa4;
  box-shadow: inset 0 0 0 2px #f5f0e4, 0 8px 22px rgba(0,0,0,.24);
  background:linear-gradient(#efe9dd,#e6ddcd); }
.oc-cr-wall { position:relative; z-index:2; height:52px; display:flex; align-items:center;
  gap:10px; padding:0 12px;
  background: linear-gradient(rgba(255,255,255,.22), rgba(0,0,0,.10)),
    var(--oc-wall, linear-gradient(#e7dbc2,#d7c7a6));
  background-size: cover, var(--oc-wall-bg,auto); background-repeat:no-repeat, repeat;
  image-rendering:pixelated; border-bottom:3px solid #b6a67f; }
/* the collaboration name in a readable pill (matching the agent name pills) */
.oc-cr-sign { flex-shrink:1; min-width:0; max-width:calc(100% - 60px);
  font-family:ui-monospace,monospace; font-size:10px; line-height:1.25;
  letter-spacing:.1em; text-transform:uppercase; font-weight:700; color:#2c2a24;
  padding:3px 9px; border-radius:7px; border:1px solid rgba(0,0,0,.16);
  background:rgba(255,255,255,.8); box-shadow:0 1px 2px rgba(0,0,0,.14);
  text-shadow:0 1px 1px rgba(255,255,255,.6);
  overflow:hidden; white-space:normal; display:-webkit-box; -webkit-box-orient:vertical;
  -webkit-line-clamp:2; }
.oc-cr-fix { margin-left:auto; flex-shrink:0; display:flex; align-items:center; gap:7px; }
.oc-cr-fiximg { height:22px; display:block; image-rendering:pixelated;
  filter:drop-shadow(0 2px 2px rgba(0,0,0,.28)); }
/* generic TV-screen glow pulse (wall band uses .oc-fix-tv-screen img above) */
img.oc-fix-tv-screen { animation: oc-tv 2.6s ease-in-out infinite; }
.oc-cr-floor { position:relative; height:200px; overflow:hidden;
  background-color:#cbb89a; background-image: var(--oc-lane, none);
  background-size: var(--oc-lane-bg, auto); background-repeat:repeat; image-rendering:pixelated; }
.oc-cr-floor::before { content:''; position:absolute; inset:10px; z-index:0; pointer-events:none;
  background-image: var(--oc-floor, none); background-size: var(--oc-floor-bg, auto);
  background-repeat:repeat; image-rendering:pixelated; border-radius:4px;
  box-shadow: inset 0 0 0 2px rgba(0,0,0,.10), inset 0 10px 18px rgba(0,0,0,.05); }
/* agents STANDING around the centred table, facing inward, gently breathing */
.oc-cr-row { position:absolute; left:0; right:0; display:flex; justify-content:center;
  align-items:flex-end; gap:10px; pointer-events:none; }
.oc-cr-row-top { top:8px; z-index:1; }         /* far side — behind the table */
.oc-cr-row-bottom { bottom:24px; z-index:3; }  /* near side — up against the table */
.oc-cr-standee { position:relative; background:none; border:none; cursor:pointer;
  padding:0; line-height:0; pointer-events:auto; }
.oc-cr-standimg { height:90px; display:block; image-rendering:pixelated;
  filter:drop-shadow(0 4px 3px rgba(0,0,0,.4));
  animation: oc-breathe 3.4s ease-in-out infinite; }
/* real breathing spritesheet (N square frames wide) stepped gently */
.oc-cr-standanim { --w:90px; display:block; width:var(--w); height:var(--w);
  background-image:var(--sheet); background-repeat:no-repeat;
  background-size:calc(var(--n) * var(--w)) var(--w); background-position:0 0;
  image-rendering:pixelated; filter:drop-shadow(0 4px 3px rgba(0,0,0,.4));
  animation: oc-play calc(var(--n) * .2s) steps(var(--n)) infinite; }
.oc-cr-standee:hover { transform:translateY(-3px); transition:transform .15s; }
/* discussion: the current speaker leans in + pops a chat bubble above the head */
.oc-cr-speaking { transform:translateY(-4px) scale(1.07); transition:transform .3s; z-index:4; }
.oc-cr-bubble { position:absolute; top:4px; left:66%; margin-left:-9px;
  width:18px; height:18px; border-radius:50% 50% 50% 3px; display:flex;
  align-items:center; justify-content:center; color:#3a6ea5;
  background:rgba(255,255,255,.95); border:1px solid rgba(0,0,0,.16);
  box-shadow:0 1px 2px rgba(0,0,0,.28); z-index:5; animation:oc-pop .25s ease-out; }
@keyframes oc-pop { 0%{opacity:0;transform:scale(.4)} 100%{opacity:1;transform:scale(1)} }
/* the shared topic — a soft blue pulse centred on the table the discussion circles */
.oc-cr-focus { position:absolute; left:50%; top:50%; z-index:2; pointer-events:none;
  width:46px; height:46px; margin:-23px 0 0 -23px; border-radius:50%;
  background:radial-gradient(circle, rgba(90,150,255,.34), rgba(90,150,255,0) 70%);
  animation:oc-focus 2.4s ease-in-out infinite; }
@keyframes oc-focus { 0%,100%{opacity:.25;transform:scale(.8)} 50%{opacity:.6;transform:scale(1.15)} }
/* the shared conference table, centred in the room */
.oc-cr-table { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
  z-index:2; width:min(112px, 44%); image-rendering:pixelated;
  filter:drop-shadow(0 5px 5px rgba(0,0,0,.30)); }
/* plants standing in the room's bottom corners (table draws in front of their base) */
.oc-cr-plant { position:absolute; bottom:8px; z-index:1; height:62px; image-rendering:pixelated;
  filter:drop-shadow(0 3px 3px rgba(0,0,0,.3)); pointer-events:none; }
.oc-cr-plant-l { left:2px; }
.oc-cr-plant-r { right:2px; }

/* Desk grid: auto-fill => reflows to agent count AND viewport with no JS. Tight
   cells + gap pack the agents' desks close together. */
.oc-grid { position:relative; z-index:2;
  display:grid; grid-template-columns:repeat(auto-fill, minmax(122px, 1fr));
  gap:0 2px; justify-items:center; }

.oc-seat { position:relative; display:flex; flex-direction:column; align-items:center;
  background:none; border:none; cursor:pointer; padding:2px 0; width:100%; }
.oc-figure { position:relative; height:116px; display:flex; align-items:flex-end; justify-content:center; }
.oc-static, .oc-anim { image-rendering:pixelated; filter:drop-shadow(0 5px 4px rgba(0,0,0,.55)); }
.oc-static { height:116px; }
/* animated sprite: the seated typing spritesheet (N frames wide) played via steps */
.oc-anim { --w:116px; display:block; width:var(--w); height:var(--w);
  background-image:var(--sheet); background-repeat:no-repeat;
  background-size:calc(var(--n) * var(--w)) var(--w); background-position:0 0;
  animation: oc-play calc(var(--n) * .12s) steps(var(--n)) infinite; }
.oc-idle .oc-static { animation: oc-breathe 3.4s ease-in-out infinite; }
.oc-working .oc-static { animation: oc-bob .8s steps(2) infinite; }
.oc-idle .oc-figure { filter:grayscale(.5) brightness(.72); }
/* (error no longer shakes — the floating alert-triangle badge conveys it) */
.oc-seat:hover .oc-figure { transform:translateY(-3px); transition:transform .15s; }

/* Status badge — the ONLY status cue: a small icon at the agent's top-right that
   floats up and fades on a loop (inspired by the sleep "z"). z = sleeping,
   cog = working/thinking, alert-triangle = error. No text pill. */
.oc-badge { position:absolute; top:22px; right:22px; z-index:3; display:flex;
  align-items:center; justify-content:center; line-height:0;
  width:22px; height:22px; border-radius:50%;
  background:rgba(255,255,255,.95); border:1px solid rgba(0,0,0,.16);
  box-shadow:0 1px 2px rgba(0,0,0,.28);
  animation: oc-float 2.6s ease-in-out infinite; }
/* the sleeping "z" floats on its own — no bubble */
.oc-b-idle { font-family:ui-monospace,monospace; font-size:13px; color:#6b7280;
  width:auto; height:auto; background:none; border:none; box-shadow:none;
  text-shadow:0 1px 1px rgba(255,255,255,.6); }
.oc-b-working { color:#2a7fff; }
.oc-b-error { color:#e0392f; animation-duration:1.5s; }
.oc-b-app { color:#1fae66; }

/* App kiosk — a Custom App consuming AI tokens, no character sprite (there is
   no human to animate). A small glowing terminal on a stand, sized to sit
   inside the same .oc-figure box a character sprite would occupy. */
.oc-kiosk { position:relative; width:88px; height:88px; display:flex;
  flex-direction:column; align-items:center; justify-content:flex-end;
  filter:drop-shadow(0 5px 4px rgba(0,0,0,.5)); }
.oc-kiosk-screen { display:flex; align-items:center; justify-content:center;
  width:72px; height:56px; border-radius:6px; image-rendering:pixelated;
  background:linear-gradient(#1c2430,#0f151c); border:3px solid #3a4656;
  box-shadow: inset 0 0 10px rgba(80,180,255,.35), 0 0 14px rgba(80,180,255,.25);
  font-size:26px; line-height:1; animation: oc-kiosk-glow 2.2s ease-in-out infinite; }
.oc-kiosk-stand { width:26px; height:14px; margin-top:2px; border-radius:2px;
  background:linear-gradient(#4a5566,#2c3440); }
@keyframes oc-kiosk-glow {
  0%,100% { box-shadow: inset 0 0 10px rgba(80,180,255,.35), 0 0 14px rgba(80,180,255,.25); }
  50% { box-shadow: inset 0 0 14px rgba(80,180,255,.55), 0 0 20px rgba(80,180,255,.4); }
}
@media (prefers-reduced-motion: reduce){ .oc-kiosk-screen { animation:none !important; } }

.oc-plate { margin-top:-2px; text-align:center; z-index:3; }
/* the agent NAME styled as a readable pill (like the old status indicator) */
.oc-name { display:inline-block; max-width:132px; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; font-family:ui-monospace,monospace; font-size:11px; font-weight:700;
  color:#2c2a24; padding:1px 8px; border-radius:6px; border:1px solid rgba(0,0,0,.14);
  background:rgba(255,255,255,.72); box-shadow:0 1px 2px rgba(0,0,0,.08);
  text-shadow:0 1px 1px rgba(255,255,255,.6); }

/* Compact the room on small screens: smaller sprites, tighter grid, fewer props. */
@media (max-width:560px){
  .oc-grid { grid-template-columns:repeat(auto-fill, minmax(112px, 1fr)); gap:4px 6px; }
  .oc-figure { height:104px; }
  .oc-static { height:104px; }
  .oc-anim { --w:104px; }
  .oc-floor { padding:44px 16px 40px; }
  .oc-floor::before { inset:14px; }
  /* the side gutters collapse on mobile, so drop the left/right-wall clusters */
  .oc-cl-lm, .oc-cl-rm { display:none; }
  .oc-cluster { gap:5px; }
  .oc-obj { height:38px !important; }
  .oc-seat-plant { height:30px; }
  .oc-wall-fix { gap:10px; }
  .oc-sign { display:none; }
  /* one conference room per row, full office width (no 3-up squeeze) */
  .oc-cr { flex-basis:100%; max-width:100%; }
}

@keyframes oc-play { to { background-position-x: calc(-1 * var(--n) * var(--w)); } }
@keyframes oc-bob { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-3px)} }
@keyframes oc-breathe { 0%,100%{transform:translateY(0) scale(1)} 50%{transform:translateY(1px) scale(.994)} }
/* status badge: rise up and fade out on a loop (the sleep-"z" motion, generalized) */
@keyframes oc-float { 0%{opacity:0;transform:translateY(3px) scale(.8)}
  25%{opacity:1} 60%{opacity:.9;transform:translateY(-5px) scale(1)}
  100%{opacity:0;transform:translateY(-12px) scale(1)} }
@media (prefers-reduced-motion: reduce){ .oc-anim,.oc-static,.oc-badge,
  .oc-cr-standanim,.oc-cr-standimg { animation:none !important; } }
`;
