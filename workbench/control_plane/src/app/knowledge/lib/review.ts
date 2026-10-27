// ── The Review view of the assistant's proposals (WS-61 KB-5b) ──────────────
//
// Pure. The words of the buttons, the groups of the list, the line that says
// who proposed, and the reading of a refused decision. `knowledge_base.md`
// §5.3, §8.1 (F1, F2) and §11.
//
// The rule for the rail entry: Review shows when at least one proposal waits
// for the member, with the count, and while the member is on the view itself.
// A member with none sees no entry and no stub.

import { personDisplayName } from "@/lib/personName";

import { KnowledgeError } from "./api";

export type ProposalKind = "page" | "edit";

/** One row of `GET /knowledge/proposals`. */
export interface Proposal {
  /** The id of an edit proposal. For a page proposal, the page id. */
  id: string;
  kind: ProposalKind;
  page_id: string;
  /** The space of the page. `null` from an older gateway. */
  space_id?: string | null;
  page_title: string | null;
  base_version: number | null;
  title: string;
  body_md: string;
  summary: string;
  proposer_email: string;
  via: string;
  status: string;
  created_at: string | null;
}

/** The cache key of the pending list. Under the app's prefix, so a write re-reads it. */
export const PROPOSALS_KEY = "knowledge/proposals?status=pending";

export function kindLabel(kind: ProposalKind): string {
  return kind === "page" ? "New page" : "Edit";
}

/** The accept button: a page publishes, and an edit applies. */
export function acceptLabel(kind: ProposalKind): string {
  return kind === "page" ? "Publish" : "Apply edit";
}

/** The toast after an accept. */
export function acceptedToast(p: Pick<Proposal, "kind" | "title">): string {
  return p.kind === "page" ? `Published ${p.title}` : `Applied the edit to ${p.title}`;
}

export function rejectedToast(p: Pick<Proposal, "kind" | "title">): string {
  return p.kind === "page" ? `Rejected the page ${p.title}` : `Rejected the edit to ${p.title}`;
}

/** The icon of a kind. */
export function kindIcon(kind: ProposalKind): string {
  return kind === "page" ? "FilePlus" : "FilePen";
}

/** "Proposed by the assistant for Asha". An outside agent says so. */
export function proposerLine(p: Pick<Proposal, "via" | "proposer_email">): string {
  const who = personDisplayName(null, p.proposer_email);
  if (p.via === "chat") return `Proposed by the assistant for ${who}`;
  if (p.via === "mcp") return `Proposed by an outside agent for ${who}`;
  return `Proposed by ${who}`;
}

/** The title a member sees: an edit may rename the page. */
export function shownTitle(p: Pick<Proposal, "title" | "page_title">): string {
  return p.title || p.page_title || "(untitled)";
}

export interface ProposalGroup {
  kind: ProposalKind;
  label: string;
  items: Proposal[];
}

/** The list in two groups, new pages first, each oldest first. An empty group is left out. */
export function groupProposals(list: readonly Proposal[]): ProposalGroup[] {
  const byAge = (a: Proposal, b: Proposal) => (a.created_at ?? "").localeCompare(b.created_at ?? "");
  const groups: ProposalGroup[] = [
    { kind: "page", label: "New pages", items: list.filter((p) => p.kind === "page").sort(byAge) },
    { kind: "edit", label: "Edits", items: list.filter((p) => p.kind === "edit").sort(byAge) },
  ];
  return groups.filter((g) => g.items.length > 0);
}

/** The proposals in the order that the member reads them: the groups, flat. */
export function readingOrder(list: readonly Proposal[]): Proposal[] {
  return groupProposals(list).flatMap((g) => g.items);
}

/** The proposal to open after `id` leaves the list: the next, else the one before, else none. */
export function nextAfter(list: readonly Proposal[], id: string): Proposal | null {
  const order = readingOrder(list);
  const at = order.findIndex((p) => p.id === id);
  if (at < 0) return order[0] ?? null;
  return order[at + 1] ?? order[at - 1] ?? null;
}

/** The pending edit proposal for a page, if any (the notice on a published page). */
export function pendingEditFor(list: readonly Proposal[] | undefined, pageId: string): Proposal | null {
  return list?.find((p) => p.kind === "edit" && p.page_id === pageId) ?? null;
}

/** The proposal that a draft page stands for, if it is pending. Its id is the page id. */
export function pendingPageFor(list: readonly Proposal[] | undefined, pageId: string): Proposal | null {
  return list?.find((p) => p.kind === "page" && p.page_id === pageId) ?? null;
}

/**
 * The kind of a page's `origin`. The gateway sends a JSON object, and the
 * client type says `string | null`, so this takes both and anything else.
 */
export function originKind(origin: unknown): string | null {
  let value = origin;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (value && typeof value === "object" && "kind" in value) {
    const kind = (value as { kind: unknown }).kind;
    return typeof kind === "string" ? kind : null;
  }
  return null;
}

/** True for a draft that the assistant proposed. A published page shows no badge. */
export function isAgentDraft(page: { state: string; origin: unknown }): boolean {
  return page.state === "draft" && originKind(page.origin) === "agent_proposal";
}

/** What a refused decision tells the view. */
export interface DecisionFailure {
  /** The server's words, or ours when it sent none. */
  detail: string;
  /** True for a 409: the page moved, or the proposal is already decided. */
  conflict: boolean;
  /** True for a 404: the proposal is not there for this member any more. */
  gone: boolean;
}

export function decisionFailure(err: unknown): DecisionFailure {
  if (err instanceof KnowledgeError) {
    return { detail: err.message, conflict: err.status === 409, gone: err.status === 404 };
  }
  return { detail: err instanceof Error ? err.message : String(err), conflict: false, gone: false };
}

/** True when a 409 says the proposal is already decided. Reject cannot help then. */
export function alreadyDecided(detail: string): boolean {
  return /\bdecided\b|\b(accepted|rejected|expired)\b/i.test(detail);
}
