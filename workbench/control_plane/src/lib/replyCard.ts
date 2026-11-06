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

import { DRAFT_LEFT_EVENT } from "@/lib/draftLeftEvent";
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
  /**
   * §21.15: the Bcc that the server recorded for the card. The card shows it
   * apart, as "Bcc (hidden from the others)". A card with none has no key.
   */
  bcc?: string[];
}

export interface ReplyForm {
  /**
   * `"new"` on the card of a NEW mail (§18.10, owner 2026-11-03). It answers
   * no email, so `messageId` is empty, the subject is editable, and the files
   * are fixed lines. A reply form has no kind.
   */
  kind?: "new";
  /** The files of a new mail, each as one fixed line ("File: \"a.pdf\" (4 KB)"). */
  files?: string[];
  /**
   * The file rows of a held new mail (fix round 1, §21.1 item 3): each with
   * its tick and its hint. The member can untick one, and the answer names
   * the ids that stay ticked. A form with none has fixed `files` lines.
   */
  fileRows?: NewMailFileRow[];
  /**
   * The longest text that the act of a new-mail card takes (fix round 1,
   * P3): 3,000 for a held card (`HELD_TEXT_MAX`), else `MAX_REPLY_BODY`.
   */
  maxBody?: number;
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
   * Task 1526, item 11: the draft write had an unknown outcome. A draft can
   * exist that the server cannot name, so the card cannot send, and it says so.
   */
  draftUnsaved?: boolean;
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
  /**
   * The `request_id` of the card (§21.15). A Bcc that the member types goes
   * to the record of THIS card, so the act can take it.
   */
  requestId?: string;
  /**
   * §18.11, a new mail only: the card that this card replaces, as the
   * gateway linked it. The card takes that card's slot, in its tab.
   */
  revises?: string;
  /** §18.11, a new mail only: the draft number, 2 or more ("Draft 2"). */
  draft?: number;
  /**
   * §18.11 fix round 5, a new mail only: the SAME card, shown again because
   * the revise of a chat message did not run. It takes its slot back, with
   * the member's edits, and says why.
   */
  reopened?: true;
  /**
   * §18.11 rule 7, a new mail only: the people that start unticked because
   * the member unticked them on the draft before, by lower case. The gateway
   * starts them unticked (`ticked`) and names them, on every path: a slot, no
   * slot, a remount and a replay. The card says so, and one tick brings each
   * person back. A name that is not a person of the card, or that starts
   * ticked, is dropped.
   */
  unticked?: string[];
}

/** The kept unticks of a new-mail form (§18.11 rule 7): each a person of the
 *  card who starts unticked, by lower case, once. Anything else is dropped. */
function untickedNames(value: unknown, all: ReplyRecipients, ticked: ReplyForm["ticked"] | undefined): { unticked?: string[] } {
  if (!Array.isArray(value) || !ticked) return {};
  const have = new Set(lower([...all.to, ...all.cc, ...(all.bcc ?? [])]));
  const start = ticked.reply_all;
  const on = new Set(lower([...start.to, ...start.cc, ...(start.bcc ?? [])]));
  const out: string[] = [];
  for (const v of value) {
    if (typeof v !== "string") continue;
    const a = v.trim().toLowerCase();
    if (have.has(a) && !on.has(a) && !out.includes(a)) out.push(a);
  }
  return out.length ? { unticked: out } : {};
}

/** The link of a redraft (§18.11): a card id that the gateway minted, and a
 *  draft number from 2 to 99. Anything else is no link. */
function linkOf(value: Record<string, unknown>): { revises?: string; draft?: number } {
  const { revises, draft } = value;
  if (typeof revises !== "string" || !/^[0-9a-f]{32}$/.test(revises)) return {};
  if (typeof draft !== "number" || !Number.isInteger(draft) || draft < 2 || draft > 99) return {};
  return { revises, draft };
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
  const bcc = addresses(r.bcc ?? []);
  if (!to || !cc || !bcc || to.length === 0) return null;
  return bcc.length > 0 ? { to, cc, bcc } : { to, cc };
}

/**
 * The reply form of a `confirmation_requested` value, or null when it has
 * none, or when the card cannot read it. A form the card cannot read is no
 * form: the card then has nothing to send, and it says so.
 */
export function replyFromEvent(value: unknown, requestId?: string): ReplyForm | null {
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
  const isNew = v.kind === "new";
  const messageId = typeof v.message_id === "string" ? v.message_id.trim() : "";
  // A reply names the email it answers. A new mail (§18.10) answers none.
  if (!messageId && !isNew) return null;
  const notices = Array.isArray(v.notices) ? v.notices.filter((n): n is string => typeof n === "string" && !!n.trim()) : [];
  const files = Array.isArray(v.files) ? v.files.filter((f): f is string => typeof f === "string" && !!f.trim()) : [];
  const fileRows = isNew ? fileRowsOf(v.file_rows) : undefined;
  // A file row the card cannot read is a form the card cannot read.
  if (fileRows === null) return null;
  return {
    ...(isNew ? { kind: "new" as const, files } : {}),
    ...(fileRows && fileRows.length > 0 ? { fileRows } : {}),
    ...(isNew ? { maxBody: maxBodyOf(v.max_body) } : {}),
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
    // A new mail's note holds whole warning lines: it is never cut (fix round 1, P3).
    note: isNew ? text(v.note, MAX_NEW_MAIL_NOTE) : text(v.note),
    draftId: text(v.draft_id, 64),
    ...(v.draft_unsaved === true ? { draftUnsaved: true } : {}),
    ...classesOf(v.classes),
    ...(ticked ? { ticked } : {}),
    ...marksOf(v.marks, replyAll),
    ...(requestId ? { requestId } : {}),
    ...(isNew ? linkOf(v) : {}),
    ...(isNew && v.reopened === true ? { reopened: true as const } : {}),
    ...(isNew ? untickedNames(v.unticked, replyAll, ticked ?? undefined) : {}),
  };
}

/** The words of each person, only for a person of the card's list. */
function marksOf(value: unknown, all: ReplyRecipients): { marks?: Record<string, string> } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const have = new Set(lower([...all.to, ...all.cc, ...(all.bcc ?? [])]));
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
  return { ...ticks, reply_all: [...lower(form.recipients.reply.to), ...bccTicked(form, ticks)] };
}

/** The Bcc people that are ticked now. A shortcut keeps them as they are. */
function bccTicked(form: ReplyForm, ticks: ReplyTicks): string[] {
  const on = new Set(ticks.reply_all);
  return lower(form.recipients.reply_all.bcc ?? []).filter((a) => on.has(a));
}

/** Tick each person of the list. */
export function tickAllTicks(form: ReplyForm, ticks: ReplyTicks): ReplyTicks {
  const all = form.recipients.reply_all;
  return { ...ticks, reply_all: [...lower([...all.to, ...all.cc]), ...bccTicked(form, ticks)] };
}

/**
 * The Reply all switch (owner amendment, 2026-10-24): tick each person of
 * the list, except a person that the server classed `new` (a look-alike
 * included), which stays unticked until the member ticks it. The switch only
 * changes ticks: the answer is always the ticked people of the one list.
 */
export function replyAllTicks(form: ReplyForm, ticks: ReplyTicks): ReplyTicks {
  const all = form.recipients.reply_all;
  const keep = [...all.to, ...all.cc].filter((a) => classOfAddress(form, a) !== "new");
  return { ...ticks, reply_all: [...lower(keep), ...bccTicked(form, ticks)] };
}

/** The people of the list that do NOT get the reply, in the server's order. */
export function leftOut(form: ReplyForm, ticks: ReplyTicks): string[] {
  const on = new Set(ticks.reply_all);
  const all = form.recipients.reply_all;
  return [...all.to, ...all.cc, ...(all.bcc ?? [])].filter((a) => !on.has(a.toLowerCase()));
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
  const out: { reply: ReplyRecipients; reply_all: ReplyRecipients } = {
    reply: { to: [], cc: [] },
    reply_all: { to: [], cc: [] },
  };
  for (const key of ["reply", "reply_all"] as const) {
    const raw = v[key] && typeof v[key] === "object" ? (v[key] as Record<string, unknown>) : null;
    if (!raw) return null;
    for (const part of ["to", "cc", "bcc"] as const) {
      const list = lists[key][part];
      if (part === "bcc" && !list) continue;
      const got = addresses(raw[part] ?? []);
      if (!got) return null;
      const have = new Set(lower(list ?? []));
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
    return lower([...t.to, ...t.cc, ...(t.bcc ?? [])]);
  };
  return { reply: of("reply"), reply_all: of("reply_all") };
}

/** The people of the choice that are ticked, in the server's order. */
export function tickedFor(form: ReplyForm, replyAll: boolean, ticks: ReplyTicks): ReplyRecipients {
  const shown = recipientsFor(form, replyAll);
  const on = new Set(replyAll ? ticks.reply_all : ticks.reply);
  const pick = (list: string[]) => list.filter((a) => on.has(a.toLowerCase()));
  return { to: pick(shown.to), cc: pick(shown.cc), ...(shown.bcc ? { bcc: pick(shown.bcc) } : {}) };
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
  const got = on.to.length + on.cc.length + (on.bcc ?? []).length;
  return `Sends to ${got} of ${shown.to.length + shown.cc.length + (shown.bcc ?? []).length}`;
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
    // §21.15: the ticked Bcc, a subset of the server's record of this card.
    bcc: shown.bcc ?? [],
    message_id: form.messageId,
  };
  return `${REPLY_ANSWER_PREFIX}${JSON.stringify({ reply })}` as ReplyApproval;
}

/** The label of the Bcc row (§21.15). */
export const BCC_LABEL = "Bcc (hidden from the others)";

/**
 * The form with one more Bcc, which the server took for this card
 * (`POST /email/reply-card/bcc`). Both choices get it, with its mark.
 */
export function withBcc(form: ReplyForm, address: string, mark: string, cls?: PersonClass): ReplyForm {
  const a = address.trim();
  const have = lower([...(form.recipients.reply_all.bcc ?? [])]);
  if (!a || have.includes(a.toLowerCase())) return form;
  const add = (r: ReplyRecipients): ReplyRecipients => ({ ...r, bcc: [...(r.bcc ?? []), a] });
  return {
    ...form,
    recipients: { reply: add(form.recipients.reply), reply_all: add(form.recipients.reply_all) },
    ...(mark ? { marks: { ...(form.marks ?? {}), [a.toLowerCase()]: mark } } : {}),
    ...(form.classes && cls ? { classes: { ...form.classes, [a.toLowerCase()]: cls } } : {}),
  };
}

/**
 * The member's own Bcc: the server classes it and adds it to the record of
 * THIS card, or refuses it. The answer is the address, its class and its
 * mark, or the server's words for a refusal.
 */
export async function addBcc(
  form: ReplyForm,
  address: string,
  post: typeof fetch = fetch,
): Promise<{ ok: true; address: string; cls?: PersonClass; mark: string } | { ok: false; error: string }> {
  if (!form.requestId) return { ok: false, error: "This card cannot take a Bcc. Ask for the reply again." };
  try {
    const res = await post("/api/email/reply-card/bcc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: form.messageId, card_request_id: form.requestId, address }),
    });
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "The Bcc was not added.";
      return { ok: false, error: detail.slice(0, 300) };
    }
    return {
      ok: true,
      address: text(data.address, 320),
      cls: classOf(data.class),
      mark: text(data.mark, 200),
    };
  } catch {
    return { ok: false, error: "The Bcc was not added. Try again." };
  }
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

/**
 * What the buttons do, in one line under them. A card that wrote a draft
 * (`draftId`) says that the reply is already in Drafts (owner report,
 * 2026-11-03): the old words told the member that only Save puts it there.
 * Discard trashes only a draft that nobody changed (`trash_chat_draft`), so
 * the words say so (fix round 1, item 8).
 */
export function replyHint(form: ReplyForm): string {
  if (form.draftId) {
    const where = form.mailbox ? `the Drafts of ${form.mailbox}` : "your Drafts";
    return `This reply is in ${where}. Send sends it now. Save keeps your edits in Drafts. Discard deletes it from Drafts if you did not change it there.`;
  }
  const box = form.mailbox ? ` from ${form.mailbox}` : "";
  return `Send replies now${box}. Save to Drafts keeps it in your mailbox and sends nothing. Discard drops it.`;
}

/** The label of the Save button: "Save draft" when the reply is already in Drafts. */
export function saveLabel(form: ReplyForm): string {
  return form.draftId ? "Save draft" : "Save to Drafts";
}

// ── A reply that left Drafts (owner report, 2026-11-03, task 1526) ─────────
//
// The card writes its reply to Drafts at once. The member can send or delete
// that draft in the Email app while the card waits. The card then reads that
// state, shows one line, and stops its Send and Save. The gateway refuses
// both too (`reply_card_act`, a 409 `draft_gone`), so the card is a courtesy
// and the server is the guard.

/**
 * The line of a card whose draft left Drafts. It claims no send (fix round
 * 1, item 3): a rule can also replace an unedited chat draft when new mail
 * comes in (`_resolve_existing_thread_draft`).
 */
export const DRAFT_LEFT_LINE =
  "This reply is no longer in Drafts. It was sent, deleted or replaced outside this card, so this card cannot send it.";

/**
 * The line of a card whose draft write had an unknown outcome (item 11). The
 * gateway sends the same words as a notice (`DRAFT_UNSAVED`).
 */
export const DRAFT_UNSAVED_LINE =
  "This reply could not be saved to Drafts, so this card cannot send it. Ask me to draft it again.";

/** How often a card that shows reads the state of its draft. */
export const DRAFT_POLL_MS = 60_000;

/** The state of a card's draft. `unknown` keeps the card as it is. */
export type DraftState = "in" | "gone" | "unknown";

/**
 * Is the card's draft still in Drafts? One read of the local row, through
 * the email BFF (`GET /email/reply-card/draft-state`). Only a clear `false`
 * of the server is `gone`. A fault, a 404 and an odd answer are `unknown`.
 */
export async function readDraftState(draftId: string, get: typeof fetch = fetch): Promise<DraftState> {
  if (!draftId) return "unknown";
  try {
    const res = await get(`/api/email/reply-card/draft-state?draft_id=${encodeURIComponent(draftId)}`, {
      cache: "no-store",
    });
    if (!res.ok) return "unknown";
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (data.in_drafts === false) return "gone";
    if (data.in_drafts === true) return "in";
    return "unknown";
  } catch {
    return "unknown";
  }
}

/** The window and the document that the watch listens on. A test passes fakes. */
export interface DraftWatchHost {
  win: Pick<EventTarget, "addEventListener" | "removeEventListener">;
  doc: Pick<EventTarget, "addEventListener" | "removeEventListener"> & { visibilityState: string };
}

/**
 * Watch the card's draft while the card shows. It reads the state once at
 * mount, when the window gets the focus, when the page shows again, every
 * `DRAFT_POLL_MS` while the page shows, and at once on `DRAFT_LEFT_EVENT`.
 * A hidden page reads nothing. On `gone` it calls `onGone` once and stops.
 * A read that comes while one runs makes one more read after it, so a send
 * during a read is not lost. Returns the stop function. A card with no
 * draft, or a render with no window, watches nothing.
 */
export function watchCardDraft(
  draftId: string,
  onGone: () => void,
  opts: { read?: (id: string) => Promise<DraftState>; host?: DraftWatchHost; everyMs?: number } = {},
): () => void {
  const found: DraftWatchHost | null =
    opts.host ?? (typeof window !== "undefined" && typeof document !== "undefined" ? { win: window, doc: document } : null);
  if (!draftId || !found) return () => {};
  const host: DraftWatchHost = found;
  const read = opts.read ?? ((id: string) => readDraftState(id));
  let live = true;
  let busy = false;
  let again = false;
  const check = async () => {
    if (!live || host.doc.visibilityState === "hidden") return;
    if (busy) {
      again = true;
      return;
    }
    busy = true;
    try {
      do {
        again = false;
        const state = await read(draftId);
        if (!live) return;
        if (state === "gone") {
          stop();
          onGone();
          return;
        }
      } while (again && live);
    } finally {
      busy = false;
    }
  };
  const onEvent = () => void check();
  const onShow = () => {
    if (host.doc.visibilityState !== "hidden") void check();
  };
  host.win.addEventListener("focus", onEvent);
  host.win.addEventListener(DRAFT_LEFT_EVENT, onEvent);
  host.doc.addEventListener("visibilitychange", onShow);
  const timer = setInterval(onEvent, opts.everyMs ?? DRAFT_POLL_MS);
  // Fix round 1, item 5: one read at mount. A card that mounts again after a
  // visit to /email can hold a draft that left while the card was away.
  void check();
  function stop() {
    if (!live) return;
    live = false;
    clearInterval(timer);
    host.win.removeEventListener("focus", onEvent);
    host.win.removeEventListener(DRAFT_LEFT_EVENT, onEvent);
    host.doc.removeEventListener("visibilitychange", onShow);
  }
  return stop;
}

// ── The one email card of a NEW mail (§18.10, owner 2026-11-03) ─────────────
//
// Every email that the assistant writes uses ONE card. For a new mail the
// member ticks the people, edits the subject and the text, and presses Send,
// Save draft or Discard. The gateway took the people and the files from its
// own record of the card, so a tick can take a person off and never add one.
// A new person is added by asking in the chat, never on the card (owner).

/** The longest subject the server takes (`reply_card.MAX_SUBJECT`). */
export const MAX_SUBJECT = 200;

/** The nudge line under the text: the member can also ask the chat. */
export const NUDGE_LEAD = "Want it changed?";
export const NUDGE_TEXT = "Tell me in the chat below.";
/**
 * Two examples of what to ask. A click puts the words in the chat box and
 * sends nothing: the member reads them, and presses Send in the chat.
 */
export const NUDGE_CHIPS: readonly string[] = ["make it shorter", "make it more formal"];

/**
 * The nudge line once the member changed the subject or the text. A request
 * in the chat closes this card, and since §18.11 it carries the member's
 * version (`lib/redraftCard.ts`), so the assistant writes the new card from
 * the member's edits. The line says so, and the chips stay. (Fix round 1 of
 * §18.10 hid the chips, because the edits did not go then.)
 */
export const NUDGE_EDITED = "Your edits go with a request in the chat. Save draft keeps your version.";

/**
 * The same line when no run is live, as after a Stop (§18.11 fix round 4). Only
 * a steer carries the member's version. A plain send starts a new run and
 * carries none, so a new card starts from the assistant's words, and a person
 * the member unticked starts ticked. The card shows no chips in this state,
 * because a chip would lead the member to lose the edits.
 */
export const NUDGE_EDITED_AT_REST = "A request in the chat starts again from the assistant's draft. Save draft keeps your version.";

/** The member's edits of a new-mail form. The queue keeps them by card key. */
export interface NewMailEdits {
  subject: string;
  body: string;
  ticks: ReplyTicks;
  fileTicks: string[];
}

/** True when the people the member left ticked differ from the card's start. */
export function newMailTicksMoved(form: ReplyForm, ticks: ReplyTicks): boolean {
  const start = startTicks(form).reply_all;
  return start.length !== ticks.reply_all.length || start.some((a) => !ticks.reply_all.includes(a));
}

/** True when the member changed the subject or the text of the card. */
export function newMailEdited(form: ReplyForm, subject: string, body: string): boolean {
  return subject !== form.subject || body !== form.body;
}

/** True for the card of a new mail. */
export function isNewMail(form: ReplyForm | null | undefined): boolean {
  return form?.kind === "new";
}

/** A line break of any kind in a subject: CR, LF, U+2028 and U+2029. */
export const SUBJECT_BREAK = /[\r\n\u2028\u2029]/;

/** The subject may go: one line, and it fits. An empty subject may go. */
export function subjectCanGo(subject: string): boolean {
  return subject.length <= MAX_SUBJECT && !SUBJECT_BREAK.test(subject);
}

/** "Email to kim@…": each ticked To, in full. */
export function newMailSummary(form: ReplyForm, ticks: ReplyTicks): string {
  const to = tickedFor(form, true, ticks).to;
  return to.length > 0 ? `Email to ${to.join(", ")}` : "No one in To is ticked, so the email cannot go";
}

/**
 * What the buttons of a new-mail card do, in one line under them. The send
 * adds the mailbox signature after the card (`transport/send.py`), so the
 * line says so, as the reply card's notice does (review of #1191, P1d).
 */
/** The longest note of a new-mail form (`ask_tools._NEW_MAIL_NOTE_MAX`). */
export const MAX_NEW_MAIL_NOTE = 40_000;

/** The text limit of a form value: a whole number up to `MAX_REPLY_BODY`. */
function maxBodyOf(v: unknown): number {
  return typeof v === "number" && Number.isInteger(v) && v > 0 && v <= MAX_REPLY_BODY ? v : MAX_REPLY_BODY;
}

/** The longest text that this card's act takes. */
export function newMailMaxBody(form: ReplyForm): number {
  return form.maxBody ?? MAX_REPLY_BODY;
}

/** The text may go: it holds text, and it fits the act of THIS card. */
export function newMailBodyCanGo(form: ReplyForm, body: string): boolean {
  return bodyCanGo(body) && body.length <= newMailMaxBody(form);
}

/**
 * The limit line under the text box, shown BEFORE Send (fix round 1, P3), or
 * "" when the card takes the full limit. A held card takes 3,000 characters.
 */
export function newMailLimitLine(form: ReplyForm, body: string): string {
  const max = newMailMaxBody(form);
  if (max >= MAX_REPLY_BODY) return "";
  const n = (x: number) => x.toLocaleString("en-US");
  return `This email can hold ${n(max)} characters of text. It holds ${n(body.length)} now.`;
}

export function newMailHint(form: ReplyForm): string {
  const box = form.mailbox ? ` from ${form.mailbox}` : "";
  return `Nothing goes out until you press Send${box}. Your mailbox signature, if you set one, goes below the text. Save draft keeps it in your Drafts and sends nothing. Discard drops it.`;
}

export type NewMailApproval = ReplyApproval;

/** One file row of a held new mail: its id, its words and its start tick. */
export interface NewMailFileRow {
  id: string;
  label: string;
  hint: string;
  checked: boolean;
}

/** The file rows of a form value, `undefined` for none, or null when one row
 *  is not a row the card can draw. */
function fileRowsOf(value: unknown): NewMailFileRow[] | undefined | null {
  if (value === undefined || value === null) return undefined;
  if (!Array.isArray(value)) return null;
  const out: NewMailFileRow[] = [];
  const seen = new Set<string>();
  for (const r of value) {
    if (!r || typeof r !== "object") return null;
    const row = r as Record<string, unknown>;
    const id = typeof row.id === "string" ? row.id.trim() : "";
    if (!id || id.length > 64 || seen.has(id) || typeof row.label !== "string" || !row.label.trim()) return null;
    seen.add(id);
    out.push({ id, label: row.label, hint: typeof row.hint === "string" ? row.hint : "", checked: row.checked !== false });
  }
  return out;
}

/** The ids of the file rows that start ticked. */
export function startFileTicks(form: ReplyForm): string[] {
  return (form.fileRows ?? []).filter((r) => r.checked).map((r) => r.id);
}

/** Tick or untick one file row. */
export function toggleFileTick(ticks: string[], id: string): string[] {
  return ticks.includes(id) ? ticks.filter((x) => x !== id) : [...ticks, id];
}

/**
 * The answer of Send or Save draft on a new-mail card, or null when the
 * text, the subject or the ticks may not go. The keys are exactly
 * `NEW_MAIL_ANSWER_KEYS` of `acb_skills/reply_card.py`, and a test reads
 * this object to hold the two in step. `to`, `cc` and `bcc` are the people
 * the member left ticked, from the server's lists. The text goes as typed.
 * A form with file rows adds `files`, the ids that stay ticked, in the
 * card's order (`NEW_MAIL_ANSWER_OPTIONAL_KEYS`). A form with none adds no key.
 */
export function newMailAnswer(
  form: ReplyForm,
  choice: { action: ReplyAction; subject: string; body: string; ticks: ReplyTicks; fileTicks?: string[] },
): NewMailApproval | null {
  if (!isNewMail(form) || !newMailBodyCanGo(form, choice.body) || !subjectCanGo(choice.subject)) return null;
  const picked = tickedFor(form, true, choice.ticks);
  if (picked.to.length === 0) return null;
  const on = new Set(choice.fileTicks ?? startFileTicks(form));
  const files = (form.fileRows ?? []).filter((r) => on.has(r.id)).map((r) => r.id);
  const mail = {
    kind: "new",
    action: choice.action,
    subject: choice.subject,
    body: choice.body,
    to: picked.to,
    cc: picked.cc,
    bcc: picked.bcc ?? [],
    ...(form.fileRows ? { files } : {}),
  };
  return `${REPLY_ANSWER_PREFIX}${JSON.stringify({ reply: mail })}` as NewMailApproval;
}
