"use client";

/**
 * One space's landing (WS-61 KB-2a): `/knowledge?space=<id>`.
 *
 * The name, the purpose, what the member may do, and the top pages of the
 * space. The whole tree is in the rail. A member who can write in the space
 * makes the first page from the empty state. A reader sees no write control.
 * A manager of the space gets the way to its archived pages (KB-2d).
 */

import Link from "next/link";
import { useRouter } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { listPages, pagesKey } from "../lib/api";
import { LEVEL_LABELS, canWriteIn, pageCount, pageIcon, spaceIcon, stateLabel, stateTone } from "../lib/labels";
import { KNOWLEDGE_HOME, archivedHref, pageHref } from "../lib/links";
import { buildTree } from "../lib/tree";
import type { PageSummary } from "../lib/types";
import { useKnowledge } from "./KnowledgeContext";

export default function SpaceLanding({ spaceId }: { spaceId: string }) {
  const { spaces, openNewPage } = useKnowledge();
  const router = useRouter();
  const space = spaces.find((s) => s.id === spaceId) ?? null;
  const pages = useCachedResource<PageSummary[]>(space ? pagesKey(space.id) : null, () =>
    listPages(spaceId),
  );

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

  const writable = canWriteIn(space.level);
  const roots = buildTree(pages.data ?? []);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Icon name={spaceIcon(space)} size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-foreground">{space.name}</h2>
            {space.purpose ? <p className="mt-0.5 text-sm text-muted-foreground">{space.purpose}</p> : null}
            <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{pageCount(space.page_count)}</span>
              <Badge>{LEVEL_LABELS[space.level] ?? space.level}</Badge>
            </p>
          </div>
          {space.level === "manage" ? (
            // KB-2d: only a manager restores, so only a manager sees the archive.
            <Button variant="ghost" size="sm" icon="Archive" href={archivedHref(space.id)} className="shrink-0">
              Archived pages
            </Button>
          ) : null}
        </header>

        <section className="mt-5" aria-label={`Pages in ${space.name}`}>
          {pages.loading ? (
            <SkeletonRows count={3} />
          ) : pages.error && !pages.data ? (
            <EmptyState
              icon="CloudOff"
              message="The pages of this space did not load."
              hint="The server did not answer. Try again in a moment."
              action={{ label: "Try again", icon: "RefreshCw", onClick: pages.refresh }}
            />
          ) : roots.length === 0 ? (
            <EmptyState
              icon="FileText"
              message={`No pages in ${space.name} yet.`}
              hint={
                writable
                  ? "Write the first page. It starts as a draft that only you see."
                  : "The writers of this space add pages here."
              }
              action={
                writable
                  ? { label: "New page", icon: "Plus", onClick: () => openNewPage({ spaceId: space.id }) }
                  : undefined
              }
            />
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border bg-card">
              {roots.map(({ page, children }) => (
                <li key={page.id}>
                  <Link
                    href={pageHref(page.id)}
                    className="cc-link flex items-center gap-3 px-3 py-2.5 tech-transition hover:bg-muted/40"
                  >
                    <Icon name={pageIcon(page)} size={16} className="shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-foreground">{page.title}</span>
                      <span className="block text-xs text-muted-foreground">
                        {children.length ? `${pageCount(children.length)} under it · ` : ""}
                        Updated {relativeTime(page.updated_at) || "now"}
                      </span>
                    </span>
                    <Badge tone={stateTone(page.state)}>{stateLabel(page.state)}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
