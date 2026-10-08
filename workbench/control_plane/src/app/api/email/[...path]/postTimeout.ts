/**
 * How long the email catch-all proxy waits for a POST to the gateway.
 *
 * Most email endpoints answer in well under a second, so a tight 30s abort
 * keeps a genuinely hung upstream from pinning a Next connection. But the AI
 * drafting / agent endpoints legitimately run long: "Draft with AI" on an
 * empty reply body orchestrates memory retrieval + a routing LLM call + a
 * specialist agent (up to ~18s) + a tier-powerful generation, which routinely
 * passes 30s. Aborting those at 30s surfaced to the user as a "502 gateway
 * error" with no draft produced. The known LLM/agent-backed POST endpoints
 * get the same budget the GET handler uses for large downloads (120s).
 *
 * WS-17 EM-T6e (D1, spec §10.4.7): "Remove older mail from Metorite"
 * (`POST accounts/<id>/storage/remove-older`) also gets 120s. Its chunk loop
 * runs inside the request, and a large mailbox can take longer than 30s. Its
 * path holds the id of a mailbox, so an exact-path Set cannot name it, and a
 * pattern does. The dialog follows a removal that outlives even this budget
 * (D1), so the budget only makes that path rarer.
 *
 * This lives in its own module so a test can import it. A Next route file
 * may export only its handlers and its route config. Fence:
 * `postTimeout.test.ts` beside this file.
 */

export const AI_SLOW_TIMEOUT_MS = 120_000;
export const DEFAULT_POST_TIMEOUT_MS = 30_000;

/** The exact joined paths of the AI and agent POSTs. */
export const AI_SLOW_POST_PATHS: ReadonlySet<string> = new Set([
  "compose-assist",
  "draft-reply",
  "ai/chat",
  "ai/quick-action",
  "rules/generate",
  "rules/test",
  "rules/test/recent",
  "rules/run-message",
  "rules/patterns/review",
  "assistant/writing-style/generate",
  "voice-profile/sample",
  "messages/summaries",
  "senders/categorize",
  "follow-ups/scan",
]);

/**
 * True for `accounts/<id>/storage/remove-older` and for nothing else: four
 * segments, a non-empty id, and the two fixed segments after it.
 */
export function isStorageRemoval(path: readonly string[]): boolean {
  return (
    path.length === 4 &&
    path[0] === "accounts" &&
    path[1] !== "" &&
    path[2] === "storage" &&
    path[3] === "remove-older"
  );
}

/** The abort budget of one POST, by the path segments after `/email/`. */
export function postTimeoutMs(path: readonly string[]): number {
  return AI_SLOW_POST_PATHS.has(path.join("/")) || isStorageRemoval(path)
    ? AI_SLOW_TIMEOUT_MS
    : DEFAULT_POST_TIMEOUT_MS;
}
