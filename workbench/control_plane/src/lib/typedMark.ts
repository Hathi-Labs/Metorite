/**
 * The ONE positive mark of a message that the member typed (WS-63 D-PZ-37,
 * the owner, 2026-11-02). Spec: `project-docs/specs/personalization.md`
 * §15.3 and §15.11.
 *
 * Only a message that the member typed in the message box carries the mark.
 * The gateway binds the member's typed words only with it, and the stored row
 * keeps it (`chat_message.member_typed`). A message with no mark counts for
 * nothing: it grounds no profile save, and the summary of a chat never reads
 * it. So a tapped "Follow up" pill, a choice in a bubble, a card action, a
 * feed starter and the Fix prompt send no mark.
 *
 * The rules here are pure. `components/AgentChat.tsx` holds the one composer
 * that sets the mark, and `typedMark.test.ts` fences each send path.
 */

/** The request field of the mark: `{ typed: true }`, or nothing. Only the
 *  value `true` counts, so a missing or odd value sends no field. */
export function typedField(typed: unknown): { typed: true } | Record<string, never> {
  return typed === true ? { typed: true } : {};
}

/**
 * The words in the message box, and whether a send of them carries the mark.
 *
 * Words that the member types carry it. Words that a program puts in the box
 * carry it only when they are the member's own typed words, such as a send
 * that failed and came back. The Fix prompt and the words of a tap do not.
 * One untyped part takes the mark from the whole box, until the box is empty.
 */
export interface ComposerMark {
  /** True while the box holds words that the member did not type. */
  untyped: boolean;
}

/** The mark of an empty box: a send of words typed from now on is typed. */
export const EMPTY_BOX: ComposerMark = { untyped: false };

/** The box after a program puts words in it. `typed` says whether those words
 *  are the member's own typed words. */
export function filled(prev: ComposerMark, typed: boolean): ComposerMark {
  return { untyped: prev.untyped || typed !== true };
}

/** The box after its text changes. An empty box starts clean. */
export function edited(prev: ComposerMark, text: string): ComposerMark {
  return text === "" ? EMPTY_BOX : prev;
}

/** Whether a send from the box carries the mark. */
export function composerTyped(mark: ComposerMark): boolean {
  return !mark.untyped;
}
