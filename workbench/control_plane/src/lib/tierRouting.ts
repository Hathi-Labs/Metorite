/**
 * WS-45 S3 (D90): the chat's model picker leaves, and each answer names its tier.
 *
 * Spec: `project-docs/specs/ai_tier_routing.md` §7, §8, §9 and ticket S3.
 *
 * The platform picks the tier of each step for an agent that the backend flag
 * `AI_TIER_ROUTING` covers (S2). So for that agent the composer offers no model
 * to pick, sends no `model` field, and reads no stored choice. Each answer then
 * names the tiers that served it, in its details menu, for every member.
 *
 * ## Two flags, and the client reads both
 *
 * - `NEXT_PUBLIC_AI_TIER_ROUTING` is the UI flag. It is build-time and ships
 *   OFF. With it off, nothing in this file changes what the chat draws or sends.
 * - The backend flag says WHICH agents are covered. The client never hard-codes
 *   that list. The gateway's `GET /agent` stamps `tier_routed` on each entry,
 *   from `acb_skills.tier_policy.tier_routing_on`, the one reader of the flag.
 *
 * A covered agent needs both. The UI flag on with no agent covered draws every
 * picker as today, so the wrong order of the two flips is safe (§9).
 *
 * Every rule here is pure, because vitest in this tree runs in node and cannot
 * mount the composer. `tierRouting.test.ts` holds each rule, and a source scan
 * there proves that `AgentChat.tsx` and `MessageBubble.tsx` call them.
 */

// ── The UI flag (§9) ────────────────────────────────────────────────────────

/**
 * True when the UI flag is on.
 *
 * ⚠️ Only the LITERAL `process.env.NEXT_PUBLIC_AI_TIER_ROUTING` is inlined into
 * the browser bundle. So the default reads the literal, and a test passes an
 * explicit `env`. `publicFlags.test.ts` holds the spelling.
 */
export function tierRoutingUiOn(env?: Record<string, string | undefined>): boolean {
  const raw = env ? env.NEXT_PUBLIC_AI_TIER_ROUTING : process.env.NEXT_PUBLIC_AI_TIER_ROUTING;
  return ["1", "true", "on", "yes"].includes(String(raw ?? "").trim().toLowerCase());
}

// ── Which agents are covered ───────────────────────────────────────────────

/** The part of an agent list entry (`GET /api/agent/list`) that this reads. */
export interface TierRoutedEntry {
  name: string;
  /** Stamped by the gateway. Absent (an older gateway, the static fallback)
   *  reads as not covered, which is today's behaviour. */
  tier_routed?: boolean;
}

/** True only for an entry that the gateway marked covered. Fails closed. */
export function agentTierRouted(entry: TierRoutedEntry | null | undefined): boolean {
  return entry?.tier_routed === true;
}

// ── What the composer does for one agent ────────────────────────────────────

export interface ComposerModelPlan {
  /** The platform picks the tier. The UI flag is on and the gateway covers
   *  the agent. */
  covered: boolean;
  /** Draw the model picker. The caller's `lockModel` can still hide it. */
  showPicker: boolean;
  /** Fetch `GET /api/models/all` for the picker. */
  fetchModels: boolean;
  /** Read and write the stored choice (`cc-model-<agent>`). */
  rememberModel: boolean;
  /** Send the `model` field on `POST /api/agent/chat`. */
  sendModel: boolean;
}

/**
 * The composer's model behaviour for the agent in view.
 *
 * - **UI flag off:** everything as today. This is the default of the product.
 * - **UI flag on, agent list not loaded yet:** no picker and no fetch, because
 *   the agent may be covered and the picker must not flash. The `model` field
 *   still goes, but its value is the caller's forced model or "auto", NOT the
 *   stored choice. The composer reads no stored choice before it knows the
 *   agent keeps its picker, so a covered agent's key is never read. A turn
 *   sent in that window runs on the agent's default, as "auto" does today.
 *   The persist effect restores the choice once the list lands.
 * - **UI flag on, agent covered:** no picker, no fetch, no stored choice and no
 *   `model` field. The effort selector stays (§5, Q3).
 * - **UI flag on, agent not covered:** everything as today.
 */
export function composerModelPlan(input: {
  uiOn: boolean;
  agentsKnown: boolean;
  entry: TierRoutedEntry | null | undefined;
}): ComposerModelPlan {
  if (!input.uiOn) {
    return { covered: false, showPicker: true, fetchModels: true, rememberModel: true, sendModel: true };
  }
  const covered = input.agentsKnown && agentTierRouted(input.entry);
  const live = input.agentsKnown && !covered;
  return { covered, showPicker: live, fetchModels: live, rememberModel: live, sendModel: !covered };
}

// ── No effort and no agent for the member (owner, 2026-10-07) ───────────────

/** The three effort states. The server keeps all three (§5). */
export type ThinkMode = "auto" | "thinking" | "max";

/** The other controls of the composer's bar, for the agent in view. */
export interface ComposerControls {
  /** Draw the effort selector (Auto, Thinking, Max). */
  showEffort: boolean;
  /** Draw the composer's agent selector. Only the orchestrator has one. */
  showAgentSwitch: boolean;
}

/**
 * The composer's effort selector and agent selector (amendment of D90 §5 and
 * §7, owner 2026-10-07). The chat is a multi-agent, multi-model system, so the
 * member chooses neither the effort nor the agent.
 *
 * - **UI flag off:** both as today. The agent selector is the orchestrator's.
 * - **UI flag on, agent covered:** neither. The run sends `think_mode` "auto"
 *   (`sentThinkMode`), and the executor reads the effort from the member's
 *   words. The orchestrator routes to the right agent (`call_agent`).
 * - **UI flag on, agent list not loaded yet:** neither, so a control does not
 *   draw and then leave. The same rule as the model picker.
 * - **UI flag on, agent not covered:** both as today.
 */
export function composerControls(input: {
  uiOn: boolean;
  agentsKnown: boolean;
  entry: TierRoutedEntry | null | undefined;
  isOrchestrator: boolean;
}): ComposerControls {
  const live = !input.uiOn || (input.agentsKnown && !agentTierRouted(input.entry));
  return { showEffort: live, showAgentSwitch: input.isOrchestrator && live };
}

/**
 * The `think_mode` the run sends. A covered agent always sends "auto", also
 * when the member chose Thinking or Max on an agent that was not covered and
 * then switched (owner, 2026-10-07).
 */
export function sentThinkMode(covered: boolean, mode: ThinkMode): ThinkMode {
  return covered ? "auto" : mode;
}

// ── Which agent a `/chat` conversation talks to (owner, 2026-10-07) ─────────

/** The agent of every new `/chat` conversation when the member does not choose. */
export const CHAT_AGENT = "orchestrator";

/**
 * Who picks the agent of a NEW `/chat` conversation.
 *
 * - `member`: the "New session" picker, as today. UI flag off, or the
 *   orchestrator is not covered.
 * - `orchestrator`: no picker. A new conversation talks to the orchestrator,
 *   which routes to the right agent.
 * - `pending`: UI flag on, and the agent list has not answered. The picker
 *   does not draw yet, so it cannot flash.
 *
 * It decides NEW conversations only. An existing conversation always opens
 * with its own agent (`initialChatOpen`), so no history breaks.
 */
export type ChatAgentChoice = "member" | "orchestrator" | "pending";

export function chatAgentChoice(input: {
  uiOn: boolean;
  agentsKnown: boolean;
  entries: readonly TierRoutedEntry[];
}): ChatAgentChoice {
  if (!input.uiOn) return "member";
  if (!input.agentsKnown) return "pending";
  const orchestrator = input.entries.find((a) => a.name === CHAT_AGENT);
  return agentTierRouted(orchestrator) ? "orchestrator" : "member";
}

/**
 * What "+ New conversation" does. `create` starts an orchestrator chat with no
 * picker. `picker` opens the picker, as today. `wait` remembers the press until
 * the agent list answers.
 */
export function newChatAction(choice: ChatAgentChoice): "create" | "picker" | "wait" {
  if (choice === "orchestrator") return "create";
  return choice === "pending" ? "wait" : "picker";
}

/**
 * True when the "New session" picker draws. A conversation whose agent is not
 * known (`isUnresolvedAgent`) still draws it, with any choice. The picker then
 * names the agent of a conversation that exists, so its history keeps the
 * right agent.
 */
export function agentPickerShows(input: {
  requested: boolean;
  choice: ChatAgentChoice;
  repairing: boolean;
}): boolean {
  if (!input.requested) return false;
  return input.repairing || input.choice === "member";
}

/** The part of a stored `/chat` session that `initialChatOpen` reads. */
export interface ChatSessionRef {
  id: string;
  agentName: string;
}

/**
 * Which conversation `/chat` opens when it loads, and with which agent.
 *
 * - `?agent=<name>` opens that agent's latest conversation, or creates one.
 *   The `/agents` page links to `/chat` this way, so it stays with either
 *   choice.
 * - Else the latest conversation opens, with ITS agent. A conversation on
 *   another agent continues with that agent, also when the orchestrator is
 *   covered. Its history never moves to a second agent.
 * - No conversation: none opens. `/chat` then asks for a new one.
 */
export type InitialChatOpen =
  | { kind: "open"; id: string }
  | { kind: "create"; agent: string }
  | { kind: "none" };

export function initialChatOpen(input: {
  sessions: readonly ChatSessionRef[];
  agentParam: string | null | undefined;
}): InitialChatOpen {
  if (input.agentParam) {
    const match = input.sessions.find((s) => s.agentName === input.agentParam);
    return match ? { kind: "open", id: match.id } : { kind: "create", agent: input.agentParam };
  }
  return input.sessions.length > 0 ? { kind: "open", id: input.sessions[0].id } : { kind: "none" };
}

/**
 * The `model` part of the chat request body. `null` sends NO field.
 *
 * Spread in the place of the old `model:` key, so the key order of the body,
 * and so its bytes, stay as today for every call that sends a model.
 */
export function chatModelField(model: string | null | undefined): { model?: string } {
  return model === null ? {} : { model: model ?? "auto" };
}

// ── A model that a surface governs from a setting (S4, §7.1) ────────────────

/**
 * Whether the platform picks the tier of *agent*, for a surface outside the
 * composer: the email chat, the Tasks and Projects rails, and the two
 * `chat_model` settings controls (WS-45 S4).
 *
 * The same answer as `composerModelPlan(...).covered`. UI flag off: never.
 * Agent list not loaded yet: not yet, so a surface keeps today's behaviour
 * until it knows.
 */
export function tierRoutedFor(input: {
  uiOn: boolean;
  agentsKnown: boolean;
  entry: TierRoutedEntry | null | undefined;
}): boolean {
  return composerModelPlan(input).covered;
}

/** What `useTierRouted` gives a surface: does it know yet, and is it covered. */
export interface TierCoverage {
  /** True with the UI flag off, and once the agent list has answered. */
  known: boolean;
  /** The platform picks the tier (`tierRoutedFor`). */
  covered: boolean;
}

/**
 * True when a surface may read the `chat_model` setting (§11 S4 done-when 2
 * and 3). Not until it knows that the agent is not covered, so a covered
 * agent's setting is never read, not even once before the list lands.
 * UI flag off: always, as today.
 */
export function readsChatModel(c: TierCoverage): boolean {
  return c.known && !c.covered;
}

/**
 * The `model` and `lockModel` props that a surface hands `AgentChat`, when it
 * forces the model from a `chat_model` setting (S4, §7.1).
 *
 * - Not covered (and every case with the UI flag off): the setting's model,
 *   locked, as today.
 * - Covered: neither prop. The platform picks the tier, so the setting is not
 *   read, and the composer draws no picker anyway (S3).
 *
 * Spread into the props, so an uncovered surface renders the same props.
 */
export function governedModelProps(
  covered: boolean,
  model: string | undefined,
  lock = true,
): { model?: string; lockModel?: boolean } {
  if (covered) return {};
  return lock ? { model, lockModel: true } : { model };
}

/**
 * The model-setting rows a settings panel draws. A covered agent's chat row
 * leaves, because no chat reads it (§7.1). Every other row stays: the
 * background features keep their literal tiers (§1.2, H-44).
 *
 * The column stays in the database. A drop is a later decision (§8, R6).
 */
export function visibleModelRows<T extends { key: string }>(
  rows: readonly T[],
  chatKey: string,
  covered: boolean,
): T[] {
  return covered ? rows.filter((r) => r.key !== chatKey) : [...rows];
}

// ── The stored choice (§8, the `localStorage` row) ──────────────────────────

/** The per-agent key of the picker's choice. */
export function modelPrefKey(agent: string): string {
  return `cc-model-${agent}`;
}

/** The key of the picker's "Frequently Used" counts. */
export const MODEL_USAGE_KEY = "cc-model-usage";

/** The part of `Storage` the model memory uses. A test passes a fake. */
export type ModelStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function store(s?: ModelStore): ModelStore {
  return s ?? globalThis.localStorage;
}

/**
 * What the composer's persist effect does on one run (WS-45 S3).
 *
 * - `skip`: write nothing. A forced model is governed elsewhere, and a covered
 *   agent keeps no choice.
 * - `restore`: UI flag on, and this is the first live run for *agent*. Load
 *   its stored choice into the composer, and write nothing over it.
 * - `persist`: store the model in view, as before.
 *
 * `restoredFor` is the agent whose stored choice this mount has read. ⚠️ A
 * covered agent CLEARS it. Without that, a switch from agent A to a covered
 * agent and back skipped A's restore and wrote "auto" over A's choice
 * (review P2 of PR #678).
 */
export type ModelMemoryStep =
  | { kind: "skip"; restoredFor: string | null }
  | { kind: "restore"; model: string; restoredFor: string }
  | { kind: "persist"; restoredFor: string | null };

export function modelMemoryStep(input: {
  forced: boolean;
  remember: boolean;
  tierUi: boolean;
  agent: string;
  model: string;
  restoredFor: string | null;
  readStored: () => string | null;
}): ModelMemoryStep {
  if (input.forced) return { kind: "skip", restoredFor: input.restoredFor };
  if (!input.remember) return { kind: "skip", restoredFor: null };
  if (input.tierUi && input.restoredFor !== input.agent) {
    const stored = input.readStored();
    if (stored && stored !== input.model) {
      return { kind: "restore", model: stored, restoredFor: input.agent };
    }
    return { kind: "persist", restoredFor: input.agent };
  }
  return { kind: "persist", restoredFor: input.restoredFor };
}

/** The stored choice of *agent*, or null. Never throws (SSR, private mode). */
export function getLastModel(agent: string, s?: ModelStore): string | null {
  try {
    return store(s).getItem(modelPrefKey(agent));
  } catch {
    return null;
  }
}

export function setLastModel(agent: string, modelId: string, s?: ModelStore): void {
  try {
    store(s).setItem(modelPrefKey(agent), modelId);
  } catch {
    /* noop */
  }
}

export function getModelUsage(s?: ModelStore): Record<string, number> {
  try {
    const raw = store(s).getItem(MODEL_USAGE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, number>) : {};
  } catch {
    return {};
  }
}

export function incrementModelUsage(modelId: string, s?: ModelStore): void {
  if (modelId === "auto") return; // don't track auto
  try {
    const usage = getModelUsage(s);
    usage[modelId] = (usage[modelId] ?? 0) + 1;
    store(s).setItem(MODEL_USAGE_KEY, JSON.stringify(usage));
  } catch {
    /* noop */
  }
}

/**
 * The stored keys that a covered agent leaves behind (§8).
 *
 * The agent's own choice always goes. The usage counts go only when every
 * agent in the list is covered, because they still sort the picker of an
 * agent that is not.
 */
export function staleModelKeys(agent: string, agents: readonly TierRoutedEntry[]): string[] {
  const keys = [modelPrefKey(agent)];
  if (agents.length > 0 && agents.every(agentTierRouted)) keys.push(MODEL_USAGE_KEY);
  return keys;
}

/** Delete the stale keys of a covered agent. Never throws. */
export function forgetModelChoice(
  agent: string,
  agents: readonly TierRoutedEntry[],
  s?: ModelStore,
): void {
  for (const key of staleModelKeys(agent, agents)) {
    try {
      store(s).removeItem(key);
    } catch {
      /* noop */
    }
  }
}

// ── The tier label of an answer (§7.2, Q4) ──────────────────────────────────

/** The AG-UI custom event of one model request (`acb_skills.tier_policy.ROUTE_EVENT`). */
export const ROUTE_EVENT = "ai.route";

/**
 * The words a member reads for each chat tier (D-AI-1).
 *
 * ⚠️ **A mirror, and fenced as one.** `tier_catalog` holds the words, and the
 * operator owns them. The Control Plane has no read of `GET /my/tiers` yet
 * (`ai_metering_and_analytics.md` §8.4 clause 6). So these are the seed rows of
 * `infra/customer_console/015_tier_pricing.sql`, and `tierRouting.test.ts`
 * reads that file and fails if the two disagree. When the read ships, the
 * label reads it and this map goes.
 *
 * A slug that is not here draws nothing. The policy picks only these three,
 * and an off-ladder default (`tier-code`, or a `provider/model` id) must
 * never show, because a member never sees a model (D32.7).
 */
export const TIER_WORDS: Readonly<Record<string, string>> = {
  "tier-fast": "Fast",
  "tier-balanced": "Balanced",
  "tier-powerful": "Powerful",
};

/** The words of each tier that served the answer, each once, in first-use order. */
export function routeTiers(
  events: ReadonlyArray<{ name: string; value: unknown }> | null | undefined,
): string[] {
  const words: string[] = [];
  for (const ev of events ?? []) {
    if (ev.name !== ROUTE_EVENT || !ev.value || typeof ev.value !== "object") continue;
    const word = TIER_WORDS[String((ev.value as Record<string, unknown>).tier ?? "")];
    if (word && !words.includes(word)) words.push(word);
  }
  return words;
}

/** One quiet label, for example "Balanced, then Powerful". Null when no tier is known. */
export function tierRouteLabel(
  events: ReadonlyArray<{ name: string; value: unknown }> | null | undefined,
): string | null {
  const words = routeTiers(events);
  return words.length > 0 ? words.join(", then ") : null;
}
