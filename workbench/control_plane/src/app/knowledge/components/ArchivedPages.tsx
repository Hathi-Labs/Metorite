"use client";

/**
 * The archived pages of one space (WS-61 KB-2d, `knowledge_base.md` §21.7):
 * `/knowledge?space=<id>&view=archived`.
 *
 * Only a manager of the space sees this view, because only a manager can
 * restore (`pages.restore_page`). Each row opens the page, and Restore puts
 * it back in the state it had before the archive. The read is
 * `GET /knowledge/pages?space=<id>&state=archived`.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import { PersonName } from "@/components/PersonName";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { archivedPagesKey, listPages } from "../lib/api";
import { pageCount, spaceIcon } from "../lib/labels";
import { KNOWLEDGE_HOME, pageHref, spaceHref } from "../lib/links";
import type { PageSummary } from "../lib/types";
import { useKnowledge } from "./KnowledgeContext";
import { useRestorePage } from "./useRestorePage";

export default function ArchivedPages({ spaceId }: { spaceId: string }) {
  const { spaces } = useKnowledge();
  const router = useRouter();
  const space = spaces.find((s) => s.id === spaceId) ?? null;
  const manager = space?.level === "manage";
  const pages = useCachedResource<PageSummary[]>(manager ? archivedPagesKey(spaceId) : null, () =>
    listPages(spaceId, { state: "archived" }),
  );
  const { restoring, restore } = useRestorePage();

  if (!space) {
    return (
      <EmptyState
        icon="SearchX"
        message="This space is not here."
        hint="It may have been removed, or you may not have access to it."
        action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => router.push(KNOWLEDGE_HOME) }}
      />
    );
  }
  if (!manager) {
    return (
      <EmptyState
        icon="Lock"
        message={`Only a manager of ${space.name} sees its archived pages.`}
        hint="Ask a manager of the space to restore a page."
        action={{ label: `Open ${space.name}`, icon: "ArrowLeft", onClick: () => router.push(spaceHref(space.id)) }}
      />
    );
  }

  const rows = pages.data ?? [];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Icon name="Archive" size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-foreground">Archived pages</h2>
            <p className="mt-0.5 flex items-center gap-1 text-sm text-muted-foreground">
              <Icon name={spaceIcon(space)} size={14} className="shrink-0" />
              <Link href={spaceHref(space.id)} className="cc-link truncate hover:text-foreground">
                {space.name}
              </Link>
              {pages.data ? <span className="shrink-0">· {pageCount(rows.length)}</span> : null}
            </p>
          </div>
        </header>

        <section className="mt-5" aria-label={`Archived pages in ${space.name}`}>
          {pages.loading ? (
            <SkeletonRows count={3} />
          ) : pages.error && !pages.data ? (
            <EmptyState
              icon="CloudOff"
              message="The archived pages did not load."
              hint="The server did not answer. Try again in a moment."
              action={{ label: "Try again", icon: "RefreshCw", onClick: pages.refresh }}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              icon="Archive"
              message={`No archived pages in ${space.name}.`}
              hint="A page that a manager archives shows here, and a manager can restore it."
            />
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border bg-card">
              {rows.map((page) => (
                <li key={page.id} className="flex items-center gap-3 px-3 py-2.5" data-archived-page={page.id}>
                  <Icon name="FileText" size={16} className="shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <Link href={pageHref(page.id)} className="cc-link block truncate text-sm text-foreground">
                      {page.title}
                    </Link>
                    <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                      <span className="shrink-0">Archived {relativeTime(page.archived_at) || "now"} · written by</span>
                      <PersonName email={page.author_email} className="min-w-0" />
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    icon="ArchiveRestore"
                    loading={restoring === page.id}
                    disabled={restoring !== null}
                    aria-label={`Restore ${page.title}`}
                    onClick={() => void restore(page)}
                  >
                    Restore
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
