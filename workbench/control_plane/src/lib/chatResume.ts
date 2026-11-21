/**
 * chatResume — the small pure rules of a chat that takes a resumed run
 * (RESUME-3a, `project-docs/specs/chat_run_continuity.md` §4.12.4 item 5).
 *
 * A late Approve of a parked card starts a NEW run on the thread (case B of
 * §4.12.4 item 2): its own run id, its own reply row at the end of the chat,
 * and no member turn. The run state (`lib/runStatus.ts`, the `resumed` event)
 * and the answer POST (`lib/respondInput.ts`, `resumedOf`) hold the rest.
 *
 * RESUME-3b adds case A (RESUME-2b): when the parked turn is the last turn,
 * the run goes on in the SAME reply, under the parked run's id. Its stream
 * holds a second `RUN_STARTED` of that run, marked `resumed`. The chat
 * translator sends it on as the frame `run_resumed`, and the attach loop then
 * keeps the reply open.
 *
 * RESUME-3d adds the reply row that the server list names for a live
 * continuation (`attachPickOf`). A tab that did not get the 200 then replays
 * the run into that row, with no read of the saved rows.
 *
 * Fence (R7): `chatResume.test.ts`.
 */

import type { ChatMessage } from "@/lib/chatStore";
import type { RunStatus } from "@/lib/runStatus";

/**
 * True when the run goes on in the reply that parked (RESUME-3b, case A):
 * the run's key is the key of the run that parked. The `attach` event keeps
 * both keys, so this stays true while the replay reads.
 */
export function goesOnInParkedReply(run: Pick<RunStatus, "runKey" | "parkedRunKey">): boolean {
  return run.runKey !== null && run.runKey === run.parkedRunKey;
}

/**
 * The message that the "Approved" line of a card draws under: the last
 * message before the reply of the run that goes on now. With no such reply
 * it is the last message. Null for an empty chat, and the line then draws at
 * the foot.
 *
 * RESUME-3b: in case A (`sameReply`) the run goes on in the parked reply, so
 * the line draws under that reply, the run's own one.
 */
export function receiptAnchorBeforeRun(
  messages: readonly Pick<ChatMessage, "id" | "role" | "content">[],
  runMessageId: string | null,
  sameReply = false,
): string | null {
  if (sameReply && runMessageId && messages.some((m) => m.id === runMessageId)) return runMessageId;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.id === runMessageId && !sameReply) continue;
    // A notice line is no turn, so a line never draws under it.
    if (m.role === "system" && m.content.startsWith("__ERROR__")) continue;
    return m.id;
  }
  return null;
}

/**
 * The cards to ask the server about when it stops listing a parked thread
 * (RESUME-3a step 7). Each blocking card that still waits on screen, and each
 * card that a remount drew again as waiting. The caller hands the set to
 * `cardsToSettle`, so a failed read settles nothing.
 */
export function waitingCardIds(
  cards: readonly { requestId?: string; redraft?: unknown }[],
  questions: readonly (string | null | undefined)[],
  redrawnWaiting: ReadonlySet<string>,
): Set<string> {
  const out = new Set(redrawnWaiting);
  for (const c of cards) if (c.requestId && !c.redraft) out.add(c.requestId);
  for (const q of questions) if (q) out.add(q);
  return out;
}

// ── RESUME-3b: a run that goes on after its park ───────────────────────────

/** The frame that the chat translator sends for a `RUN_STARTED` marked
 *  `resumed`: the same run goes on after its park (case A). */
export const RUN_RESUMED_FRAME = "run_resumed";

/**
 * The frame of one gateway event that says the run goes on after its park,
 * or null. Only a `RUN_STARTED` with `resumed: true` makes one. The caller
 * checks the run binding first, so a start of another run never gets here.
 * Every other event, a plain `RUN_STARTED` too, makes no frame.
 */
export function runResumedFrameOf(
  ev: { type?: unknown; resumed?: unknown; runId?: unknown; run_id?: unknown },
): { type: typeof RUN_RESUMED_FRAME; run_id: string } | null {
  if (ev.type !== "RUN_STARTED" || ev.resumed !== true) return null;
  const id = typeof ev.runId === "string" ? ev.runId : typeof ev.run_id === "string" ? ev.run_id : "";
  return { type: RUN_RESUMED_FRAME, run_id: id };
}

/** How a replay ends so far: `terminal` after a `done`, and `parked` when
 *  that `done` was a park. */
export interface ReplayEnd {
  terminal: boolean;
  parked: boolean;
}

/** A replay that has read no end yet. */
export const REPLAY_OPEN: ReplayEnd = Object.freeze({ terminal: false, parked: false });

/**
 * The end of a replay after one more frame. The end is the LAST `done` after
 * the last `run_resumed`. A `done` in the middle of a stream (a park, a Stop
 * in the window of the start, the synthetic end of a reconnect) ends nothing
 * when the same run goes on after it. So a `run_resumed` opens the end again,
 * and a cut after it reads as not finished. Any other frame keeps the end.
 */
export function replayEndAfter(end: ReplayEnd, frame: { type?: unknown; parked?: unknown }): ReplayEnd {
  if (frame.type === "done") return { terminal: true, parked: frame.parked === true };
  if (frame.type === RUN_RESUMED_FRAME) return REPLAY_OPEN;
  return end;
}

// ── RESUME-3d: the reply row that the list names ───────────────────────────

/** Where a re-attach replays (`useAgentChat.attach`). */
export interface AttachPick {
  /** The reply that this chat shows and that the replay goes into, or null. */
  target: ChatMessage | null;
  /** Read the saved rows to find the target, as before RESUME-3d. */
  readSaved: boolean;
  /** The id that the replay takes when no target shows (`replayIdOf`). */
  resumeId: string | null;
}

/**
 * The target of a re-attach, before any read of the saved rows.
 *
 * - With a reply row that the list names (`listedReplyId`), that row is the
 *   target. When the chat does not show it yet, the replay takes its id.
 *   No saved row is read, so no older reply can be the target. Case A
 *   names the parked reply, and case B names the new one.
 * - With none, the rules of before hold. The reply that this tab knows is
 *   the target, else an interrupted last reply when no stale tail holds,
 *   else the saved rows decide.
 */
export function attachPickOf(
  run: Pick<RunStatus, "messageId" | "listedReplyId" | "staleTail" | "resumedAt">,
  messages: readonly ChatMessage[],
  interrupted: (m: ChatMessage | undefined) => boolean,
): AttachPick {
  const reply = (id: string | null) =>
    (id ? messages.find((m) => m.id === id && m.role === "assistant") : undefined) ?? null;
  if (run.listedReplyId) {
    return { target: reply(run.listedReplyId), readSaved: false, resumeId: run.listedReplyId };
  }
  const last = messages[messages.length - 1];
  const target = reply(run.messageId) ?? (!run.staleTail && interrupted(last) ? last : null);
  return {
    target,
    readSaved: target === null,
    resumeId: run.resumedAt > 0 ? run.messageId : null,
  };
}
