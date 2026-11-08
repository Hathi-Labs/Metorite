/**
 * The form "Wait on someone" (WS-AP AP-9a, `waiting_on_me.md` §6.2 and §6.5).
 *
 * The pure rules live here, and `components/WaitOnSomeone.tsx` draws them. The
 * send goes through the ONE ask route (`POST /projects/tasks/{id}/asks`) and
 * the withdraw through `POST /projects/asks/{id}/withdraw`. Nobody who asks is
 * named in the body: the session is the asker (R5).
 *
 * Each refusal of §6.5 has its text here. The text says what happened and what
 * to do next.
 */

/** The server's `QUESTION_MAX`. */
export const QUESTION_MAX = 140;
/** The server's `LABEL_MAX`. */
export const CHOICE_MAX = 80;
export const CHOICES_MIN = 2;
export const CHOICES_LIMIT = 3;

export const ASK_COPY = {
  heading: "Wait on someone",
  who: "Who",
  need: "Need",
  needHint: "140 characters at most.",
  choices: "Choices (optional, 2 or 3)",
  addChoice: "Add a choice",
  due: "By when (optional)",
  send: "Send",
  cancel: "Cancel",
  withdraw: "Withdraw",
  personal: "Move it to a project to ask someone",
  move: "Move to project…",
} as const;

export interface AskDraft {
  addressee: string;
  question: string;
  /** Free text of each choice. Blank rows are dropped. */
  choices: readonly string[];
  /** `YYYY-MM-DD` from a date input, or an empty string. */
  due: string;
}

export const EMPTY_DRAFT: AskDraft = { addressee: "", question: "", choices: [], due: "" };

/** The end of a day in the member's own zone, as a moment with a zone. */
export function dueAt(day: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day.trim());
  if (!m) return null;
  const end = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 0);
  return Number.isNaN(end.getTime()) ? null : end.toISOString();
}

/** The choices that carry text. */
export function keptChoices(choices: readonly string[]): string[] {
  return choices.map((c) => c.trim()).filter(Boolean);
}

/** The reason the draft cannot send yet, or `null` when it can. */
export function draftProblem(draft: AskDraft): string | null {
  if (!draft.addressee.trim().includes("@")) return "Pick the person to ask.";
  if (draft.addressee.trim().startsWith("agent:")) return "Pick a person. An agent takes no ask.";
  const q = draft.question.trim();
  if (!q) return "Write what you need.";
  if (q.length > QUESTION_MAX) return `Keep the need to ${QUESTION_MAX} characters.`;
  const kept = keptChoices(draft.choices);
  if (kept.length === 1) return `Give ${CHOICES_MIN} or ${CHOICES_LIMIT} choices, or none.`;
  if (kept.length > CHOICES_LIMIT) return `Give ${CHOICES_MIN} or ${CHOICES_LIMIT} choices, or none.`;
  if (kept.some((c) => c.length > CHOICE_MAX)) return `Keep each choice to ${CHOICE_MAX} characters.`;
  if (draft.due.trim() && dueAt(draft.due) === null) return "The date is not valid.";
  return null;
}

export interface AskBody {
  addressee: string;
  question: string;
  choices?: { key: string; label: string }[];
  due_at?: string;
}

/** The body of the one ask route. Nobody who asks is named in it. */
export function askBody(draft: AskDraft): AskBody {
  const body: AskBody = {
    addressee: draft.addressee.trim(),
    question: draft.question.trim(),
  };
  const kept = keptChoices(draft.choices);
  if (kept.length >= CHOICES_MIN) {
    body.choices = kept.map((label, i) => ({ key: `c${i + 1}`, label }));
  }
  const due = dueAt(draft.due);
  if (due) body.due_at = due;
  return body;
}

/** The code and the message of a refusal, from the gateway's `detail`. */
export function refusalOf(detail: unknown): { code: string; message: string } {
  if (detail && typeof detail === "object") {
    const rec = detail as { error?: unknown; message?: unknown };
    return {
      code: typeof rec.error === "string" ? rec.error : "",
      message: typeof rec.message === "string" ? rec.message : "",
    };
  }
  return { code: "", message: typeof detail === "string" ? detail : "" };
}

/** The text of each refusal of §6.5, and the next step. */
export function askFailure(status: number | undefined, detail: unknown): string {
  const { code, message } = refusalOf(detail);
  if (status === 422 && code === "addressee_cannot_open") {
    return "This person cannot open the task. Share the project with them first.";
  }
  if (status === 422 && code === "self_ask") return "You cannot ask yourself. Pick someone else.";
  if (status === 409 && code === "ask_open") {
    return "You already wait on this person here. Wait for the answer, or withdraw the ask first.";
  }
  if (status === 429) return "You asked enough people today. Try again tomorrow.";
  if (status === 422 && code === "bad_due") return "Pick a day that is ahead, and not too far away.";
  if (status === 422 && code === "bad_choices") {
    return `Give ${CHOICES_MIN} or ${CHOICES_LIMIT} choices, or none.`;
  }
  if (status === 403) return "A person asks a person. Send this ask from Metorite, not from an agent.";
  if (status === 404) return "This task is not open to you now. Reload the page.";
  if (status === 422 && message) return message;
  return "Could not send the ask. Your text is still here. Try again.";
}

/** The failure copy of a withdraw. */
export function withdrawFailure(status: number | undefined, detail: unknown): { text: string; reload: boolean } {
  if (status === 409) {
    return { text: "This ask has an answer or is closed. The card shows it now.", reload: true };
  }
  if (status === 404) {
    return { text: "This ask is not yours, or it no longer exists.", reload: true };
  }
  void detail;
  return { text: "Could not withdraw the ask. Try again.", reload: false };
}
