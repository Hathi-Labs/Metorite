"use client";

/**
 * One proposal of the assistant, open for a decision (WS-61 KB-5b,
 * `knowledge_base.md` §8.1 and §11).
 *
 * An EDIT draws the proposed text through the page's own read-only editor,
 * with the words that changed marked in place against the page as it is now.
 * The marks come from `lib/historyDiff.ts`, the engine of the page history
 * (KB-2g). This file writes no second diff. A PAGE draws its draft read-only.
 *
 * The buttons are Accept ("Publish" or "Apply edit") and Reject. They call
 * the web-only routes (F2). A 409 shows the server's words and keeps the
 * proposal in the list. The parent moves to the next proposal after a
 * decision.
 */

import { useMemo, useState } from "react";
import type { Node as PMNode } from "@tiptap/pm/model";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { acceptProposal, getPage, pageKey, rejectProposal } from "../lib/api";
import { parseBody } from "../lib/editor/markdown";
import { changeCount, compareDecorations, compareDocs, wordCounts } from "../lib/historyDiff";
import { pageHref } from "../lib/links";
import {
  acceptLabel,
  alreadyDecided,
  decisionFailure,
  kindIcon,
  kindLabel,
  proposerLine,
  shownTitle,
  type DecisionFailure,
  type Proposal,
} from "../lib/review";
import type { PageDetail } from "../lib/types";
import KnowledgeEditor from "./editor/KnowledgeEditor";
import { knowledgeSchema } from "./history/HistoryView";

export type Decision = "accept" | "reject";

export default function ProposalDetail({
  proposal,
  spaceName,
  onDecided,
  onNavigate,
}: {
  proposal: Proposal;
  spaceName: string | null;
  /** The decision went through. The parent re-reads the list and moves on. */
  onDecided: (decision: Decision) => void;
  onNavigate: (href: string) => void;
}) {
  const [busy, setBusy] = useState<Decision | null>(null);
  const [failure, setFailure] = useState<DecisionFailure | null>(null);
  const isEdit = proposal.kind === "edit";
  const current = useCachedResource<PageDetail>(isEdit ? pageKey(proposal.page_id) : null, () =>
    getPage(proposal.page_id),
  );
  const base = isEdit ? (current.data?.body_md ?? null) : null;

  async function decide(decision: Decision) {
    setBusy(decision);
    setFailure(null);
    try {
      await (decision === "accept" ? acceptProposal(proposal.id) : rejectProposal(proposal.id));
      onDecided(decision);
    } catch (err) {
      setFailure(decisionFailure(err));
    } finally {
      setBusy(null);
    }
  }

  const title = shownTitle(proposal);
  const renamed = isEdit && current.data && current.data.title !== proposal.title;
  return (
    <article aria-label={`Proposal: ${title}`} className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge tone={isEdit ? "primary" : "warning"} icon={kindIcon(proposal.kind)}>
          {kindLabel(proposal.kind)}
        </Badge>
        {spaceName ? <span>{spaceName}</span> : null}
        <span aria-hidden>·</span>
        <span>
          {proposerLine(proposal)}
          {proposal.created_at ? `, ${relativeTime(proposal.created_at) || "now"}` : ""}
        </span>
      </div>
      <h2 className="mt-2 break-words text-xl font-semibold text-foreground">{title}</h2>
      {renamed ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Title changed from <del className="kb-diff-removed">{current.data?.title}</del>
        </p>
      ) : null}
      {proposal.summary ? (
        <p className="mt-2 rounded-md bg-muted px-3 py-2 text-sm text-foreground" data-proposal-summary="">
          {proposal.summary}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          icon={isEdit ? "Check" : "Send"}
          loading={busy === "accept"}
          disabled={busy !== null}
          onClick={() => void decide("accept")}
        >
          {acceptLabel(proposal.kind)}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon="X"
          loading={busy === "reject"}
          disabled={busy !== null}
          onClick={() => void decide("reject")}
        >
          Reject
        </Button>
        <Link
          href={pageHref(proposal.page_id)}
          onClick={(e) => {
            e.preventDefault();
            onNavigate(pageHref(proposal.page_id));
          }}
          className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground tech-transition hover:text-foreground"
        >
          <Icon name="ExternalLink" size={12} />
          {isEdit ? "Open the page" : "Open the draft"}
        </Link>
      </div>
      {failure ? <DecisionFailureNote failure={failure} /> : null}
      <div className="mt-5">
        {isEdit ? (
          <EditBody proposal={proposal} base={base} loading={current.loading} missing={Boolean(current.error)} onNavigate={onNavigate} />
        ) : proposal.body_md.trim() ? (
          <KnowledgeEditor
            key={proposal.id}
            mode="read"
            body={proposal.body_md}
            label={`Draft of ${title}, the page body`}
            onNavigate={onNavigate}
          />
        ) : (
          <p className="text-sm text-muted-foreground">The draft is empty.</p>
        )}
      </div>
    </article>
  );
}

/** The server's words after a refused decision, with what the member can do. */
function DecisionFailureNote({ failure }: { failure: DecisionFailure }) {
  const decided = failure.conflict && alreadyDecided(failure.detail);
  return (
    <div
      role="alert"
      data-decision-failure=""
      className="mt-3 flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-foreground"
    >
      <Icon name="TriangleAlert" size={14} className="mt-0.5 shrink-0 text-warning" />
      <div className="min-w-0">
        <p>{failure.detail}</p>
        {failure.conflict && !decided ? (
          <p className="mt-1 text-muted-foreground">
            Reject it, or ask the assistant to propose it again. The proposal stays here until you decide.
          </p>
        ) : null}
        {failure.gone ? (
          <p className="mt-1 text-muted-foreground">This proposal is not there for you any more.</p>
        ) : null}
      </div>
    </div>
  );
}

/** The proposed body of an edit, with the changed words marked against the page now. */
function EditBody({
  proposal,
  base,
  loading,
  missing,
  onNavigate,
}: {
  proposal: Proposal;
  base: string | null;
  loading: boolean;
  missing: boolean;
  onNavigate: (href: string) => void;
}) {
  const parsed = useMemo(() => {
    if (base === null) return null;
    const schema = knowledgeSchema();
    return {
      older: schema.nodeFromJSON(parseBody(base)),
      newer: schema.nodeFromJSON(parseBody(proposal.body_md)),
    };
  }, [base, proposal.body_md]);
  const result = useMemo(() => (parsed ? compareDocs(parsed.older, parsed.newer) : null), [parsed]);
  const decorate = useMemo(() => {
    if (!parsed || !result) return null;
    return (doc: PMNode) =>
      compareDecorations(doc, doc.eq(parsed.newer) ? result : compareDocs(parsed.older, doc), 0);
  }, [parsed, result]);

  if (loading && base === null) return <SkeletonRows count={5} />;
  if (base === null || missing) {
    return (
      <EmptyState
        icon="CloudOff"
        message="The page did not load, so there is no diff."
        hint="Open the page, or try again in a moment."
      />
    );
  }
  return (
    <>
      <p className="mb-3 text-xs text-muted-foreground" data-diff-counts="">
        {result && result.changes.length
          ? `${changeCount(result.changes.length)}: ${wordCounts(result)}`
          : "The words are the same as the page now."}
      </p>
      <KnowledgeEditor
        key={proposal.id}
        mode="read"
        body={proposal.body_md}
        label={`Proposed text of ${shownTitle(proposal)}, with the changes marked`}
        decorate={decorate}
        onNavigate={onNavigate}
      />
    </>
  );
}
