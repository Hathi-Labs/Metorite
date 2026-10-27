"use client";

/**
 * The Knowledge home lists (WS-61 KB-2f, `knowledge_base.md` §21.7).
 *
 * Under the space cards, three tabs, in the shape of Docmost's home:
 *
 *   - Recently updated: the live pages the member can read, newest change
 *     first, with Load more (`GET /knowledge/recent`);
 *   - Favourites: the pages the member starred and still sees
 *     (`GET /knowledge/favorites`);
 *   - Created by me: the member's own pages, drafts too, with Load more
 *     (`GET /knowledge/recent?mine=true`).
 *
 * Each row shows the page icon, the title, the space and when it changed.
 * Each tab has its own empty state with one next act. The first page of a
 * list goes through the read cache, so a return to the home never blanks.
 * Load more asks for the next page with the cursor of the last answer, and
 * a new first page drops the pages that were added below it.
 */

import { useCallback, useState } from "react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { CREATED_KEY, RECENT_KEY, listRecent, type HomeList, type HomePage } from "../lib/homeApi";
import { dayLabel, stateLabel } from "../lib/labels";
import { pageHref } from "../lib/links";
import { useFavorites } from "./Favorites";
import { useKnowledge } from "./KnowledgeContext";
import { PageGlyph } from "./PageIcon";

/** The three tabs of the home. */
export const HOME_TABS = [
  { id: "recent", label: "Recently updated", icon: "Clock" },
  { id: "favorites", label: "Favourites", icon: "Star" },
  { id: "created", label: "Created by me", icon: "UserRound" },
] as const;

export type HomeTab = (typeof HOME_TABS)[number]["id"];

/** The first page, then each page Load more added, with no page twice. Pure. */
export function mergePages(first: readonly HomePage[], more: readonly HomePage[]): HomePage[] {
  const seen = new Set<string>();
  const out: HomePage[] = [];
  for (const page of [...first, ...more]) {
    if (seen.has(page.id)) continue;
    seen.add(page.id);
    out.push(page);
  }
  return out;
}

export default function KnowledgeHome() {
  const [tab, setTab] = useState<HomeTab>("recent");
  return (
    <section aria-label="Your pages" className="mt-8">
      <Tabs
        tabs={HOME_TABS.map((t) => ({ ...t }))}
        activeTab={tab}
        onTabChange={(id) => setTab(id as HomeTab)}
        variant="underline"
        chrome="inline"
        className="border-b border-border"
      />
      <div className="pt-3" role="tabpanel" aria-label={HOME_TABS.find((t) => t.id === tab)?.label}>
        {tab === "recent" ? <PagedList key="recent" mine={false} /> : null}
        {tab === "favorites" ? <FavoriteList /> : null}
        {tab === "created" ? <PagedList key="created" mine /> : null}
      </div>
    </section>
  );
}

/** Recently updated, or Created by me, with Load more. */
function PagedList({ mine }: { mine: boolean }) {
  const { openNewPage } = useKnowledge();
  const first = useCachedResource<HomeList>(mine ? CREATED_KEY : RECENT_KEY, () => listRecent({ mine }));
  // The pages Load more added, and the first page they follow.
  const [more, setMore] = useState<{ after: HomeList | undefined; pages: HomePage[]; cursor: string | null }>({
    after: undefined,
    pages: [],
    cursor: null,
  });
  const [busy, setBusy] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  // A new first page starts the list again. Set during render.
  const extra = more.after === first.data ? more : { after: first.data, pages: [], cursor: null };
  const cursor = extra.after === undefined ? null : extra.pages.length ? extra.cursor : (first.data?.next_cursor ?? null);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setBusy(true);
    setMoreError(null);
    try {
      const next = await listRecent({ mine, cursor });
      setMore({ after: first.data, pages: [...extra.pages, ...next.pages], cursor: next.next_cursor });
    } catch (err) {
      setMoreError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [cursor, extra.pages, first.data, mine]);

  if (first.loading) return <SkeletonRows count={4} />;
  if (!first.data) {
    return (
      <EmptyState
        icon="CloudOff"
        message="This list did not load."
        hint="The server did not answer. Try again in a moment."
        action={{ label: "Try again", icon: "RefreshCw", onClick: first.refresh }}
      />
    );
  }
  const pages = mergePages(first.data.pages, extra.pages);
  if (pages.length === 0) {
    return mine ? (
      <EmptyState
        icon="FilePen"
        message="You have not written a page yet."
        hint="The pages you write show here, drafts too."
        action={{ label: "New page", icon: "Plus", onClick: () => openNewPage({}) }}
      />
    ) : (
      <EmptyState
        icon="FileText"
        message="No pages yet."
        hint="The pages that change in the spaces you can read show here."
        action={{ label: "New page", icon: "Plus", onClick: () => openNewPage({}) }}
      />
    );
  }
  return (
    <>
      <PageRows pages={pages} when="updated" />
      {moreError ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          Could not load more pages. {moreError}
        </p>
      ) : null}
      {cursor ? (
        <div className="mt-2 flex justify-center">
          <Button variant="ghost" size="sm" loading={busy} disabled={busy} onClick={() => void loadMore()}>
            Load more
          </Button>
        </div>
      ) : null}
    </>
  );
}

/** The member's favourites. */
function FavoriteList() {
  const favorites = useFavorites();
  if (favorites.loading) return <SkeletonRows count={4} />;
  if (!favorites.data) {
    return (
      <EmptyState
        icon="CloudOff"
        message="Your favourites did not load."
        hint="The server did not answer. Try again in a moment."
        action={{ label: "Try again", icon: "RefreshCw", onClick: favorites.refresh }}
      />
    );
  }
  if (favorites.data.pages.length === 0) {
    return (
      <EmptyState
        icon="Star"
        message="No favourites yet."
        hint="Choose the star beside the title of a page, and the page shows here."
      />
    );
  }
  return <PageRows pages={favorites.data.pages} when="starred" />;
}

/** The rows of a home list. One link a row, as the search view draws. */
function PageRows({ pages, when }: { pages: readonly HomePage[]; when: "updated" | "starred" }) {
  return (
    <ul className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
      {pages.map((page) => {
        const stamp = when === "starred" ? (page.starred_at ?? null) : page.updated_at;
        return (
          <li key={page.id}>
            <Link
              href={pageHref(page.id)}
              className="cc-link flex min-w-0 items-center gap-3 px-3 py-2.5 tech-transition hover:bg-muted/40"
            >
              <PageGlyph page={page} size="md" className="text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{page.title}</span>
              {page.state === "draft" ? (
                <span className="shrink-0 text-[11px] text-muted-foreground">{stateLabel(page.state)}</span>
              ) : null}
              <span className="hidden max-w-[10rem] shrink-0 truncate rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground sm:inline">
                {page.space_name}
              </span>
              <span
                className="w-16 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground"
                title={dayLabel(stamp)}
              >
                {relativeTime(stamp) || dayLabel(stamp)}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
