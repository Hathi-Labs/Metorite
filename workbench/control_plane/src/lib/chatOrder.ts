/**
 * chatOrder — the ONE merge of server rows into a chat's `messages`
 * (task 4146, owner report 2026-11-28).
 *
 * On production a reply drew ABOVE the prompt that it answers. The saved
 * rows were in the right order. Two client merges flipped them: the history
 * load on open dropped the prompt of the turn in flight, and the recovery
 * poll then appended the prompt after its reply.
 *
 * The rule, for every merge of rows that the server returned:
 *
 * 1. **The server's order wins for the rows the server returned.** A row
 *    that the chat holds already takes the server's order, in the slots
 *    that those rows hold now. A new row goes after its nearest server
 *    predecessor in the list, else before its nearest server successor in
 *    the list. With no server row in the list, it goes before every local
 *    row. Nothing here compares a timestamp or an id: a browser clock and a
 *    server clock disagree, and a random id says nothing about order.
 * 2. **So a reply never draws before the prompt of its own turn.** The
 *    gateway saves the prompt first (`routes/agent.py`), and its order puts
 *    the prompt first.
 * 3. **A local row that the server does not know keeps its place.** A turn
 *    in flight stays after every row that the server returned with it.
 * 4. **The content of a row only grows** (`growRow`), as the poll did.
 *
 * Pure. Callers: the recovery poll and the re-attach in
 * `hooks/useAgentChat.ts`, and the history load in `components/AgentChat.tsx`.
 * Do not add a second merge of server rows beside this one.
 *
 * Fence (R7): `chatOrder.test.ts`.
 */

import type { ChatMessage } from "@/lib/chatStore";

export interface ServerMergeOptions {
  /** One row of the same id from the chat and the server. The default takes
   *  the server's row. Return `local` itself for "no change". */
  combine?: (local: ChatMessage, server: ChatMessage) => ChatMessage;
  /** Whether a chat row that the server did not return stays. The default
   *  keeps every row. */
  keepLocal?: (m: ChatMessage) => boolean;
}

/**
 * Merge `server` (in the server's order) into `local` (the chat's list).
 * Returns `local` itself when nothing changed, so a caller can skip a write.
 */
export function mergeServerRows(
  local: readonly ChatMessage[],
  server: readonly ChatMessage[],
  opts: ServerMergeOptions = {},
): ChatMessage[] {
  const combine = opts.combine ?? ((_l: ChatMessage, s: ChatMessage) => s);
  const keepLocal = opts.keepLocal ?? (() => true);

  // The server's rows, each id once, in the server's order.
  const rows: ChatMessage[] = [];
  const at = new Map<string, number>();
  for (const s of server) {
    if (at.has(s.id)) continue;
    at.set(s.id, rows.length);
    rows.push(s);
  }

  // 1. The chat's rows that stay: each row the server returned, and each
  //    local row that the caller keeps. One row per id.
  const seen = new Set<string>();
  const base: ChatMessage[] = [];
  for (const m of local) {
    if (seen.has(m.id)) continue;
    if (!at.has(m.id) && !keepLocal(m)) continue;
    seen.add(m.id);
    base.push(m);
  }

  // 2. The rows that the chat holds take the server's order, in their own
  //    slots. A local row never moves.
  const slots: number[] = [];
  base.forEach((m, i) => { if (at.has(m.id)) slots.push(i); });
  const held = slots.map((i) => base[i]).sort((a, b) => at.get(a.id)! - at.get(b.id)!);
  const out = base.slice();
  slots.forEach((slot, k) => {
    const mine = held[k];
    out[slot] = combine(mine, rows[at.get(mine.id)!]);
  });

  // 3. Each new row goes after its nearest server predecessor in the list,
  //    else before its nearest server successor, else before every local row.
  const present = new Set(out.map((m) => m.id));
  rows.forEach((s, i) => {
    if (present.has(s.id)) return;
    let pos = 0;
    let j = i - 1;
    while (j >= 0 && !present.has(rows[j].id)) j--;
    if (j >= 0) {
      pos = out.findIndex((m) => m.id === rows[j].id) + 1;
    } else {
      let k = i + 1;
      while (k < rows.length && !present.has(rows[k].id)) k++;
      if (k < rows.length) pos = out.findIndex((m) => m.id === rows[k].id);
    }
    out.splice(pos, 0, s);
    present.add(s.id);
  });

  const same = out.length === local.length && out.every((m, i) => m === local[i]);
  return same ? (local as ChatMessage[]) : out;
}

/**
 * The poll's "only grows" rule for one row of the same id. Returns `local`
 * itself when the server's row holds nothing new.
 */
export function growRow(local: ChatMessage, rm: ChatMessage): ChatMessage {
  // Custom events count: the run-boundary fold persists generative_ui and
  // artifact cards that this client may never have seen (dropped SSE).
  // Authorship arriving for the first time is news too: an optimistic local
  // turn has no author until the server stamps one.
  const grew =
    rm.content.length > local.content.length ||
    (rm.toolEvents?.length ?? 0) > (local.toolEvents?.length ?? 0) ||
    (rm.reasoningBlocks?.length ?? 0) > (local.reasoningBlocks?.length ?? 0) ||
    (rm.customEvents?.length ?? 0) > (local.customEvents?.length ?? 0) ||
    (!!rm.authorKind && !local.authorKind);
  if (!grew) return local;
  // Spreading `rm` wholesale used to REPLACE the local stream artifacts with
  // the server's, which blanked the cards this client had drawn live. Each
  // artifact list is monotonic: keep whichever side has more.
  const richer = <T,>(a?: T[], b?: T[]): T[] | undefined =>
    (a?.length ?? 0) >= (b?.length ?? 0) ? a : b;
  return {
    ...local,
    ...rm,
    // The text must not shrink because the server row is a beat behind. A
    // redacted row is the exception: its notice always wins.
    content: rm.redacted || rm.content.length >= local.content.length ? rm.content : local.content,
    toolEvents: richer(rm.toolEvents, local.toolEvents),
    customEvents: richer(rm.customEvents, local.customEvents),
    reasoningBlocks: richer(rm.reasoningBlocks, local.reasoningBlocks),
    progressLines: richer(rm.progressLines, local.progressLines),
    segments: richer(rm.segments, local.segments),
    // Authorship only grows: an older row carries `undefined`, and it must
    // not un-attribute a turn that the chat already names.
    authorEmail: rm.authorEmail ?? local.authorEmail,
    authorKind: rm.authorKind ?? local.authorKind,
    redacted: rm.redacted ?? local.redacted,
    redactedCaps: rm.redactedCaps ?? local.redactedCaps,
    streaming: false,
  };
}

/**
 * The recovery poll's merge (`useAgentChat`). `remote` is the server's
 * newest window, in its order, with the rows an edit removed already taken
 * out by the caller.
 *
 * A server reply that extends a local partial of ANOTHER id takes that
 * partial's place (refresh recovery: a local id against an old server id).
 * Only a row that the server does not know is such a partial. A reply that
 * the server returned under its own id is never one, so a new reply such as
 * "Done. More." never replaces "Done." two turns up.
 */
export function mergePolledRows(local: readonly ChatMessage[], remote: readonly ChatMessage[]): ChatMessage[] {
  const serverIds = new Set(remote.map((m) => m.id));
  const localIds = new Set(local.map((m) => m.id));
  const localAssistantContents = local.filter((m) => m.role === "assistant").map((m) => m.content);
  let base: ChatMessage[] = local as ChatMessage[];
  const server: ChatMessage[] = [];
  for (const rm of remote) {
    if (localIds.has(rm.id)) { server.push(rm); continue; }
    if (rm.role === "assistant" && rm.content) {
      const partial = base.findIndex(
        (m) =>
          m.role === "assistant" &&
          !serverIds.has(m.id) &&
          !!m.content &&
          rm.content.startsWith(m.content) &&
          rm.content.length >= m.content.length,
      );
      if (partial >= 0) {
        const old = base[partial];
        base = base.slice();
        // The same "only grows" rule: the local partial may already know
        // which agent spoke when this server row does not.
        base[partial] = {
          ...rm,
          authorEmail: rm.authorEmail ?? old.authorEmail,
          authorKind: rm.authorKind ?? old.authorKind,
          streaming: false,
        };
        server.push(rm);
        continue;
      }
      // The same words are on screen already.
      if (localAssistantContents.includes(rm.content)) continue;
    }
    server.push(rm);
  }
  return mergeServerRows(base, server, { combine: growRow });
}

/**
 * The ids of the turn in flight: the member's turn before the live reply,
 * the reply, and every row after it (a steer, a notice). Empty with no live
 * reply in the list.
 */
export function liveTurnIds(local: readonly ChatMessage[], liveId: string | null): Set<string> {
  const out = new Set<string>();
  if (!liveId) return out;
  const at = local.findIndex((m) => m.id === liveId);
  if (at < 0) return out;
  let from = at;
  for (let i = at - 1; i >= 0; i--) {
    if (local[i].role === "user") { from = i; break; }
  }
  for (let i = from; i < local.length; i++) out.add(local[i].id);
  return out;
}

/** A row that has not reached the server yet. */
function unsent(m: ChatMessage): boolean {
  return !!m.awaitingServer || !!m.pendingDelivery || m.steerAck === "sending";
}

/**
 * The history load on open (`AgentChat`): the server's full history takes
 * the chat's place, as before. Two kinds of local row stay, after every
 * saved row: the turn in flight (the live reply `liveId` and the member's
 * turn before it), and a row that has not reached the server yet. The load
 * may have read before the gateway saved the prompt, and dropping it was
 * the incident of task 4146. The live reply keeps the stream's own copy.
 */
export function hydrateRows(
  local: readonly ChatMessage[],
  remote: readonly ChatMessage[],
  liveId: string | null,
): ChatMessage[] {
  const live = liveTurnIds(local, liveId);
  return mergeServerRows(local, remote, {
    combine: (l, s) => (l.id === liveId ? l : s),
    keepLocal: (m) => live.has(m.id) || unsent(m),
  });
}

/**
 * The re-attach (`useAgentChat.attach`): the rows that `planReplay` chose,
 * placed by the server's order among the rows the chat holds. The chat's
 * own rows keep their content. The target is added only when the chat
 * lacks it, as before.
 */
export function placeReplayRows(
  local: readonly ChatMessage[],
  saved: readonly ChatMessage[],
  plan: { target: ChatMessage | null; missing: readonly ChatMessage[] },
): ChatMessage[] {
  const have = new Set(local.map((m) => m.id));
  const add = new Set(plan.missing.map((m) => m.id));
  if (plan.target && !have.has(plan.target.id)) add.add(plan.target.id);
  if (add.size === 0) return local as ChatMessage[];
  const rows = saved
    .map((m) => (plan.target && m.id === plan.target.id ? plan.target : m))
    .filter((m) => have.has(m.id) || add.has(m.id));
  return mergeServerRows(local, rows, { combine: (l) => l });
}
