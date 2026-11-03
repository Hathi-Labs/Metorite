/**
 * The starter pills of the fresh chat (WS-62 CF-4 and CF-4b,
 * `chat_first_surface.md` §4.6, D-CF-8). With the chat-first flag on, a fresh
 * chat of the one assistant shows up to three action pills from the member's
 * My Day feed. Only a card of the groups `now`, `needs_you` and `ready`
 * fills a slot, in the feed's own order. A fixed template for the card's
 * kind words the pill, so a pill is a short request in the member's voice.
 * The tap sends a longer message that names the item. No model writes a pill.
 *
 * This file is the one rule, and it is pure, so `starterPills.test.ts` can
 * drive every branch. `AgentChat.tsx` reads the feed through the feed's own
 * cache key (`useFeed`), hands the cards here, and draws each pill with the
 * icon of its `app` (`feedAppIcon`). With the flag off, no cards, or no
 * card that qualifies, the fixed list shows.
 *
 * Fences: `starterPills.test.ts`, and the source block "the starter pills
 * come from the feed" in `lib/shell/assistant.test.ts`.
 */

/** How many pills a fresh chat shows. */
export const STARTER_PILL_COUNT = 3;

/** The groups whose cards ask the member to act. Any other group never fills a slot. */
export const ACTION_GROUPS: readonly string[] = ["now", "needs_you", "ready"];

/** The name in a label keeps at most this many characters, cut at a word. */
const LABEL_NAME_CHARS = 28;
/** The name in a label keeps at most this many words. */
const LABEL_NAME_WORDS = 4;
/** A label is a short request: at most this many words. */
const LABEL_WORDS = 8;
/** The sent message holds at most this much of a name, cut at a word. */
const MESSAGE_NAME_CHARS = 160;

/** The part of a feed card the pills read. */
export interface StarterCard {
  app: string;
  kind: string;
  group: string;
  title: string;
}

/** One pill: the text it shows, the message a tap sends, and the app of its icon. */
export interface StarterPill {
  label: string;
  message: string;
  /** The feed app id, for the icon. Null for a fixed-list pill. */
  app: string | null;
}

export interface StarterPillsInput {
  /** The chat-first flag, for the one assistant's fresh chat. */
  chatFirst: boolean;
  /**
   * The feed cards the member holds, in the feed's order. Absent while the
   * feed loads, or after it failed with nothing cached.
   */
  cards: readonly StarterCard[] | null | undefined;
  /** The agent's fixed list, shown when the feed gives no pill. */
  fallback: readonly string[];
}

interface Template {
  /** The label with no name. */
  plain: string;
  /** The label with a short quoted name. Absent: the label has no name. */
  named?: (name: string) => string;
  /** The message a tap sends. `ref` is the quoted name. */
  message: (ref: string) => string;
  /** True when the kind has no item name: the card's title is a sentence. */
  nameless?: boolean;
}

/** One template for each known card kind. */
export const KIND_TEMPLATES: Readonly<Record<string, Template>> = {
  overdue: {
    plain: "Get my overdue task moving",
    named: (n) => `Get ${n} moving again`,
    message: (r) => `Help me get the overdue task ${r} moving. What blocks it, and what should I do first?`,
  },
  due_today: {
    plain: "Plan my task due today",
    named: (n) => `Finish ${n} today`,
    message: (r) => `Help me finish the task ${r} that is due today. What should I do first?`,
  },
  approval: {
    plain: "Review what waits for my approval",
    named: (n) => `Review ${n} for approval`,
    message: (r) => `Show me what the approval ${r} asks for, and tell me what you advise.`,
  },
  notification: {
    plain: "What is new on my tasks?",
    named: (n) => `What is new on ${n}?`,
    message: (r) => `What is new on ${r}? Summarize the latest activity.`,
  },
  needs_reply: {
    plain: "Draft a reply to the mail waiting",
    named: (n) => `Draft a reply to ${n}`,
    message: (r) => `Draft a reply to the email ${r} that waits for my answer.`,
  },
  block_soon: {
    plain: "Prepare for my next block",
    named: (n) => `Prepare for ${n}`,
    message: (r) => `Help me prepare for my next calendar block ${r}. What do I need to start?`,
  },
  block_overlap: {
    plain: "Fix my overlapping blocks",
    named: (n) => `Fix the overlap on ${n}`,
    message: (r) => `Two of my calendar blocks overlap, and ${r} is one. Help me fix my schedule.`,
  },
  day_overbooked: {
    plain: "Fit my overbooked day",
    message: () => "My day is overbooked. Help me decide what to move or drop today.",
    nameless: true,
  },
  no_plan_yet: {
    plain: "Plan my day",
    message: () => "I have no plan for today. Plan my day from my tasks and my calendar.",
    nameless: true,
  },
  waiting_quiet: {
    plain: "Draft a nudge on a quiet task",
    named: (n) => `Draft a nudge on ${n}`,
    message: (r) => `Draft a nudge for the task ${r} that I wait on. It has been quiet too long.`,
  },
  stale_next: {
    plain: "Move a stalled task forward",
    named: (n) => `Move ${n} forward`,
    message: (r) => `The next step on ${r} has not changed for a while. What should I do next?`,
  },
  unblocked: {
    plain: "Restart my unblocked task",
    named: (n) => `Restart ${n}`,
    message: (r) => `The task ${r} is unblocked. Help me restart it.`,
  },
  absent_on_due: {
    plain: "Handle a task due while I am away",
    named: (n) => `Handle ${n} while I am away`,
    message: (r) => `The task ${r} falls due while I am away. What should I do about it?`,
  },
  promise_due: {
    plain: "Keep the promise I made",
    named: (n) => `Keep my promise on ${n}`,
    message: (r) => `I promised the task ${r}. Help me finish it, or draft a note to move the date.`,
  },
  no_answer: {
    plain: "Follow up on a mail with no answer",
    named: (n) => `Follow up on ${n}`,
    message: (r) => `Draft a follow-up to the email ${r}. It got no answer.`,
  },
  crm_followup_due: {
    plain: "Draft my CRM follow-up",
    named: (n) => `Draft a follow-up for ${n}`,
    message: (r) => `Draft a follow-up for ${r} in CRM. A follow-up is due.`,
  },
  deal_quiet: {
    plain: "Revive a quiet deal",
    named: (n) => `Revive the ${n} deal`,
    message: (r) => `The deal ${r} has gone quiet. What should I do to move it forward?`,
  },
  deal_close_passed: {
    plain: "Fix a deal with a past close date",
    named: (n) => `Update the ${n} close date`,
    message: (r) => `The close date of the deal ${r} has passed. What should I do with it?`,
  },
  colleague_out: {
    plain: "Cover for a colleague who is away",
    message: () => "A colleague is away today. Who covers their work on my shared tasks?",
    nameless: true,
  },
  agent_asks: {
    plain: "Answer the question waiting for me",
    message: () => "An agent asked me a question. Show me the question.",
    nameless: true,
  },
  digest_ready: {
    plain: "Summarize my weekly CRM digest",
    message: () => "Summarize my weekly CRM digest, and tell me what needs action.",
    nameless: true,
  },
  crm_open_questions: {
    plain: "Answer my open CRM questions",
    message: () => "Show me the open questions about my CRM data, and help me answer them.",
    nameless: true,
  },
  kb_gap: {
    plain: "Fill a gap in Knowledge",
    message: (r) => `Knowledge could not answer this question: ${r}. Help me write a page that answers it.`,
  },
  doc_needs_review: {
    plain: "Review a page past its date",
    named: (n) => `Review ${n}`,
    message: (r) => `The page ${r} is past its review date. Help me review it.`,
  },
  doc_shared_with_me: {
    plain: "Summarize the page shared with me",
    named: (n) => `Summarize ${n}`,
    message: (r) => `Summarize the page ${r} that someone shared with me.`,
  },
  source_sync_failed: {
    plain: "Fix my stopped Knowledge source",
    named: (n) => `Fix my ${n} source`,
    message: (r) => `My source ${r} stopped syncing. Help me fix it.`,
  },
  reminder_due: {
    plain: "Act on my reminder",
    named: (n) => `Act on my ${n} reminder`,
    message: (r) => `My reminder ${r} is due. Help me act on it.`,
  },
  reminder_check: {
    plain: "Check back on my reminder",
    named: (n) => `Check back on ${n}`,
    message: (r) => `It is time to check back on ${r}. What is the state, and what should I do?`,
  },
  reminder_unconfirmed: {
    plain: "Confirm my reminder",
    named: (n) => `Confirm ${n}`,
    message: (r) => `My reminder ${r} has no confirmation. Help me confirm it or move it.`,
  },
};

/** One template for each feed app, for a kind the table above does not know. */
export const APP_TEMPLATES: Readonly<Record<string, Template>> = {
  tasks: { plain: "Help me with a task", message: (r) => `Help me with the task ${r}. What should I do first?` },
  approvals: { plain: "Review an approval for me", message: (r) => `Show me the approval ${r}, and tell me what you advise.` },
  projects: { plain: "What needs me in Projects?", message: (r) => `What needs me on ${r} in Projects?` },
  email: { plain: "Help me with a mail", message: (r) => `Help me with the email ${r}. What should I do?` },
  calendar: { plain: "Help me with my calendar", message: (r) => `Help me with ${r} on my calendar.` },
  crm: { plain: "What needs me in CRM?", message: (r) => `What needs me on ${r} in CRM?` },
  people: { plain: "What needs me with my team?", message: (r) => `What needs me about ${r} with my team?` },
  chat: { plain: "Show me what waits in chat", message: (r) => `Show me what waits for me in ${r}.` },
  knowledge: { plain: "What needs me in Knowledge?", message: (r) => `What needs me on ${r} in Knowledge?` },
};

/** A name in one line: whitespace folded, double quotes made single. */
function fold(text: string): string {
  return text.replace(/\s+/g, " ").replace(/"/g, "'").trim();
}

/** A long name cut at a word, with no ellipsis. Used for the message only. */
function cutAtWord(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return (space >= Math.floor(max / 2) ? cut.slice(0, space) : cut).replace(/[\s,;:.!?]+$/, "");
}

/**
 * The short name a label holds, or null when nothing usable is left. A "Re:",
 * "Fwd:" or "FW:" prefix goes. The part before the first " — ", " - ", " | "
 * or "(" stands for the name when it has two words or more. The rest is cut at
 * a word, to `words` words and `LABEL_NAME_CHARS` characters, with no ellipsis.
 */
export function shortName(name: string, words = LABEL_NAME_WORDS): string | null {
  let text = name.replace(/[“”"]/g, "");
  let before: string;
  do {
    before = text;
    text = text.replace(/^\s*(?:re|fwd?)\s*:\s*/i, "");
  } while (text !== before);
  const head = text.split(/\s[—–|-]\s|\(/)[0].trim();
  if (head.split(/\s+/).filter(Boolean).length >= 2) text = head;
  else text = text.replace(/\s[—–|-]\s/g, " ").replace(/[()]/g, "");
  const kept: string[] = [];
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (kept.length >= words || [...kept, word].join(" ").length > LABEL_NAME_CHARS) break;
    kept.push(word);
  }
  const out = kept.join(" ").replace(/[\s,;:.!?…-]+$/, "");
  return out || null;
}

/** The label of a template: with the short name when it fits the word limit, else plain. */
function labelOf(tpl: Template, name: string): string {
  if (!tpl.named) return tpl.plain;
  for (let words = LABEL_NAME_WORDS; words >= 1; words -= 1) {
    const short = shortName(name, words);
    if (!short) break;
    const label = tpl.named(`“${short}”`);
    if (label.split(/\s+/).length <= LABEL_WORDS) return label;
  }
  return tpl.plain;
}

/** The pill of one card, or null when no template fits it. */
export function pillOfCard(card: StarterCard): StarterPill | null {
  if (!card || typeof card.group !== "string" || !ACTION_GROUPS.includes(card.group)) return null;
  const tpl = Object.hasOwn(KIND_TEMPLATES, card.kind)
    ? KIND_TEMPLATES[card.kind]
    : Object.hasOwn(APP_TEMPLATES, card.app)
      ? APP_TEMPLATES[card.app]
      : null;
  if (!tpl) return null;
  const name = fold(typeof card.title === "string" ? card.title : "");
  if (!tpl.nameless && !name) return null;
  const label = tpl.nameless ? tpl.plain : labelOf(tpl, name);
  const message = tpl.message(tpl.nameless ? "" : `"${cutAtWord(name, MESSAGE_NAME_CHARS)}"`);
  return { label, message, app: typeof card.app === "string" ? card.app : null };
}

/**
 * The pills a fresh chat shows.
 *
 *   - The flag off: the fixed list, whatever the feed holds.
 *   - No cards yet, or none at all: the fixed list.
 *   - Else the first `STARTER_PILL_COUNT` cards of the action groups, in the
 *     feed's order, each as a pill (`pillOfCard`), with a repeated label
 *     dropped. Fewer cards give fewer pills. None left gives the fixed list.
 */
export function starterPills({ chatFirst, cards, fallback }: StarterPillsInput): StarterPill[] {
  const fixed = (): StarterPill[] => fallback.map((s) => ({ label: s, message: s, app: null }));
  if (!chatFirst || !cards || cards.length === 0) return fixed();
  const out: StarterPill[] = [];
  const seen = new Set<string>();
  for (const card of cards) {
    if (out.length >= STARTER_PILL_COUNT) break;
    const pill = pillOfCard(card);
    if (!pill) continue;
    const key = pill.label.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(pill);
  }
  return out.length > 0 ? out : fixed();
}
