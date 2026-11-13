/**
 * The skill offer of a chat turn (STU-F6a, `project-docs/specs/ai_studio.md`
 * §14 F6a). After a finished turn, one card asks "Save this as a skill?".
 *
 * This file holds every rule of that card, so the node-env vitest holds them.
 * It makes no model call and no network call. `SkillOfferCard.tsx` draws it,
 * and `StudioToolCards.tsx` mounts it once, in the one `MessageBubble` mount.
 *
 * **When the card shows.** All five must be true (`skillOffer`):
 *
 * 1. The turn is finished: it does not stream.
 * 2. The chat is solo, the viewer ran the turn, and the viewer typed its
 *    prompt. The typed mark is the one of `lib/typedMark.ts`. The chat keeps
 *    it on the live message only, so a reloaded turn gets no offer.
 * 3. The turn has `MIN_CALLS` or more calls. Each call finished, and none is
 *    an error or a refusal.
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
 * that row.
 *
 * **The answer.** Save sends ONE `POST /api/studio/skills` through the
 * `create` that the card passes (`skillsApi.createSkill`). That makes a
 * private draft (D127.2), and Save never publishes. A second press while a
 * save is open sends nothing. Not now sends nothing.
 *
 * Fences: `skillOffer.test.ts` and `components/studio/SkillOfferCard.test.ts`.
 */

import { parseUploadNote } from "@/lib/chatUpload";
import { bareToolName, refusedText } from "@/lib/toolSteps";

import { refusedResult } from "./recordsCard";
import { MAX_ACTIONS, pickableActions } from "./skillForm";
import { starterFor } from "./skillStarter";
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

/** True when a call finished, and is no error and no refusal (rule 3). */
export function finishedCall(call: OfferCallLike): boolean {
  if (call.status !== "done") return false;
  return !refusedText(call.result) && !refusedResult(call.result);
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
  return calls.length >= MIN_CALLS && calls.every(finishedCall);
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

// ── The answer of the viewer, for this page ─────────────────────────────────

/** The state of the offer of one message. */
export type OfferLocal =
  | { kind: "open"; error: string | null }
  | { kind: "saving" }
  | { kind: "saved"; skillId: string }
  | { kind: "dismissed" };

const OPEN: OfferLocal = { kind: "open", error: null };
const MAX_REMEMBERED = 200;
const remembered = new Map<string, OfferLocal>();
const listeners = new Set<() => void>();

/**
 * The state of the offer of one message. A remount in this page (a switch of
 * chat and back, the dock and `/chat`) reads the same state. A reload starts
 * empty, and a reloaded turn has no typed mark, so it gets no offer.
 */
export function offerLocal(messageId: string): OfferLocal {
  return remembered.get(messageId) ?? OPEN;
}

/** True when the viewer answered the offer of the message (rule 5). */
export function answeredOffer(local: OfferLocal): boolean {
  return local.kind === "saved" || local.kind === "dismissed";
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
export type CreateSkill = (input: SkillDraft) => Promise<Reply<{ skill: Skill; version: SkillVersion | null }>>;

/**
 * Save: ONE create of a private draft. The state moves to `saving` before the
 * call starts, so a second press, here or in another card of the message,
 * sends nothing. A refusal keeps the card open with its text.
 */
export async function pressSave(offer: SkillOffer, create: CreateSkill): Promise<void> {
  if (offerLocal(offer.messageId).kind !== "open") return;
  setLocal(offer.messageId, { kind: "saving" });
  let reply: Awaited<ReturnType<CreateSkill>>;
  try {
    reply = await create({
      name: offer.draft.name,
      description: offer.draft.description,
      body: { ...offer.draft.body, actions: [...(offer.draft.body.actions ?? [])] },
    });
  } catch {
    reply = { ok: false, status: 0, detail: NOT_SAVED };
  }
  if (reply.ok && reply.data?.skill?.id) {
    setLocal(offer.messageId, { kind: "saved", skillId: reply.data.skill.id });
  } else {
    setLocal(offer.messageId, { kind: "open", error: (!reply.ok && reply.detail) || NOT_SAVED });
  }
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
}
