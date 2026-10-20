/**
 * The reply form of a confirmation card (EM-CR, `email_app_master_plan.md` §18).
 *
 * The email assistant's `compose_reply` parks on ONE confirmation card with a
 * `reply` form (`request_confirmation(..., reply=form)` in
 * `acb_skills/ask_tools.py`). The card shows the mail it answers, the
 * recipients the gateway derived for Reply and for Reply all, a toggle, the
 * editable body, and Send, Save to Drafts and Discard. The member's choice
 * goes back in ONE respond-input as `APPROVE {"reply": {...}}`.
 *
 * The client never derives a recipient. It shows the server's two lists and
 * switches between them, and it echoes the list it showed in the answer, so
 * the gateway can refuse a reply whose recipients changed after the card.
 * The server owns the shape (`acb_skills/reply_card.py`, `REPLY_ANSWER_KEYS`),
 * and `tests/unit/test_email_chat_reply.py` reads this file to hold
 * the two in step.
 *
 * Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/replyCard.test.ts`.
 */

/** The prefix of an Approve with a payload (`REPLY_ANSWER_PREFIX`). */
export const REPLY_ANSWER_PREFIX = "APPROVE ";

/** The longest body the server takes (`MAX_REPLY_BODY`). */
export const MAX_REPLY_BODY = 20_000;

/** The most addresses of one list (`MAX_REPLY_RECIPIENTS`). */
export const MAX_REPLY_RECIPIENTS = 100;

export interface ReplyRecipients {
  to: string[];
  cc: string[];
}

export interface ReplyForm {
  /** The email the reply answers. The answer names it, so it acts on no other. */
  messageId: string;
  /**
   * Lines the card must show and never hide: a Reply-To that is not the
   * sender, and the text the send adds after the card (review of #1191).
   */
  notices: string[];
  /** The sending mailbox, as "label · address". */
  mailbox: string;
  /** The subject of the reply ("Re: …"). */
  subject: string;
  /** The mail the reply answers. The sender wrote the excerpt. */
  original: { from: string; subject: string; received: string; excerpt: string };
  /** The server's recipients for each choice. */
  recipients: { reply: ReplyRecipients; reply_all: ReplyRecipients };
  /** The choice the card opens with. */
  replyAll: boolean;
  /** The draft, which the member edits. */
  body: string;
  /** A line from the tool, for example when it wrote no draft. */
  note: string;
}

export type ReplyAction = "send" | "save";

export type ReplyApproval = `APPROVE {"reply":${string}`;

const text = (v: unknown, max = 500): string => (typeof v === "string" ? v.slice(0, max) : "");

function addresses(v: unknown): string[] | null {
  if (!Array.isArray(v) || v.length > MAX_REPLY_RECIPIENTS) return null;
  const out: string[] = [];
  for (const a of v) {
    if (typeof a !== "string" || !a.trim()) return null;
    out.push(a.trim());
  }
  return out;
}

function recipients(v: unknown): ReplyRecipients | null {
  if (!v || typeof v !== "object") return null;
  const r = v as Record<string, unknown>;
  const to = addresses(r.to);
  const cc = addresses(r.cc ?? []);
  if (!to || !cc || to.length === 0) return null;
  return { to, cc };
}

/**
 * The reply form of a `confirmation_requested` value, or null when it has
 * none, or when the card cannot read it. A form the card cannot read is no
 * form: the card then has nothing to send, and it says so.
 */
export function replyFromEvent(value: unknown): ReplyForm | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const rec = v.recipients && typeof v.recipients === "object" ? (v.recipients as Record<string, unknown>) : {};
  const reply = recipients(rec.reply);
  const replyAll = recipients(rec.reply_all);
  if (!reply || !replyAll) return null;
  const o = v.original && typeof v.original === "object" ? (v.original as Record<string, unknown>) : {};
  const messageId = typeof v.message_id === "string" ? v.message_id.trim() : "";
  if (!messageId) return null;
  const notices = Array.isArray(v.notices) ? v.notices.filter((n): n is string => typeof n === "string" && !!n.trim()) : [];
  return {
    messageId,
    notices,
    mailbox: text(v.mailbox),
    subject: text(v.subject),
    original: {
      from: text(o.from),
      subject: text(o.subject),
      received: text(o.received, 64),
      excerpt: text(o.excerpt, 1200),
    },
    recipients: { reply, reply_all: replyAll },
    replyAll: v.reply_all === true,
    body: text(v.body, MAX_REPLY_BODY),
    note: text(v.note),
  };
}

/** The recipients the card shows, for the member's choice. */
export function recipientsFor(form: ReplyForm, replyAll: boolean): ReplyRecipients {
  return replyAll ? form.recipients.reply_all : form.recipients.reply;
}

const lower = (list: string[]) => list.map((a) => a.toLowerCase());

/** Reply all adds someone. When it does not, the toggle has nothing to switch. */
export function replyAllAddsSomeone(form: ReplyForm): boolean {
  const a = form.recipients.reply;
  const b = form.recipients.reply_all;
  return JSON.stringify([lower(a.to), lower(a.cc)]) !== JSON.stringify([lower(b.to), lower(b.cc)]);
}

/** The body may go: it holds text, and it fits. */
export function bodyCanGo(body: string): boolean {
  return body.trim().length > 0 && body.length <= MAX_REPLY_BODY;
}

/**
 * The answer of Send or Save to Drafts, or null when the body may not go.
 * The keys are exactly `REPLY_ANSWER_KEYS`, and `to` and `cc` are the lists
 * the card showed. The body goes as typed, with no trim.
 */
export function replyAnswer(
  form: ReplyForm,
  choice: { action: ReplyAction; replyAll: boolean; body: string },
): ReplyApproval | null {
  if (!bodyCanGo(choice.body)) return null;
  const shown = recipientsFor(form, choice.replyAll);
  const reply = {
    action: choice.action,
    reply_all: choice.replyAll,
    body: choice.body,
    to: shown.to,
    cc: shown.cc,
    message_id: form.messageId,
  };
  return `${REPLY_ANSWER_PREFIX}${JSON.stringify({ reply })}` as ReplyApproval;
}

/** The time of the mail, short, or "" when it does not parse. */
export function receivedText(iso: string, locale?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(locale, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** The lines of a body, as the card counts them. */
export function lineCount(body: string): number {
  return body.split("\n").length;
}

/** The summary of the card: who gets the reply now, each address in full. */
export function replySummary(form: ReplyForm, replyAll: boolean): string {
  return `Reply to ${recipientsFor(form, replyAll).to.join(", ")}`;
}

/** What the buttons do, in one line under them. */
export function replyHint(form: ReplyForm): string {
  const box = form.mailbox ? ` from ${form.mailbox}` : "";
  return `Send replies now${box}. Save to Drafts keeps it in your mailbox and sends nothing. Discard drops it.`;
}
