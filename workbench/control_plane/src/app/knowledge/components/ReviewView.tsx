"use client";

/**
 * The Review view (WS-61 KB-5b, `knowledge_base.md` §5.3, §8.1 and §11).
 *
 * The proposals of the assistant that wait for the member: new pages and
 * edits, in two groups, oldest first. A list on the left and the open
 * proposal on the right. On a phone the list and the proposal take turns.
 *
 * A decision (the member accepts or rejects) re-reads the whole Knowledge
 * cache, because a publish or an apply changes the tree and the page. Then
 * it shows a toast and opens the next proposal. The assistant never decides:
 * the buttons call web-only routes (F2).
 */

import { useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import { invalidate } from "@/lib/dataCache";
import { relativeTime } from "@/lib/taskCard";

import { KNOWLEDGE_CACHE } from "../lib/api";
import { KNOWLEDGE_HOME, reviewHref } from "../lib/links";
import {
  acceptedToast,
  groupProposals,
  kindIcon,
  nextAfter,
  proposerLine,
  readingOrder,
  rejectedToast,
  shownTitle,
  type Proposal,
} from "../lib/review";
import { useKnowledge } from "./KnowledgeContext";
import ProposalDetail, { type Decision } from "./ProposalDetail";
import { useProposals } from "./useProposals";

export default function ReviewView() {
  const router = useRouter();
  const toast = useToast();
  const { isMobile } = useViewMode();
  const { spaces, requestLeave } = useKnowledge();
  const asked = useSearchParams().get("p");
  const read = useProposals();
  const list = useMemo(() => read.data ?? [], [read.data]);

  const spaceName = useCallback(
    (p: Proposal) => spaces.find((s) => s.id === p.space_id)?.name ?? null,
    [spaces],
  );
  const selected = list.find((p) => p.id === asked) ?? (isMobile ? null : (readingOrder(list)[0] ?? null));

  const decided = useCallback(
    (p: Proposal, decision: Decision) => {
      const next = nextAfter(list, p.id);
      invalidate(KNOWLEDGE_CACHE);
      toast.show({
        key: `kb-proposal:${p.id}`,
        title: decision === "accept" ? acceptedToast({ ...p, title: shownTitle(p) }) : rejectedToast({ ...p, title: shownTitle(p) }),
      });
      router.replace(reviewHref(next?.id ?? null));
    },
    [list, router, toast],
  );
  const go = useCallback((href: string) => requestLeave(() => router.push(href)), [requestLeave, router]);

  if (read.loading && !read.data) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <SkeletonRows count={4} />
      </div>
    );
  }
  if (read.error && !read.data) {
    return (
      <EmptyState
        icon="CloudOff"
        message="The proposals did not load."
        hint="The server did not answer. Try again in a moment."
        action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
      />
    );
  }
  if (list.length === 0) {
    return (
      <EmptyState
        icon="CircleCheck"
        message="Nothing waits for your review."
        hint="When the assistant proposes a page or an edit, it shows here. It never publishes by itself."
        action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => go(KNOWLEDGE_HOME) }}
      />
    );
  }

  const showList = !isMobile || selected === null;
  const showDetail = selected !== null;
  return (
    <div className="flex min-h-0 flex-1" data-review-view="">
      {showList ? (
        <nav
          aria-label="Proposals to review"
          className={`min-h-0 overflow-y-auto border-border bg-card p-2 ${isMobile ? "w-full" : "w-80 shrink-0 border-r"}`}
        >
          {groupProposals(list).map((group) => (
            <section key={group.kind} aria-label={group.label} className="mb-3">
              <h3 className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {group.label} <span className="tabular-nums">{group.items.length}</span>
              </h3>
              <ul className="flex flex-col gap-0.5">
                {group.items.map((p) => (
                  <li key={p.id}>
                    <ProposalRow proposal={p} space={spaceName(p)} selected={p.id === selected?.id} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </nav>
      ) : null}
      {showDetail ? (
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {isMobile ? (
            <div className="px-4 pt-3">
              <Button variant="ghost" size="sm" icon="ChevronLeft" onClick={() => router.push(reviewHref())}>
                All proposals
              </Button>
            </div>
          ) : null}
          <ProposalDetail
            key={selected.id}
            proposal={selected}
            spaceName={spaceName(selected)}
            onDecided={(d) => decided(selected, d)}
            onNavigate={go}
          />
        </div>
      ) : null}
    </div>
  );
}

function ProposalRow({ proposal, space, selected }: { proposal: Proposal; space: string | null; selected: boolean }) {
  return (
    <Link
      href={reviewHref(proposal.id)}
      replace
      aria-current={selected ? "true" : undefined}
      data-proposal-row={proposal.id}
      className={`flex flex-col gap-0.5 rounded-md px-2 py-2 tech-transition hover:bg-muted ${selected ? "bg-muted" : ""}`}
    >
      <span className="flex items-center gap-2">
        <Icon name={kindIcon(proposal.kind)} size={14} className="shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{shownTitle(proposal)}</span>
        <Badge tone={proposal.kind === "edit" ? "primary" : "warning"} size="xs">
          {proposal.kind === "edit" ? "Edit" : "New page"}
        </Badge>
      </span>
      <span className="truncate text-xs text-muted-foreground">
        {[space, proposal.kind === "edit" && proposal.page_title ? `Page: ${proposal.page_title}` : null]
          .filter(Boolean)
          .join(" · ")}
      </span>
      <span className="truncate text-xs text-muted-foreground">
        {proposerLine(proposal)}
        {proposal.created_at ? `, ${relativeTime(proposal.created_at) || "now"}` : ""}
      </span>
      {proposal.summary ? <span className="line-clamp-2 text-xs text-foreground/80">{proposal.summary}</span> : null}
    </Link>
  );
}
