// AI clarify proposal — the structured recommendation the assistant makes for an
// inbox item during the Process/Clarify stage. Today this is a local heuristic
// (proposeClarification); when the gateway lands, `POST /tasks/items/{id}/clarify`
// returns the same shape from the `task-manager` agent. The human always
// reviews/edits before it's applied (GTD: AI proposes, you decide).

import { Energy, GtdItem, GtdProject, Person, Target } from "./types";

/** The disposition the assistant recommends (superset of the GTD outcomes). */
export type ClarifyDisposition =
  | "NEXT" // defer → next action (by context)
  | "PROJECT" // outcome needs >1 action
  | "WAITING" // delegate → waiting for
  | "CALENDAR" // day/time-specific action
  | "DO_NOW" // 2-minute rule
  | "SOMEDAY" // incubate
  | "REFERENCE" // file, non-actionable
  | "TRASH"; // no longer needed

/** How sure the assistant is — drives how loudly the UI nudges a one-tap accept. */
export type Confidence = "high" | "medium" | "low";

export interface ClarifyProposal {
  actionable: boolean;
  disposition: ClarifyDisposition;
  /** the very next physical, visible action (GTD's pivotal question) */
  nextAction: string;
  /** the successful outcome — most important for projects (GTD's other key question) */
  outcome?: string;
  context?: string;
  energy?: Energy;
  timeEstimateMins?: number;
  isTwoMinute?: boolean;
  /** who to hand off to, for a delegation */
  suggestedAssignee?: Person;
  /** the proposed owner's live workload, when the server annotated it (§5,
   *  Phase 2) — powers the "already at capacity" warning at assign time. */
  assigneeLoad?: { overloaded: boolean; openTaskCount: number; note?: string };
  /** where it should be stored — Local vs a connected PM tool (§5.1) */
  target?: Target;
  /** an existing project to file it under, if any (auto-matched) */
  projectId?: string;
  /** true when the project was inferred by the assistant (vs already set on the item) */
  projectInferred?: boolean;
  /** the space (and optional folder) a NEW project/list should be created
   *  under — set when the user's guidance named a destination without an
   *  existing project match; pre-selects the Where target (server only). */
  targetSpaceId?: string;
  targetFolderId?: string;
  /** how sure the assistant is about this disposition */
  confidence: Confidence;
  /** short why, shown under the proposal */
  rationale: string;
  /** provider stage default for the destination (server proposal only) */
  status?: string;
  /** the assistant's read of how big this is: one action, a task with
   *  subtasks, or a full project (server proposal only). */
  complexity?: "single" | "subtasks" | "project";
  /** concrete child steps the assistant suggests, when complexity="subtasks". */
  suggestedSubtasks?: string[];
  /** true when re-clarifying a SYNCED task: its ClickUp destination (account +
   *  project) is locked and the "Where it goes" picker should be read-only. */
  lockedDestination?: boolean;
  /** true when the assistant reads the TITLE itself as too vague to clarify
   *  well (server proposal only) — drives the vague-title gate. */
  isVague?: boolean;
  /** a clearer rewrite of the title, offered whether or not it's vague
   *  (server proposal only). Undefined when no improvement is offered. */
  suggestedTitle?: string;
  /** a model-inferred hard deadline (ISO date), when the item implies one
   *  (server proposal only). Feeds the "When" axis. */
  dueDate?: string;
  /** Prioritization matrix reads (AI proposes, user confirms). `urgent` is NOT
   *  proposed — it derives from the due date. `important` = something stalls if
   *  skipped (downside); `leveraged` = asymmetric 100x upside. `weightReason`
   *  is a short why shown next to the toggles. */
  important?: boolean;
  leveraged?: boolean;
  /** needs an unbroken flow state — creative/build/design/write/strategy */
  deepWork?: boolean;
  weightReason?: string;
  /** a likely-existing PM-tool (ClickUp) task this inbox capture may duplicate
   *  — set only on a fresh inbox clarify (server proposal, token-free lexical
   *  match). Lets the card offer "already on ClickUp: merge into it, or drop
   *  this capture". */
  duplicate?: DuplicateMatch;
  /** an existing task this capture looks like a concrete SUB-STEP of (server
   *  proposal, scoped to the matched project). Lets the card file it as a
   *  subtask under that task instead of as a standalone. */
  parentSuggestion?: ParentMatch;
}

/** A likely-existing PM-tool task the inbox capture may duplicate (dedup). */
export interface DuplicateMatch {
  /** the existing SYNCED item's local id — the merge target. */
  itemId: string;
  title: string;
  /** deep link to the task in the tool (ClickUp), when known. */
  providerUrl?: string;
  /** the tool's current stage, when known. */
  providerStatus?: string;
  /** the list/project it lives in, when known. */
  projectName?: string;
  /** "duplicate" (confident same) | "similar" (maybe the same). */
  verdict: "duplicate" | "similar";
  /** lexical similarity score (transparency). */
  score: number;
}

/** An existing task the inbox capture looks like a concrete sub-step of. */
export interface ParentMatch {
  /** the existing parent task's local id — the file-under target. */
  itemId: string;
  title: string;
}

/** Map a GTD disposition to a sensible provider stage/status.
 *  e.g. a project item that's really "someday" → Backlog; an actioned/delegated
 *  item → To-do. Falls back gracefully to whatever statuses the tool has. */
export function defaultStatus(
  disposition: ClarifyDisposition,
  statuses: string[],
): string | undefined {
  if (!statuses.length) return undefined;
  const find = (re: RegExp) => statuses.find((s) => re.test(s));
  if (disposition === "SOMEDAY") return find(/backlog|someday|icebox/i) ?? statuses[0];
  if (disposition === "PROJECT") return find(/backlog|to.?do|selected/i) ?? statuses[0];
  // actionable (next / delegate / calendar) → the "to-do" column
  return find(/to.?do|selected|to do/i) ?? statuses[1] ?? statuses[0];
}

/** The Sort→Shape verdict, condensed for the inbox list's "AI suggests"
 *  column. SORT = is it actionable, and how urgent (do-now); SIZE = one
 *  action, break into steps, or a project; DELEGATE = a hand-off to someone. */
export type SortBucket = "do-now" | "actionable" | "reference" | "someday" | "trash";
export type SizeBucket = "single" | "steps" | "project";

export interface SortShapeSummary {
  sort: SortBucket;
  sortLabel: string;
  /** SIZE only applies to plain actionable items (not a 2-minute do-now). */
  size?: SizeBucket;
  sizeLabel?: string;
  /** true when the assistant reads this as a hand-off (WAITING / has assignee). */
  delegate: boolean;
  /** the suggested owner's first name, when delegating. */
  delegateTo?: string;
}

const SORT_LABEL: Record<SortBucket, string> = {
  "do-now": "Do now",
  actionable: "Actionable",
  reference: "Reference",
  someday: "Someday",
  trash: "Trash",
};
const SIZE_LABEL: Record<SizeBucket, string> = {
  single: "Single action",
  steps: "Break into steps",
  project: "Project",
};

/** Condense a proposal into its Sort→Shape read for at-a-glance display. */
export function sortShapeSummary(p: ClarifyProposal): SortShapeSummary {
  const sort: SortBucket =
    p.disposition === "DO_NOW" ? "do-now"
      : p.disposition === "REFERENCE" ? "reference"
        : p.disposition === "SOMEDAY" ? "someday"
          : p.disposition === "TRASH" ? "trash"
            : "actionable"; // NEXT / PROJECT / WAITING / CALENDAR

  // SIZE is only meaningful for plain actionable work — a do-now is by
  // definition a single quick action, and reference/someday/trash aren't shaped.
  const size: SizeBucket | undefined =
    sort !== "actionable"
      ? undefined
      : p.disposition === "PROJECT" || p.complexity === "project" ? "project"
        : p.complexity === "subtasks" ? "steps"
          : "single";

  const delegate = p.disposition === "WAITING" || !!p.suggestedAssignee;
  return {
    sort,
    sortLabel: SORT_LABEL[sort],
    size,
    sizeLabel: size ? SIZE_LABEL[size] : undefined,
    delegate,
    delegateTo: p.suggestedAssignee?.name.split(/\s+/)[0],
  };
}

// Word-boundary hint match (mirror of gateway ai.py `_has`): bare substring
// matching misfiled captures - "profile..." tripped the "file" reference hint.
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const has = (t: string, ...words: string[]) =>
  words.some((w) => {
    const hint = w.trim();
    return !!hint && new RegExp(`(?<![a-z0-9])${esc(hint)}(?![a-z0-9])`).test(t);
  });
const CAP = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// Words too common to carry a project signal.
const STOP = new Set([
  "the", "and", "for", "with", "our", "out", "get", "set", "new", "you", "your",
  "this", "that", "from", "into", "about", "need", "want", "make", "have", "has",
  "ask", "put", "add", "let", "off", "day", "week", "next", "soon", "some", "any",
]);

function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOP.has(w));
}

/** Match an inbox item to the best-fit existing project by keyword overlap.
 *  Lets the assistant file captures under the right project automatically —
 *  even across many projects — instead of forcing you to hunt a long list.
 *  Only suggests when at least two meaningful words overlap. */
export function suggestProject(
  item: GtdItem,
  projects: GtdProject[],
): { projectId?: string; score: number } {
  const words = new Set([...tokenize(item.title), ...tokenize(item.notes ?? "")]);
  if (!words.size) return { score: 0 };
  let best: { projectId?: string; score: number } = { score: 0 };
  for (const p of projects) {
    if (p.status !== "ACTIVE") continue;
    const pt = new Set([...tokenize(p.outcome), ...tokenize(p.purpose ?? "")]);
    let score = 0;
    for (const w of words) if (pt.has(w)) score++;
    if (score > best.score) best = { projectId: p.id, score };
  }
  return best.score >= 2 ? best : { score: best.score };
}

/** Infer the GTD context from the wording. */
function inferContext(t: string): string {
  if (has(t, "call", "phone", "ring", "dial", "ring up")) return "@calls";
  if (has(t, "buy", "pick up", "pickup", "store", "errand", "drop off", "collect", "bank", "post office"))
    return "@errands";
  if (has(t, "ask ", "discuss", "1:1", "agenda", "raise with", "bring up", "talk to"))
    return "@agenda";
  return "@computer";
}

/** Turn a raw capture into a specific, verb-first next action. */
function draftNextAction(title: string, ctx: string, assignee?: Person): string {
  const t = title.trim();
  const lower = t.toLowerCase();
  if (assignee) return `Ask ${assignee.name} to ${lower.replace(/^(ask|get|have)\s+\w+\s+(to\s+)?/, "")}`.trim();
  // already imperative? keep it.
  if (/^(call|email|reply|draft|send|buy|pick|book|write|review|check|pay|sign|schedule|confirm|follow up|order|renew|research|plan|prepare)/.test(lower))
    return CAP(t);
  if (ctx === "@calls") return `Call about ${t}`;
  if (ctx === "@errands") return `Pick up / handle: ${t}`;
  if (ctx === "@agenda") return `Raise with the team: ${t}`;
  if (has(lower, "email", "reply", "message", "slack", "respond")) return `Reply re: ${t}`;
  return `Action: ${t}`;
}

const PROJECT_HINTS = [
  "plan", "organize", "organise", "launch", "set up", "setup", "build", "design",
  "research", "prepare", "roll out", "rollout", "migrate", "hire", "onboard",
  "campaign", "event", "trip", "strategy", "handbook", "write up", "fit-out",
  "process", "framework", "overhaul", "redesign",
];
const TWO_MIN_HINTS = ["reply", "confirm", "rsvp", "sign", "pay", "forward", "text", "send the", "quick", "approve", "reschedule"];
const REFERENCE_HINTS = ["receipt", "invoice", "statement", "fyi", "file", "for the record", "article", "read:", "link", "doc:", "reference"];
const SOMEDAY_HINTS = ["idea:", "someday", "maybe", "one day", "learn ", "explore", "evaluate", "wish", "consider "];
const CALENDAR_HINTS = ["today", "tomorrow", "tonight", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "deadline", "due ", " at ", "o'clock", "appointment", "meeting on", "next week"];

// Prioritization signals (heuristic; the LLM path reads these more richly).
// IMPORTANT = downside — something stalls/breaks or a real obligation. Money,
// contracts, blockers, compliance, the team/company being held up.
const IMPORTANT_HINTS = [
  "blocker", "blocking", "blocked", "urgent", "critical", "deadline", "overdue",
  "payroll", "invoice", "payment", "pay ", "contract", "legal", "compliance",
  "tax", "gst", "renew", "expire", "expiry", "lapse", "outage", "down", "bug",
  "production", "customer", "client", "launch", "ship", "release", "hire",
  "offer letter", "board", "audit", "penalty", "fine",
];
// LEVERAGED = asymmetric 100x upside — the rare, trajectory-changing tasks.
const LEVERAGED_HINTS = [
  "investor", "vc ", "raise", "fundraise", "fundraising", "term sheet",
  "grant", "pitch", "partnership", "partner with", "acquisition", "acquire",
  "key hire", "cofounder", "co-founder", "intro to", "introduction to",
  "keynote", "press", "pr ", "media", "podcast", "conference", "demo day",
  "strategic", "distribution deal", "enterprise deal", "big customer",
];

/** Heuristic read of the two manual matrix flags from the wording. Conservative
 *  — leveraged especially stays rare (it's the scarce flag). The LLM clarify
 *  path overrides this with a richer read; this keeps the offline heuristic and
 *  the "AI proposes" contract coherent. */
function readWeight(item: GtdItem): {
  important: boolean;
  leveraged: boolean;
  weightReason: string;
} {
  const t = `${item.title} ${item.notes ?? ""}`.toLowerCase();
  const leveraged = has(t, ...LEVERAGED_HINTS);
  const important = leveraged || has(t, ...IMPORTANT_HINTS);
  const reason = leveraged
    ? "Looks high-leverage — a potential 100x outcome."
    : important
      ? "Reads as important — something stalls if it slips."
      : "No strong importance/leverage signal — left unflagged.";
  return { important, leveraged, weightReason: reason };
}

// A capture that packs several actions into one line ("book flights and hotel,
// then email the team") reads as needing a break-down into steps, not a single
// action. Conservative: needs a clear separator AND at least two parts that
// each start with an action verb, so "salt and pepper" or "black & white" don't
// trip it.
const MULTISTEP_SPLIT = /\s+(?:and|then|&|\+)\s+|\s*[,;/]\s*|\s*(?:→|->)\s*/i;
const STEP_VERB = /^(call|email|reply|draft|send|buy|pick|book|write|review|check|pay|sign|schedule|confirm|follow|order|renew|research|plan|prepare|update|fix|create|add|remove|set|get|ask|make|finish|finalize|finalise|design|build|test|deploy|clean|organize|organise|arrange|contact|message|ping|share|upload|download|print|scan|file|collect|drop|book)\b/i;
function looksMultiStep(title: string): boolean {
  const t = title.trim();
  if (t.length < 18) return false; // too short to be several real steps
  const parts = t.split(MULTISTEP_SPLIT).map((s) => s.trim()).filter(Boolean);
  if (parts.length < 2) return false;
  return parts.filter((p) => STEP_VERB.test(p)).length >= 2;
}

function coreProposal(item: GtdItem, people: Person[]): ClarifyProposal {
  const t = item.title.toLowerCase();

  // Non-actionable first.
  if (has(t, ...SOMEDAY_HINTS)) {
    return {
      actionable: false,
      disposition: "SOMEDAY",
      nextAction: item.title,
      confidence: "high",
      rationale: "Reads like an idea to incubate, not a commitment yet.",
    };
  }
  if (has(t, ...REFERENCE_HINTS)) {
    return {
      actionable: false,
      disposition: "REFERENCE",
      nextAction: item.title,
      confidence: "high",
      rationale: "Looks like information to keep, not an action.",
    };
  }

  // Delegation — a teammate's name in the text.
  const assignee = people.find((p) => t.includes(p.name.split(/\s+/)[0].toLowerCase()));
  const ctx = inferContext(t);

  if (assignee && has(t, "ask", "get ", "have ", "follow up with", "chase", "remind")) {
    return {
      actionable: true,
      disposition: "WAITING",
      nextAction: draftNextAction(item.title, ctx, assignee),
      suggestedAssignee: assignee,
      timeEstimateMins: 5,
      energy: "low",
      confidence: "high",
      rationale: `Someone else's to do — delegate to ${assignee.name} and track it.`,
    };
  }

  // Project — multi-step outcome.
  if (has(t, ...PROJECT_HINTS)) {
    return {
      actionable: true,
      disposition: "PROJECT",
      outcome: `${CAP(item.title)} — done`,
      nextAction: `Outline the first step for: ${item.title}`,
      context: "@computer",
      energy: "medium",
      confidence: "medium",
      rationale: "Needs more than one action — track it as a project with a next action.",
    };
  }

  // Calendar — day/time-specific.
  if (has(t, ...CALENDAR_HINTS)) {
    return {
      actionable: true,
      disposition: "CALENDAR",
      nextAction: draftNextAction(item.title, ctx),
      context: ctx,
      energy: "low",
      confidence: "high",
      rationale: "Time-specific — put it on the calendar (hard landscape).",
    };
  }

  // Two-minute rule — small & quick.
  if (has(t, ...TWO_MIN_HINTS) && item.title.length < 60) {
    return {
      actionable: true,
      disposition: "DO_NOW",
      nextAction: draftNextAction(item.title, ctx),
      isTwoMinute: true,
      timeEstimateMins: 2,
      energy: "low",
      confidence: "high",
      rationale: "Quick — under two minutes, so just do it now.",
    };
  }

  // Default — a deferred next action, by context.
  return {
    actionable: true,
    disposition: "NEXT",
    nextAction: draftNextAction(item.title, ctx),
    context: ctx,
    energy: ctx === "@errands" ? "low" : "medium",
    timeEstimateMins: ctx === "@calls" ? 10 : ctx === "@errands" ? 20 : 25,
    confidence: "medium",
    // A capture that packs several actions into one line reads as needing a
    // break-down; leave it undefined (→ single action) otherwise.
    complexity: looksMultiStep(item.title) ? "subtasks" : undefined,
    rationale: `Actionable now — a next action for ${ctx}.`,
  };
}

/** Local heuristic proposal — a stand-in for the AI clarify call. Fills in as
 *  much as it can so the common case is a single tap: it auto-matches an
 *  existing **project** by keyword, then picks the storage **target** to follow
 *  that project (delegated/collaborative → the team tool; solo → Local, §5.1). */
export function proposeClarification(
  item: GtdItem,
  people: Person[] = [],
  projects: GtdProject[] = [],
): ClarifyProposal {
  const core = coreProposal(item, people);

  // Match an existing project (skip for PROJECT — that creates a *new* one).
  const existing = item.projectId
    ? projects.find((p) => p.id === item.projectId)
    : undefined;
  const match =
    !existing && core.disposition !== "PROJECT"
      ? suggestProject(item, projects)
      : { score: 0 };
  const matched = match.projectId
    ? projects.find((p) => p.id === match.projectId)
    : undefined;
  const project = existing ?? matched;

  // Where it goes: follow the matched project's home; delegation is always
  // collaborative so it lands on a synced tool regardless.
  const baseTarget: Target =
    item.source === "SYNCED"
      ? { source: "SYNCED", provider: item.provider ?? "clickup", accountId: item.accountId }
      : { source: "LOCAL", provider: "local" };
  let target: Target = project
    ? {
        source: project.source,
        provider: project.provider ?? (project.source === "LOCAL" ? "local" : "clickup"),
        accountId: project.accountId,
      }
    : baseTarget;
  if (core.disposition === "WAITING") {
    target = {
      source: "SYNCED",
      provider:
        project?.source === "SYNCED" && project.provider
          ? project.provider
          : item.provider && item.provider !== "local"
            ? item.provider
            : "clickup",
      accountId:
        project?.source === "SYNCED" ? project.accountId : item.accountId,
    };
  }

  const projectInferred = !!matched;
  const rationale = matched
    ? `${core.rationale} Looks like it belongs to “${matched.outcome}”.`
    : core.rationale;

  // AI-proposed matrix flags (user confirms). Skip for non-actionable outcomes
  // — reference/someday/trash don't get prioritized.
  const weight =
    core.actionable && core.disposition !== "SOMEDAY"
      ? readWeight(item)
      : { important: false, leveraged: false, weightReason: "" };

  return {
    ...core,
    target,
    projectId: project?.id,
    projectInferred,
    rationale,
    important: weight.important,
    leveraged: weight.leveraged,
    weightReason: weight.weightReason,
  };
}

// ── Delegating under the lens (WS-39 S6a) ───────────────────────────────────

/** Why a delegate decision cannot be applied yet, or `null` when it can. */
export type LensDelegateBlock = "needs-company-project" | "private-project";

/**
 * Under the one store the rule for delegating is the assign guard's, not a
 * connector's: a colleague cannot be put on a task in my PRIVATE tree, so a
 * delegate on a task that lives there (every inbox capture does) needs a
 * COMPANY project to move into, in the same request. And a delegated task
 * cannot ALSO become a private project — the outcome's child is mine, and
 * the guard refuses a colleague there.
 *
 * Pure, so `ClarifyPanel` cannot drift from the fence: `companyProjectIds`
 * is the list the lens serves (`GET /projects/nodes`, team projects only),
 * so "is a company project" is membership and nothing more.
 */
export function lensDelegateBlock(input: {
  lens: boolean;
  delegating: boolean;
  size: "single" | "subtasks" | "project" | string;
  /** the project picked in the panel, if any */
  projectId?: string;
  /** the project the item already lives in, if any */
  itemProjectId?: string;
  companyProjectIds: readonly string[];
}): LensDelegateBlock | null {
  if (!input.lens || !input.delegating) return null;
  if (input.size === "project") return "private-project";
  const dest = input.projectId ?? input.itemProjectId;
  if (dest && input.companyProjectIds.includes(dest)) return null;
  return "needs-company-project";
}

// ── The Where picker under the lens (WS-39 S6b) ─────────────────────────────

/** One row a member can file a task into. */
export interface WhereRow {
  id: string;
  name: string;
  /** Mine and private, or the company's board. The two never mix in a group. */
  kind: "area" | "project";
}

/** One heading and the rows under it, in the order the picker draws them. */
export interface WhereGroup {
  label: "My Areas" | "Company projects";
  kind: WhereRow["kind"];
  rows: WhereRow[];
}

/**
 * The two groups the Where picker shows under the lens: my Areas first, the
 * company's projects second. Pure, so the panel cannot drift from the fence
 * (`areas.test.ts`).
 *
 * Why two groups and not one list: an Area is private and a project is not,
 * and the delegate rule (`lensDelegateBlock`) turns on which was picked. A
 * flat list would let a member pick "Home" while delegating and learn from a
 * 422 that "Home" was the wrong kind of thing.
 *
 * An archived Area is left out. It is not a destination any more, and the
 * gateway's own list omits it unless asked.
 */
export function whereGroups(input: {
  areas: readonly { id: string; name: string; archived?: boolean }[];
  projects: readonly { id: string; outcome: string; status?: string }[];
  /**
   * Whether the Areas group is offered at all. `false` for a task that lives
   * on a company board: D62 refuses a move from a team node into anybody's
   * personal tree, so offering an Area there is offering a 422. Default true.
   */
  includeAreas?: boolean;
}): WhereGroup[] {
  const areas = input.areas
    .filter((a) => !a.archived)
    .map((a) => ({ id: a.id, name: a.name, kind: "area" as const }));
  const projects = input.projects
    .filter((p) => p.status === undefined || p.status === "ACTIVE")
    .map((p) => ({ id: p.id, name: p.outcome, kind: "project" as const }));
  const groups: WhereGroup[] = [];
  if (input.includeAreas !== false) {
    groups.push({ label: "My Areas", kind: "area", rows: areas });
  }
  groups.push({ label: "Company projects", kind: "project", rows: projects });
  return groups;
}

/**
 * Is this task in MY tree — the personal root, or one of my Areas?
 *
 * Under the lens the answer decides two things in Clarify (S6b repair):
 * whether the Where picker offers my Areas, and whether Size=project (which
 * mints an Area) is offered at all. Both are moves INTO the personal tree,
 * and D62 refuses them for a task that lives on a company board.
 *
 * A task with no project at all can only be mine — the lens creates every
 * capture in the root — so it reads as personal. `rootId` is null before a
 * member's first capture, and then nothing they see can be a team task
 * either, which is why null does not flip the answer.
 */
export function isPersonalTask(
  item: { projectId?: string },
  rootId: string | null,
  areaIds: readonly string[],
): boolean {
  if (!item.projectId) return true;
  if (rootId !== null && item.projectId === rootId) return true;
  return areaIds.includes(item.projectId);
}

// ── Where a Clarify starts, and what it may offer (audit 2026-09-24) ────────

/** The Where picker's starting pick, and the row it marks as suggested. */
export interface InitialWhere {
  /** The project the form starts on. `undefined` means "No project". */
  selected?: string;
  /** A row the picker marks "suggested" and the member must click. */
  suggested?: string;
}

/**
 * Where the Clarify form starts for one task.
 *
 * ⚠️ A capture is PRIVATE. Filing it into a company project publishes it to
 * everyone on that board. The assistant infers a project by keyword or on the
 * server, and the old form pre-selected that inference, so one "Organize it"
 * click published a private capture to a board the member never chose. The
 * audit of 2026-09-24 found this in production.
 *
 * The rule FAILS CLOSED. The proposal's project starts selected only when it
 * is the task's own project (nothing moves) or one of my Areas (it stays
 * private). Any other id starts NOT selected, and the picker marks it as a
 * suggestion the member must click.
 *
 * It does not ask `isPersonalTask`. That answer reads `personalRootId`, which
 * is null for a member whose first capture has only just created the root,
 * and after a failed `fetchMyRoot`. A null there made a capture read as a
 * board task, and a board task used to take the proposal as-is (review of
 * PR #440, P1).
 *
 * Fence: `clarifyWhere.test.ts`.
 */
export function initialWhere(input: {
  /** The project the proposal names, if any. */
  proposalProjectId?: string;
  /** The project the task lives in now, if any. */
  itemProjectId?: string;
  areaIds: readonly string[];
}): InitialWhere {
  const id = input.proposalProjectId;
  if (!id) return {};
  if (id === input.itemProjectId || input.areaIds.includes(id)) {
    return { selected: id };
  }
  return { suggested: id };
}

/**
 * Whether the Where picker offers "No project".
 *
 * A board task cannot leave its board for my personal tree: D62 refuses a move
 * from a team node into anybody's personal tree. "No project" IS that move, so
 * offering it is offering a 422. A personal task may always stay loose.
 */
export function whereOffersNoProject(personal: boolean): boolean {
  return personal;
}

/**
 * The line under the Where picker. It says who can see the task once it is
 * filed, so the member knows before the click, not after it.
 */
export function whereVisibilityHint(input: {
  /** The picked id, or undefined for "No project". */
  selected?: string;
  companyProjectIds: readonly string[];
}): string {
  if (input.selected && input.companyProjectIds.includes(input.selected)) {
    return "Visible to the team. A company project is shared with everyone on its board.";
  }
  return "Private to you until it joins a company project. File it in an Area, or leave it loose.";
}

/**
 * The items a Clarify session walks, oldest first (GTD processes FIFO).
 *
 * Two sets feed the walk. The first is my captures, which are INBOX. The
 * second is the "From Projects" group (S6e): a board task that a colleague
 * assigned to me and that I have not triaged. Its disposition is derived
 * (NEXT, SOMEDAY or WAITING), so a walk that reads INBOX alone skips it, and
 * the modal used to close on the first render for every such row.
 *
 * `done` is the ids decided in this session. They never come back into the
 * walk, whatever a stale re-read of `fromProjectIds` says. A re-read that
 * lands before the triage write puts the id straight back into that set, and
 * the walk used to open the same row a second time (review of PR #440, P1).
 */
export function clarifyQueue<
  T extends { id: string; disposition: string; createdAt: string; archivedAt?: string },
>(
  items: readonly T[],
  fromProjectIds: ReadonlySet<string>,
  done: ReadonlySet<string> = new Set(),
): T[] {
  return items
    .filter((i) => !done.has(i.id))
    .filter(
      (i) =>
        i.disposition === "INBOX" || (fromProjectIds.has(i.id) && !i.archivedAt),
    )
    .sort(
      (a, b) =>
        new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
    );
}

/** Whether Clarify may open on this item: a capture, or an untriaged board row. */
export function isClarifiable(
  item: { id: string; disposition: string },
  fromProjectIds: ReadonlySet<string>,
  done: ReadonlySet<string> = new Set(),
): boolean {
  if (done.has(item.id)) return false;
  return item.disposition === "INBOX" || fromProjectIds.has(item.id);
}

// ── Owner on a board task (review of PR #440, P1) ───────────────────────────

/**
 * Who the Clarify form starts on as the owner.
 *
 * ⚠️ On a board task a delegate is a REASSIGN. The organize request puts the
 * colleague on the shared task and takes me, and any co-assignee, off it.
 * The form used to start on "Delegate" whenever the assistant proposed a
 * person, so one Enter reassigned a colleague's board task. A board task now
 * starts on "Me", and only the member's own click picks Delegate. A personal
 * capture keeps the proposal, because a delegate there first needs a company
 * project the member picks (`lensDelegateBlock`).
 *
 * `personal` is `isPersonalTask`. When that answer is unsure (a null root),
 * it reads false, so this fails closed to "Me".
 */
export function initialOwner(input: {
  personal: boolean;
  hasSuggestedAssignee: boolean;
}): "me" | "delegate" {
  return input.personal && input.hasSuggestedAssignee ? "delegate" : "me";
}

/**
 * Whether a delegate decision may be applied. On a board task it may only
 * when the member picked Delegate in this session, by a click. Enter, a late
 * server proposal or a note re-run never picks it for them.
 */
export function delegateAllowed(input: {
  personal: boolean;
  delegating: boolean;
  pickedThisSession: boolean;
}): boolean {
  if (!input.delegating || input.personal) return true;
  return input.pickedThisSession;
}
