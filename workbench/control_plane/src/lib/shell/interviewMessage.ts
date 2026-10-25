/**
 * The "Get to know me" message (WS-63 PZ-27, `personalization.md` §14.4).
 *
 * The button and the offer in the empty chat send this text as the member's
 * own turn. It names no tool. The orchestrator persona maps the request to
 * `profile_interview` with `next_sitting` (`_PROFILE_SWAP`).
 */
import { openAssistant } from "./assistant";

export const GET_TO_KNOW_ME_MESSAGE = "Let's do the Get to know me interview.";

/** Open a fresh assistant chat and send the request at once. */
export function startGetToKnowMe(): void {
  openAssistant({ fresh: true, text: GET_TO_KNOW_ME_MESSAGE });
}
