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
 * switches between them. Each To and Cc has a tick box (owner, 2026-10-24),
 * and the answer names the people the member left ticked. The gateway takes
 * only a subset of its own list, so a tick can take a person off and never
 * add one, and a list that changed after the card sends nothing.
 * The server owns the shape (`acb_skills/reply_card.py`, `REPLY_ANSWER_KEYS`),
 * and `tests/unit/test_email_chat_reply.py` reads this file to hold
 * the two in step.
 *
 * Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/replyCard.test.ts`.
 */

import { classOf, type PersonClass } from "@/lib/sendCard";

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
  /**
   * The local id of the draft that the card wrote to Drafts (owner,
   * 2026-10-22). The member's Discard names it, and only it can go.
   */
  draftId?: string;
  /**
   * §21, a held chat only: the server's class of each address, by its lower
   * case. A form with none is exactly as before, and draws no mark.
   */
  classes?: Record<string, PersonClass>;
  /**
   * The tick boxes (owner, 2026-10-24): the people of each choice that start
   * ticked, as the server chose them. In a held chat a new address starts
   * unticked. A form with none starts with each person ticked.
   */
  ticked?: { reply: ReplyRecipients; reply_all: ReplyRecipients };
  /**
   * The server's words for each person, by lower case (owner, 2026-10-24):
   * "on Dr Sagar's original email", "you usually copy them". A form with
   * none draws the class mark only.
   */
  marks?: Record<string, string>;
}

/** The ticked people of each choice, by lower case. */
export interface ReplyTicks {
  reply: string[];
  reply_all: string[];
}

export type ReplyAction = "send" | "save";

export type ReplyApproval = `APPROVE {"reply":${string}`;

const text = (v: unknown, max = 500): string => (typeof v === "string" ? v.slice(0, max) : "");

const lower = (list: string[]) => list.map((a) => a.toLowerCase());

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
  // A start that names a person off the list is a form the card cannot read.
  const ticked = tickedOf(v.ticked, { reply, reply_all: replyAll });
  if (ticked === null) return null;
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
    draftId: text(v.draft_id, 64),
    ...classesOf(v.classes),
    ...(ticked ? { ticked } : {}),
    ...marksOf(v.marks, replyAll),
  };
}

/** The words of each person, only for a person of the card's list. */
function marksOf(value: unknown, all: ReplyRecipients): { marks?: Record<string, string> } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const have = new Set(lower([...all.to, ...all.cc]));
  const out: Record<string, string> = {};
  for (const [addr, words] of Object.entries(value as Record<string, unknown>)) {
    const a = addr.trim().toLowerCase();
    if (have.has(a) && typeof words === "string" && words.trim()) out[a] = words.slice(0, 200);
  }
  return Object.keys(out).length > 0 ? { marks: out } : {};
}

/** The server's words for one person, or "" (owner, 2026-10-24, item 8). */
export function markFor(form: ReplyForm, address: string): string {
  return form.marks?.[address.trim().toLowerCase()] ?? "";
}

/**
 * The card shows ONE list (owner, 2026-10-24, item 11): every person of the
 * email, and each person the server added. There is no Reply or Reply all
 * mode. Two shortcuts change the ticks, and never the list.
 */
export function onlySenderTicks(form: ReplyForm, ticks: ReplyTicks): ReplyTicks {
  return { ...ticks, reply_all: lower(form.recipients.reply.to) };
}

/** Tick each person of the list. */
export function tickAllTicks(form: ReplyForm, ticks: ReplyTicks): ReplyTicks {
  const all = form.recipients.reply_all;
  return { ...ticks, reply_all: lower([...all.to, ...all.cc]) };
}

/** The people of the list that do NOT get the reply, in the server's order. */
export function leftOut(form: ReplyForm, ticks: ReplyTicks): string[] {
  const on = new Set(ticks.reply_all);
  const all = form.recipients.reply_all;
  return [...all.to, ...all.cc].filter((a) => !on.has(a.toLowerCase()));
}

/** "ananda@… will not get this.", or "" when each person is ticked. */
export function leftOutText(form: ReplyForm, ticks: ReplyTicks): string {
  const gone = leftOut(form, ticks);
  return gone.length > 0 ? `${gone.join(", ")} will not get this.` : "";
}

/**
 * The ticked start of a form, or null when the value names a person that is
 * not on the list of its choice. A form that names no start has none.
 */
function tickedOf(
  value: unknown,
  lists: { reply: ReplyRecipients; reply_all: ReplyRecipients },
): { reply: ReplyRecipients; reply_all: ReplyRecipients } | undefined | null {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "object" || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  const out = { reply: { to: [] as string[], cc: [] as string[] }, reply_all: { to: [] as string[], cc: [] as string[] } };
  for (const key of ["reply", "reply_all"] as const) {
    const raw = v[key] && typeof v[key] === "object" ? (v[key] as Record<string, unknown>) : null;
    if (!raw) return null;
    for (const part of ["to", "cc"] as const) {
      const got = addresses(raw[part] ?? []);
      if (!got) return null;
      const have = new Set(lower(lists[key][part]));
      if (got.some((a) => !have.has(a.toLowerCase()))) return null;
      out[key][part] = got;
    }
  }
  return out;
}

/** The classes of a form, or nothing for a form with none. */
function classesOf(value: unknown): { classes?: Record<string, PersonClass> } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, PersonClass> = {};
  for (const [addr, cls] of Object.entries(value as Record<string, unknown>)) {
    const c = classOf(cls);
    if (addr && c) out[addr.trim().toLowerCase()] = c;
  }
  return Object.keys(out).length > 0 ? { classes: out } : {};
}

/**
 * The class of one address of a held reply card, or undefined on a card
 * with no classes. An address that the server did not class reads as `new`,
 * so a drift marks the person rather than hiding the mark.
 */
export function classOfAddress(form: ReplyForm, address: string): PersonClass | undefined {
  if (!form.classes) return undefined;
  return form.classes[address.trim().toLowerCase()] ?? "new";
}

/**
 * The member's own Discard of the draft that the card wrote. The card calls
 * it on the member's click, beside the REJECT that ends the tool. The model
 * never calls it, so a card that times out keeps its draft. The gateway
 * trashes only this draft, and only while the member did not edit it. A
 * failure keeps the draft in Drafts, which loses nothing.
 */
export async function discardReplyDraft(
  form: ReplyForm,
  post: typeof fetch = fetch,
): Promise<void> {
  if (!form.draftId) return;
  try {
    await post("/api/email/reply-card/discard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: form.messageId, draft_id: form.draftId }),
    });
  } catch {
    // The draft stays in Drafts. Nothing was sent.
  }
}

/** The recipients the card shows, for the member's choice. */
export function recipientsFor(form: ReplyForm, replyAll: boolean): ReplyRecipients {
  return replyAll ? form.recipients.reply_all : form.recipients.reply;
}

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

/** The ticked start of each choice: the server's, or each person. */
export function startTicks(form: ReplyForm): ReplyTicks {
  const of = (key: "reply" | "reply_all") => {
    const t = form.ticked ? form.ticked[key] : form.recipients[key];
    return lower([...t.to, ...t.cc]);
  };
  return { reply: of("reply"), reply_all: of("reply_all") };
}

/** The people of the choice that are ticked, in the server's order. */
export function tickedFor(form: ReplyForm, replyAll: boolean, ticks: ReplyTicks): ReplyRecipients {
  const shown = recipientsFor(form, replyAll);
  const on = new Set(replyAll ? ticks.reply_all : ticks.reply);
  return { to: shown.to.filter((a) => on.has(a.toLowerCase())), cc: shown.cc.filter((a) => on.has(a.toLowerCase())) };
}

/** Tick or untick one person of the choice. */
export function toggleTick(ticks: ReplyTicks, replyAll: boolean, address: string): ReplyTicks {
  const key = replyAll ? "reply_all" : "reply";
  const a = address.toLowerCase();
  const list = ticks[key];
  return { ...ticks, [key]: list.includes(a) ? list.filter((x) => x !== a) : [...list, a] };
}

/** "Sends to 3 of 5": the ticked people, and the people on the card. */
export function sendsCount(form: ReplyForm, replyAll: boolean, ticks: ReplyTicks): string {
  const shown = recipientsFor(form, replyAll);
  const on = tickedFor(form, replyAll, ticks);
  return `Sends to ${on.to.length + on.cc.length} of ${shown.to.length + shown.cc.length}`;
}

/**
 * The answer of Send or Save to Drafts, or null when the body may not go or
 * no To is ticked. The keys are exactly `REPLY_ANSWER_KEYS`, and `to` and
 * `cc` are the people the member ticked, from the server's lists. With no
 * `ticks`, each person of the choice goes. The body goes as typed, with no
 * trim.
 */
export function replyAnswer(
  form: ReplyForm,
  choice: { action: ReplyAction; replyAll: boolean; body: string; ticks?: ReplyTicks },
): ReplyApproval | null {
  if (!bodyCanGo(choice.body)) return null;
  const shown = choice.ticks
    ? tickedFor(form, choice.replyAll, choice.ticks)
    : recipientsFor(form, choice.replyAll);
  if (shown.to.length === 0) return null;
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

/**
 * The summary of the card: who gets the reply now, each ticked To in full.
 * With `ticks`, only the ticked people count.
 */
export function replySummary(form: ReplyForm, replyAll: boolean, ticks?: ReplyTicks): string {
  const to = ticks ? tickedFor(form, replyAll, ticks).to : recipientsFor(form, replyAll).to;
  return to.length > 0 ? `Reply to ${to.join(", ")}` : "No one in To is ticked, so the reply cannot go";
}

/** What the buttons do, in one line under them. */
export function replyHint(form: ReplyForm): string {
  const box = form.mailbox ? ` from ${form.mailbox}` : "";
  return `Send replies now${box}. Save to Drafts keeps it in your mailbox and sends nothing. Discard drops it.`;
}
