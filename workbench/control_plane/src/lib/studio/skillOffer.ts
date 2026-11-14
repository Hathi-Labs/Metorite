/**
 * The skill offer of a chat turn (STU-F6a, `project-docs/specs/ai_studio.md`
 * §14 F6a). After a finished turn, one card asks "Save this as a skill?".
 *
 * This file holds every rule of that card, so the node-env vitest holds them.
 * It makes no model call and no network call. `SkillOfferCard.tsx` draws it,
 * and `MessageBubble` mounts it once, after the action bar of the turn.
 *
 * **When the card shows.** All five must be true (`skillOffer`):
 *
 * 1. The turn is finished: it does not stream.
 * 2. The chat is solo on positive evidence (`provenSolo`): the room read
 *    answered with a room of one. The viewer ran the turn, and the viewer
 *    typed its prompt. The typed mark is the one of `lib/typedMark.ts`. The
 *    chat keeps it on the live message only, so a reloaded turn gets no
 *    offer. The dock reads no room, so the dock gets no offer.
 * 3. The turn has `MIN_CALLS` or more calls. Each call finished, and its
 *    result holds the success mark of its tool at its fixed place
 *    (`SUCCESS_MARKS`). That is
 *    positive evidence: a refusal that a tool answers as plain text ("The
 *    member declined. Nothing was filed.") holds no mark, so it gives no
 *    offer. A tool with no mark in the list gives no offer either.
 * 4. Each call maps to one row of the catalog through the `tool` of the row
 *    (`skill_packs.action_tool` on the gateway, the one map). A tool that no
 *    row names, or that two rows name, maps no call. A workflow row maps a
 *    call only through the workflow id in its arguments. One call that maps
 *    to no row stops the offer for the whole turn: a mail tool and a CRM tool
 *    have no catalog action, so their turn gets no offer (fail closed).
 * 5. The viewer did not answer an offer on this message in this page.
 *
 * ⚠️ **No tool argument and no tool result goes into the draft.** A model or
 * another person wrote that text. A stored skill goes into the prompt of each
 * later turn that loads it, so an argument in the draft would be a stored
 * prompt injection. The text comes from the viewer's typed words through
 * `skillStarter.starterFor`. The actions are catalog ids, in call order. A
 * workflow id of an argument only picks a row, and the draft holds the id of
 * that row. A result is only searched for the success mark of its tool.
 *
 * **The answer.** Save sends ONE `POST /api/studio/skills` through the
 * `create` that the card passes (`skillsApi.createSkill`), with the name in
 * the box of the card. That makes a private draft (D127.2), and Save never
 * publishes. A second press while a save is open sends nothing. Not now sends
 * nothing. A refusal shows in member words, through `skillErrors.ts`. A
 * refusal that the card cannot repair closes the offer, with a link to the
 * skill creator of Studio (`STUDIO_NEW_SKILL`).
 *
 * Fences: `skillOffer.test.ts` and `components/studio/SkillOfferCard.test.ts`.
 */

import { parseUploadNote } from "@/lib/chatUpload";
import { isRoomState, isShared } from "@/lib/rooms";
import { bareToolName, refusedText } from "@/lib/toolSteps";

import { refusedResult } from "./recordsCard";
import { WORDS, placeOfFailure } from "./skillErrors";
import { MAX_ACTIONS, pickableActions } from "./skillForm";
import { NAME_MAX, starterFor } from "./skillStarter";
import type { CatalogAction, Reply, Skill, SkillBody, SkillVersion } from "./skillsApi";

/** The fewest finished calls of a turn that earn an offer (rule 3). */
export const MIN_CALLS = 3;

/** The title of the card. A short question, and the text is in the body. */
export const OFFER_TITLE = "Save this as a skill?";
/** The line over the draft. */
export const OFFER_LINE =
  "Save the actions of this reply as a skill. Then name the skill in a chat, and the assistant does these steps again.";
/** The line beside the buttons. */
export const SAVE_HINT = "Save makes a private draft. It does not publish the skill.";
/** The line of a saved offer. */
export const SAVED_LINE = "Saved as a private draft. Open it in Studio to check it and publish it.";
/** The words of a failed save that gave no sentence of its own. */
export const NOT_SAVED = "The skill was not saved. Try again.";
/** The words of a save that names an action that the catalog no longer serves. */
export const STALE_CATALOG = "An action of this reply is not in the catalog now. Reload the actions, then try again.";

/**
 * Rule 3: the ONE list of success marks, by chat tool. A call counts only
 * when its answer holds the mark of its tool AT ITS FIXED PLACE: the whole
 * line at a fixed line of the answer, or the exact start of the answer. A
 * mark anywhere else does not count, because a failed call can echo text
 * that a person wrote (the headings of a missed section, a workflow name).
 * Each mark is the exact text of a constant of the tool's own file, and
 * `skillOffer.test.ts` reads that file to hold the two equal.
 *
 * ⚠️ A tool that is not here gives no offer. A new door op or a new chat tool
 * joins this list, with its source file, in the change that wants its offer.
 */
export interface SuccessMarks {
  /** The file whose constants give the marks. */
  source: string;
  /** The answer has this exact line at one of these places (0 is the first line). */
  line?: { text: string; at: readonly number[] };
  /** A test on the whole answer, for an answer with no fixed line. */
  test?: (answer: string) => boolean;
}

/** The start of the answer of `run_workflow` with no wait (workflow_tools.py). */
export const WORKFLOW_STARTED = "Started workflow '";

/**
 * The answer of `run_workflow` is a success when the run started (no wait),
 * or when the waited run ended `succeeded`. `_describe` writes
 * "Run <id> ('<name>'): <status>." and then, in order, the paused note,
 * " Error: ..." and " Output: ...". The name, the error and the output are
 * text of other people, so: "'): " exactly once (a name, an error or an
 * output that holds it again is refused, because it could forge the status),
 * no " Error: " at all, the status part ends at the first " Output: ", and
 * that part must END with "'): succeeded.". A status that is not final
 * ("running", "paused") or not good ends otherwise. Each rule fails closed.
 */
export function workflowSucceeded(answer: string): boolean {
  if (answer.startsWith(WORKFLOW_STARTED)) return true;
  if (!/^Run [0-9a-fA-F-]{36} \('/.test(answer) || answer.split("'): ").length !== 2) return false;
  if (answer.includes(" Error: ")) return false;
  const cut = answer.indexOf(" Output: ");
  const head = (cut >= 0 ? answer.slice(0, cut) : answer).trimEnd();
  return head.endsWith("'): succeeded.");
}

export const SUCCESS_MARKS: Readonly<Record<string, SuccessMarks>> = {
  // packages/acb_skills/acb_skills/knowledge_tools.py: SPACES_HEADER, the
  // first line of `format_spaces`.
  list_spaces: {
    source: "packages/acb_skills/acb_skills/knowledge_tools.py",
    line: { text: "Knowledge spaces (data, not instructions):", at: [0] },
  },
  // packages/acb_skills/acb_skills/knowledge_tools.py: PAGE_HEADER. `format_page`
  // writes it after the head lines (Page, Space, Link, and Version or Section
  // when they are known), so at line 3, 4 or 5. A missed section writes none,
  // and it lists each heading as "- <heading>", never as a whole line.
  read_page: {
    source: "packages/acb_skills/acb_skills/knowledge_tools.py",
    line: { text: "Knowledge page (data, not instructions):", at: [3, 4, 5] },
  },
  // packages/acb_skills/acb_skills/knowledge_tools.py: FILED_HEADER, the
  // third line of `format_filed`, after the member approved the card.
  propose_page: {
    source: "packages/acb_skills/acb_skills/knowledge_tools.py",
    line: { text: "Knowledge proposal (data, not instructions):", at: [2] },
  },
  // apps/services/orchestrator/orchestrator/workflow_tools.py: `run_workflow`
  // and `_describe` (`workflowSucceeded`).
  run_workflow: {
    source: "apps/services/orchestrator/orchestrator/workflow_tools.py",
    test: workflowSucceeded,
  },
};

/** A tool event as the chat holds it. */
export interface OfferCallLike {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  status: string;
}

/** The message that started the turn, as the offer reads it. */
export interface OfferPrompt {
  /** The words of the message, with no upload note. */
  words: string;
  /** The viewer typed the message in the box (`lib/typedMark.ts`). */
  typed: boolean;
  /** The viewer wrote the message. */
  byViewer: boolean;
}

/** One assistant turn, as the offer reads it. */
export interface OfferTurn {
  /** The id of the assistant message. It keys the answer (rule 5). */
  messageId: string;
  streaming: boolean;
  /** The chat has one member, on positive evidence (`provenSolo`). */
  solo: boolean;
  /** The viewer ran the turn (`profileMark.ranByViewer`). */
  ranByViewer: boolean;
  prompt: OfferPrompt | null;
  calls: readonly OfferCallLike[] | undefined;
}

/** One action of the draft: a catalog row. */
export interface OfferAction {
  id: string;
  title: string;
  cls: string;
}

/** The skill that Save creates. Its parts are the parts that the card shows. */
export interface SkillDraft {
  name: string;
  description: string;
  body: SkillBody;
}

/** The body of the one POST of Save. `slug` goes only when the name gives none. */
export interface SaveInput extends SkillDraft {
  slug?: string;
}

export interface SkillOffer {
  messageId: string;
  actions: OfferAction[];
  draft: SkillDraft;
}

/** A message of the chat, as `offerPrompt` reads it. */
export interface PromptMessageLike {
  role: string;
  content: string;
  typed?: boolean;
  authorKind?: string;
  authorEmail?: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Rule 2, the solo half, on positive evidence only: the chat read its room,
 * the read answered with no error, and the room has one member. A chat that
 * reads no room (the dock, `compact`), a read that failed (`useRoom` then
 * falls back to a room of one) and a read that has not answered all give
 * false, because the chat could be shared.
 */
export function provenSolo(read: { enabled: boolean; room: unknown; error: string | null }): boolean {
  return read.enabled && read.error === null && isRoomState(read.room) && !isShared(read.room);
}

/**
 * The solo state that `AgentChat` hands each bubble. With the flag off it is
 * false at every room read, so the memo of `MessageBubble` sees no change
 * when the room answers, and no bubble draws again (the state of main).
 */
export function offerSolo(flagOn: boolean, read: { enabled: boolean; room: unknown; error: string | null }): boolean {
  return flagOn && provenSolo(read);
}

/**
 * The message that started the assistant turn at *index*: the message just
 * before it, when that is a member's message. Else null, and the turn gets
 * no offer.
 */
export function promptBefore<M extends { role: string }>(messages: readonly M[], index: number): M | null {
  const before = index > 0 ? messages[index - 1] : undefined;
  return before && before.role === "user" ? before : null;
}

/**
 * The prompt of a turn, as rule 2 reads it. A human message with no author
 * is a solo row, and only the reader can have written it.
 */
export function offerPrompt(
  message: PromptMessageLike | null | undefined,
  viewerEmail: string | undefined,
): OfferPrompt | null {
  if (!message || message.role !== "user") return null;
  const human = message.authorKind === "human" || message.authorKind === undefined;
  const viewer = (viewerEmail ?? "").trim().toLowerCase();
  const author = (message.authorEmail ?? "").trim().toLowerCase();
  const note = parseUploadNote(message.content);
  return {
    words: (note ? note.words : message.content).trim(),
    typed: message.typed === true,
    byViewer: human && (!author || !viewer || author === viewer),
  };
}

/**
 * Rule 3 for one call: it finished, it is no error and no refusal, and its
 * result holds the success mark of its tool (`SUCCESS_MARKS`).
 */
export function succeededCall(call: OfferCallLike): boolean {
  if (call.status !== "done") return false;
  const result = typeof call.result === "string" ? call.result : "";
  if (refusedText(result) || refusedResult(result)) return false;
  const tool = bareToolName(call.name);
  const entry = Object.hasOwn(SUCCESS_MARKS, tool) ? SUCCESS_MARKS[tool] : undefined;
  if (!entry) return false;
  const text = result.trim();
  if (entry.test && entry.test(text)) return true;
  if (!entry.line) return false;
  const lines = text.split(/\r?\n/);
  const { text: mark, at } = entry.line;
  return at.some((n) => lines[n] === mark);
}

/**
 * Rules 1, 2, 3 and 5: the rules that need no catalog. The card reads the
 * catalog only when this is true.
 */
export function offerAsks(turn: OfferTurn, answered: boolean): boolean {
  if (answered || turn.streaming) return false;
  if (!turn.solo || !turn.ranByViewer) return false;
  const prompt = turn.prompt;
  if (!prompt || !prompt.typed || !prompt.byViewer || !prompt.words) return false;
  const calls = turn.calls ?? [];
  return calls.length >= MIN_CALLS && calls.every(succeededCall);
}

/** The workflow id that a call names in its arguments, or "". */
function workflowIdOf(args: Record<string, unknown> | undefined): string {
  const raw = args && typeof args === "object" ? args.workflow : undefined;
  const id = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  return UUID.test(id) ? id : "";
}

/**
 * Rule 4. The catalog rows of the calls, in call order and each once, or
 * null when one call maps to no row. Only a row that a skill may hold maps a
 * call (`pickableActions`: no package row, no class X).
 */
export function mapCalls(
  calls: readonly OfferCallLike[],
  catalog: readonly CatalogAction[],
): OfferAction[] | null {
  const byTool = new Map<string, CatalogAction[]>();
  for (const row of pickableActions(catalog)) {
    const tool = typeof row.tool === "string" ? row.tool.trim().toLowerCase() : "";
    if (!tool) continue;
    byTool.set(tool, [...(byTool.get(tool) ?? []), row]);
  }
  const out: OfferAction[] = [];
  const seen = new Set<string>();
  for (const call of calls) {
    const found = byTool.get(bareToolName(call.name)) ?? [];
    let row: CatalogAction | undefined;
    if (found.some((r) => r.source === "workflow")) {
      const id = workflowIdOf(call.args);
      row = id ? found.find((r) => r.source === "workflow" && r.id.toLowerCase() === `workflow:${id}`) : undefined;
    } else if (found.length === 1) {
      row = found[0];
    }
    if (!row) return null;
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    out.push({ id: row.id, title: String(row.title || row.id), cls: String(row.class) });
  }
  return out.length > 0 && out.length <= MAX_ACTIONS ? out : null;
}

/**
 * The draft of the skill. The text comes from the viewer's typed words only,
 * and the actions are catalog ids. No argument and no result reaches it.
 */
export function offerDraft(words: string, actions: readonly OfferAction[]): SkillDraft {
  const fields = starterFor(words);
  return {
    name: fields.name,
    description: fields.description,
    body: { description: fields.description, instructions: fields.instructions, actions: actions.map((a) => a.id) },
  };
}

/** The offer of a turn, or null when the card shows nothing (rules 1 to 5). */
export function skillOffer(
  turn: OfferTurn,
  catalog: readonly CatalogAction[] | null | undefined,
  answered: boolean,
): SkillOffer | null {
  if (!offerAsks(turn, answered) || !catalog || !turn.prompt) return null;
  const actions = mapCalls(turn.calls ?? [], catalog);
  if (!actions) return null;
  const draft = offerDraft(turn.prompt.words, actions);
  if (!draft.name || !draft.body.instructions) return null;
  return { messageId: turn.messageId, actions, draft };
}

// ── The name and the body of Save ───────────────────────────────────────────

/**
 * True when the gateway can make a slug from *name*: its letters, with each
 * accent dropped, hold an ASCII letter or digit (`skill_slug.slug_from_name`).
 */
export function nameGivesSlug(name: string): boolean {
  const folded = name.normalize("NFKD").replace(/[^\x20-\x7e]/g, "");
  return /[a-z0-9]/i.test(folded);
}

/** A slug for a name that gives none, such as a name in Hindi: `skill-` and a hash of the name. */
export function fallbackSlug(name: string): string {
  let hash = 0x811c9dc5;
  for (const ch of name) {
    hash ^= ch.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `skill-${hash.toString(16).padStart(8, "0")}`;
}

/**
 * The body of the one POST: the name of the box, and the draft that the card
 * shows. A name that gives no slug sends `fallbackSlug`, so the save works.
 * Null for an empty name, which sends nothing.
 */
export function saveInput(offer: SkillOffer, name: string): SaveInput | null {
  const shown = name.trim().slice(0, NAME_MAX).trim();
  if (!shown) return null;
  return {
    name: shown,
    description: offer.draft.description,
    body: { ...offer.draft.body, actions: [...(offer.draft.body.actions ?? [])] },
    ...(nameGivesSlug(shown) ? {} : { slug: fallbackSlug(shown) }),
  };
}

/** Where a refused save shows, in member words. */
export interface OfferError {
  /**
   * `name`: under the name box, and a new name can pass. `stale`: with a
   * Reload of the actions. `form`: under the buttons, and Save can try again
   * (no answer, or a fault of the server). `studio`: the chat cannot save
   * this text, so the offer closes and the card links to the skill creator.
   */
  place: "name" | "stale" | "form" | "studio";
  text: string;
}

/** The words of a 429: a retry can pass after a wait. */
export const SAVE_LATER = "Too many saves at once. Wait a moment, then press Save again.";
/** The words of a 401: the session ended. The words of the Studio door (`realDoor.SIGN_IN_SENTENCE`). */
export const SIGN_IN_AGAIN = "Your session ended. Sign in again.";

/** The skill creator of Studio (`app/studio/skills/new`). */
export const STUDIO_NEW_SKILL = "/studio/skills/new";
/** The words of a save that the chat cannot repair. */
export const STUDIO_HANDOFF =
  "The chat cannot save this skill as it is. Make it in Studio, where you can change each field.";

/**
 * A refused save in member words, through the one map of `skillErrors.ts`
 * (`placeOfFailure`). A 409 (the name is taken) and a name with no slug show
 * under the name box, so a new name can pass. A row of the catalog that is
 * gone says to reload the actions, because the card cannot remove an action.
 */
export function offerFailure(status: number, detail: string): OfferError {
  const placed = placeOfFailure("create", status, detail);
  if (placed.place === "name") return { place: "name", text: placed.text };
  if (placed.place === "action" || placed.place === "picker") return { place: "stale", text: STALE_CATALOG };
  // No answer, or a fault of the server: the same Save can pass later.
  if (status === 0 || status >= 500) return { place: "form", text: placed.text || NOT_SAVED };
  // Too many calls, or a session that ended: a retry can pass after a wait,
  // or after the member signs in again. The offer stays open.
  if (status === 429) return { place: "form", text: SAVE_LATER };
  if (status === 401) return { place: "form", text: SIGN_IN_AGAIN };
  // A refusal of the description or the instructions (a hidden character, a
  // character that Python refuses), or any other refusal. The card holds no
  // box for that text, so a repeat sends the same body: hand it to Studio.
  return { place: "studio", text: STUDIO_HANDOFF };
}

// ── The answer of the viewer, for this page ─────────────────────────────────

/** The state of the offer of one message. */
export type OfferLocal =
  | { kind: "open"; error: OfferError | null }
  | { kind: "saving" }
  | { kind: "saved"; skillId: string }
  /** The chat cannot save it (`offerFailure` place `studio`). Closed: Save sends nothing. */
  | { kind: "studio"; text: string }
  | { kind: "dismissed" };

const OPEN: OfferLocal = { kind: "open", error: null };
const MAX_REMEMBERED = 200;
const remembered = new Map<string, OfferLocal>();
const listeners = new Set<() => void>();

/**
 * The state of the offer of one message. A remount in this page (a switch of
 * chat and back) reads the same state. A reload starts empty, and a reloaded
 * turn has no typed mark, so it gets no offer.
 */
export function offerLocal(messageId: string): OfferLocal {
  return remembered.get(messageId) ?? OPEN;
}

/** True when the viewer answered the offer of the message (rule 5). */
export function answeredOffer(local: OfferLocal): boolean {
  return local.kind === "saved" || local.kind === "dismissed" || local.kind === "studio";
}

const typedNames = new Map<string, string>();

/** The name that the member typed in the box of this message, or null for none. */
export function typedName(messageId: string): string | null {
  return typedNames.get(messageId) ?? null;
}

/**
 * The member types a name. It lives here, beside the state, so Save reads the
 * name of the box itself (`pressSave`), and a remount keeps it.
 */
export function setTypedName(messageId: string, name: string): void {
  typedNames.delete(messageId);
  typedNames.set(messageId, name);
  while (typedNames.size > MAX_REMEMBERED) {
    const oldest = typedNames.keys().next().value;
    if (oldest === undefined) break;
    typedNames.delete(oldest);
  }
  for (const listener of [...listeners]) listener();
}

/** The name in the box: the typed one, else the name of the draft. */
export function boxName(offer: SkillOffer): string {
  return typedName(offer.messageId) ?? offer.draft.name;
}

/** Each card of a message reads one state. */
export function subscribeOffers(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function setLocal(messageId: string, local: OfferLocal): void {
  remembered.delete(messageId);
  remembered.set(messageId, local);
  while (remembered.size > MAX_REMEMBERED) {
    const oldest = remembered.keys().next().value;
    if (oldest === undefined) break;
    remembered.delete(oldest);
  }
  for (const listener of [...listeners]) listener();
}

/** The create of the skill routes (`skillsApi.createSkill`). */
export type CreateSkill = (input: SaveInput) => Promise<Reply<{ skill: Skill; version: SkillVersion | null }>>;

/**
 * Save: ONE create of a private draft, with the name of the box (`boxName`).
 * The state moves to `saving` before the call starts, so a second press,
 * here or in another card of the message, sends nothing. A refusal keeps the
 * card open with its member words (`offerFailure`), or closes the offer with
 * the link to Studio. An empty name sends nothing.
 */
export async function pressSave(offer: SkillOffer, create: CreateSkill): Promise<void> {
  if (offerLocal(offer.messageId).kind !== "open") return;
  const input = saveInput(offer, boxName(offer));
  if (!input) {
    setLocal(offer.messageId, { kind: "open", error: { place: "name", text: WORDS.nameEmpty } });
    return;
  }
  setLocal(offer.messageId, { kind: "saving" });
  let reply: Awaited<ReturnType<CreateSkill>>;
  try {
    reply = await create(input);
  } catch {
    reply = { ok: false, status: 0, detail: NOT_SAVED };
  }
  if (reply.ok && reply.data?.skill?.id) {
    setLocal(offer.messageId, { kind: "saved", skillId: reply.data.skill.id });
  } else {
    const detail = !reply.ok && reply.detail ? reply.detail : NOT_SAVED;
    const error = offerFailure(reply.status, detail);
    setLocal(offer.messageId, error.place === "studio" ? { kind: "studio", text: error.text } : { kind: "open", error });
  }
}

/** Reload the actions: the error goes, and the card reads the catalog again. */
export function clearOfferError(messageId: string): void {
  if (offerLocal(messageId).kind !== "open") return;
  setLocal(messageId, OPEN);
}

/** Not now: the card closes, and nothing is sent. */
export function pressDismiss(messageId: string): void {
  if (offerLocal(messageId).kind !== "open") return;
  setLocal(messageId, { kind: "dismissed" });
}

/** The link of a saved skill. */
export function skillHref(skillId: string): string {
  return `/studio/skills/${encodeURIComponent(skillId)}`;
}

/** Tests only: forget every answer. */
export function __forgetOffersForTests(): void {
  remembered.clear();
  typedNames.clear();
}
