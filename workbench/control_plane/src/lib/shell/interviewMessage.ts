/**
 * The "Get to know me" message (WS-63 PZ-27, `personalization.md` §14.4).
 *
 * The button and the offer in the empty chat send this text as the member's
 * own turn. It names no tool and no "interview": it reads as words a person
 * would type (PZ-30). The orchestrator persona maps the request to
 * `profile_interview` with `next_sitting` (`_PROFILE_SWAP`).
 */
import { openAssistant } from "./assistant";

export const GET_TO_KNOW_ME_MESSAGE = "Get to know me. Ask me a few questions.";

/** Open a fresh assistant chat and send the request at once. */
export function startGetToKnowMe(): void {
  openAssistant({ fresh: true, text: GET_TO_KNOW_ME_MESSAGE });
}
