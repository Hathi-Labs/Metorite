/**
 * The flag of the skill offer in the chat (STU-F6a, `ai_studio.md` §14 F6a).
 * Off, no turn draws the offer card, and the chat reads no catalog for it.
 * On, a turn that passes the rule of `skillOffer.ts` draws one card.
 *
 * A file of its own, with no imports, as `myDayPeekFlag.ts` is. Turning it
 * on in production is the `enforcement-flip` act, after the owner preview
 * (D145).
 */

/** Whether the chat offers to save a finished turn as a skill. */
export function skillOfferOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  return process.env.NEXT_PUBLIC_SKILL_OFFER === "1";
}
