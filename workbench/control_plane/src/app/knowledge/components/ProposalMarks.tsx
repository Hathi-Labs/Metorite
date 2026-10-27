"use client";

/**
 * The marks that the assistant's proposals leave on a page (WS-61 KB-5b,
 * `knowledge_base.md` §11.1).
 *
 * `AgentDraftBadge` goes beside the state badge of a draft that the assistant
 * proposed, with a Review link. `PendingEditNotice` is a slim notice on a
 * published page that has a pending edit proposal. Both read the one cached
 * list that the rail reads (`useProposals`), so they agree with it.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";

import { reviewHref } from "../lib/links";
import { isAgentDraft, pendingEditFor } from "../lib/review";
import { useKnowledge } from "./KnowledgeContext";
import { useProposals } from "./useProposals";

function useReviewLink(): (href: string) => (e: React.MouseEvent) => void {
  const router = useRouter();
  const { requestLeave } = useKnowledge();
  return (href) => (e) => {
    e.preventDefault();
    requestLeave(() => router.push(href));
  };
}

/** "Proposed by the assistant" and a Review link, for a draft with that origin. */
export function AgentDraftBadge({ page }: { page: { id: string; state: string; origin: unknown } }) {
  const go = useReviewLink();
  if (!isAgentDraft(page)) return null;
  const href = reviewHref(page.id);
  return (
    <span className="inline-flex items-center gap-1.5" data-agent-draft="">
      <Badge tone="primary" icon="Bot">
        Proposed by the assistant
      </Badge>
      <Link href={href} onClick={go(href)} className="text-xs text-primary tech-transition hover:underline">
        Review
      </Link>
    </span>
  );
}

/** A slim notice on a published page that has a pending edit proposal. */
export function PendingEditNotice({ pageId, state }: { pageId: string; state: string }) {
  const read = useProposals();
  const go = useReviewLink();
  const pending = state === "published" ? pendingEditFor(read.data, pageId) : null;
  if (!pending) return null;
  const href = reviewHref(pending.id);
  return (
    <div
      role="status"
      data-pending-edit=""
      className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-xs text-foreground"
    >
      <Icon name="Bot" size={14} className="shrink-0 text-primary" />
      <span className="min-w-0 flex-1">The assistant proposed an edit.</span>
      <Link href={href} onClick={go(href)} className="font-medium text-primary tech-transition hover:underline">
        Review
      </Link>
    </div>
  );
}
