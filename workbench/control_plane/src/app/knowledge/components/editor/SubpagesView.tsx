"use client";

/**
 * The sub-pages list of the Knowledge editor (WS-61 KB-2p, §21.7).
 *
 * Adapted from Docmost (AGPL-3.0), apps/client/src/features/editor/components/subpages/subpages-view.tsx
 *
 * It lists the child pages of the open page, in the order the body names:
 * Manual (the order of the tree), A to Z, or Recent. The rows come from
 * `GET /knowledge/pages`, the pages list that the rail reads too, through
 * the one read cache. So the list follows the tree with no save, and a page
 * the member cannot see is never listed, because the gateway leaves it out.
 *
 * In the read view each row is a link, and the editor's click handler opens
 * it in place. While a member edits, a row is plain text, so a click never
 * leaves a page with unsaved changes past the leave guard.
 */
import { useParams } from "next/navigation";
import { NodeViewWrapper, type NodeViewProps } from "@tiptap/react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { SelectButton } from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { listPages } from "../../lib/api";
import {
  SUBPAGE_ORDERS,
  SUBPAGE_ORDER_LABELS,
  isSubpageOrder,
  sortSubpages,
  type SubpageOrder,
} from "../../lib/editor/subpages";
import { pageHref } from "../../lib/links";
import type { PageSummary, Space } from "../../lib/types";
import { useKnowledge } from "../KnowledgeContext";

/**
 * The live children of a page. The page's space is the one whose list holds
 * it. A space that fails to answer is left out, as in the `[[` picker.
 */
export async function loadSubpages(
  pageId: string,
  spaces: readonly Pick<Space, "id" | "archived_at">[],
  list: (spaceId: string) => Promise<PageSummary[]>,
): Promise<PageSummary[]> {
  const open = spaces.filter((s) => !s.archived_at);
  const lists = await Promise.allSettled(open.map((s) => list(s.id)));
  for (const r of lists) {
    if (r.status !== "fulfilled" || !r.value.some((p) => p.id === pageId)) continue;
    return r.value.filter((p) => p.parent_id === pageId && p.state !== "archived");
  }
  return [];
}

export default function SubpagesView({ node, editor, updateAttributes }: NodeViewProps) {
  const order: SubpageOrder = isSubpageOrder(node.attrs.order) ? node.attrs.order : "manual";
  const params = useParams<{ pageId?: string }>();
  const pageId = typeof params?.pageId === "string" ? params.pageId : null;
  const { spaces } = useKnowledge();
  const spaceIds = spaces.map((s) => s.id).join(",");
  const read = useCachedResource<PageSummary[]>(
    pageId && spaces.length ? `knowledge/subpages/${pageId}?spaces=${spaceIds}` : null,
    () => loadSubpages(pageId ?? "", spaces, listPages),
  );
  const editable = editor.isEditable;
  const rows = sortSubpages(read.data ?? [], order);

  return (
    <NodeViewWrapper className="kb-subpages" data-order={order} contentEditable={false}>
      <div className="kb-subpages-head">
        <span className="kb-subpages-title">
          <Icon name="Files" size={14} aria-hidden />
          Sub-pages
          {rows.length ? <span className="kb-subpages-count">{rows.length}</span> : null}
        </span>
        {/* As in Docmost, the order shows once there are two pages to order. */}
        {editable && rows.length > 1 ? (
          <SelectButton
            label="Order of the sub-pages"
            value={order}
            options={SUBPAGE_ORDERS.map((o) => ({ value: o, label: SUBPAGE_ORDER_LABELS[o] }))}
            onChange={(value) => isSubpageOrder(value) && updateAttributes({ order: value })}
            widthClass="w-32"
          />
        ) : null}
      </div>
      {read.loading ? (
        <div className="space-y-1.5" aria-label="Loading the sub-pages">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-4 w-36" />
        </div>
      ) : read.error && !read.data ? (
        <p role="alert" className="kb-subpages-note">
          Could not load the sub-pages. {read.error}{" "}
          <Button variant="text" size="none" onClick={read.refresh}>
            Try again
          </Button>
        </p>
      ) : rows.length === 0 ? (
        <p className="kb-subpages-note">No sub-pages yet. The pages you make under this page show here.</p>
      ) : (
        <ul className="kb-subpages-list" aria-label="Sub-pages">
          {rows.map((p) => (
            <li key={p.id}>
              {editable ? (
                <span className="kb-subpage">
                  <Icon name="FileText" size={14} aria-hidden />
                  <span className="truncate">{p.title}</span>
                </span>
              ) : (
                <a className="kb-subpage" href={pageHref(p.id)}>
                  <Icon name="FileText" size={14} aria-hidden />
                  <span className="truncate">{p.title}</span>
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </NodeViewWrapper>
  );
}
