/**
 * "Not now": the member declines a card that parks a run (owner request,
 * 2026-10-18).
 *
 * The owner wrote: "if I don't want to answer a question for some reason, I
 * should be allowed to turn off the card. The agent should take it as input
 * that the user does not want to answer the question at the moment and might
 * want to do something else later on."
 *
 * ## The envelope
 *
 * A decline is one POST to `/api/agent/respond-input` (`lib/respondInput.ts`)
 * with `declined: true` and an empty `answer`. The route and the gateway
 * refuse a decline that carries an answer, and a `declined` that is not a
 * boolean. The parked run RESUMES: the tool result tells the model that the
 * member chose not to answer now, to go on without the answer, to use the
 * stated default and to say what it assumed
 * (`acb_skills.ask_tools.declined_text`). A decline is never a cancel.
 *
 * A card whose run already ended takes the decline too. The gateway closes
 * its durable row and starts no run: the member chose to do something else.
 *
 * ## The card after a decline
 *
 * It locks, and it shows {@link DECLINED_LINE} in muted words. It is never an
 * error. A blocking generative-UI card keeps the state across a reload: the
 * run's tool result records `declined: true` (`lib/askAnswers.ts`), and the
 * card reads {@link DECLINED_ANSWER} as its answer.
 *
 * The one control is `components/NotNow.tsx`. Fence: `declineAnswer.test.ts`.
 */

/** The label of the control, and the accessible name of the header's X. */
export const NOT_NOW = "Not now";

/** The title of a declined card. */
export const DECLINED_TITLE = "Not answered";

/** The words after the title. */
export const DECLINED_NOTE = "You can ask again later.";

/** The one muted line a declined card shows. */
export const DECLINED_LINE = `${DECLINED_TITLE} · ${DECLINED_NOTE}`;

/**
 * The words after "Not answered" on a declined question card. A card with a
 * stated default says what Metorite uses now, because a CRM default holds
 * the question for 24 hours, so "ask again later" would mislead. Any other
 * card keeps {@link DECLINED_NOTE}.
 */
export function declinedNoteFor(defaults: readonly string[]): string {
  const said = defaults.map((d) => d.trim()).filter(Boolean);
  if (said.length === 0) return DECLINED_NOTE;
  if (said.length === 1) {
    const one = said[0].replace(/[.\s]+$/, "");
    // "Keep them open for now" already says "for now": no second one.
    return /\bfor now$/i.test(one) ? `Metorite uses ${one}.` : `Metorite uses ${one} for now.`;
  }
  return "Metorite uses the stated defaults for now.";
}

/**
 * The answer a declined blocking card holds in the chat's answer map
 * (`askAnswers`). It is no answer the run can get: a picker sends
 * "Selected: …" and a form sends "<label> — <json>".
 */
export const DECLINED_ANSWER = "__declined__";

/** True when a card's held answer is a decline. */
export function isDeclinedAnswer(answer: string | null | undefined): boolean {
  return answer === DECLINED_ANSWER;
}

/** The body of a decline, for `sendRespondInputResult`. */
export function declineBody(requestId: string): {
  request_id: string;
  answer: string;
  was_freeform: boolean;
  declined: true;
} {
  return { request_id: requestId, answer: "", was_freeform: false, declined: true };
}

/**
 * One way out of a card: Submit or Not now, whichever comes first. The
 * second one does nothing, so a double click or a click on both never sends
 * an answer AND a decline. Returns the gate's two doors.
 */
export function cardExit(): {
  submit: (send: () => void) => boolean;
  decline: (send: () => void) => boolean;
  readonly taken: "submit" | "decline" | null;
} {
  let taken: "submit" | "decline" | null = null;
  const door = (kind: "submit" | "decline") => (send: () => void): boolean => {
    if (taken !== null) return false;
    taken = kind;
    send();
    return true;
  };
  return {
    submit: door("submit"),
    decline: door("decline"),
    get taken() {
      return taken;
    },
  };
}
