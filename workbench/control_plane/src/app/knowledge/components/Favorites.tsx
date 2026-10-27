"use client";

/**
 * The star of a Knowledge page (WS-61 KB-2f, `knowledge_base.md` §21.7).
 *
 * One read of the member's favourites (`GET /knowledge/favorites`) feeds
 * every star on screen: the title row of a page, the tree menu and the home
 * list. A star or an unstar draws at once, then re-reads every Knowledge
 * list, because the cache keys share `KNOWLEDGE_CACHE`.
 *
 * The gateway takes the member from the session. A page the member cannot
 * see answers 404, and the star then says why in a toast.
 */

import { useCallback } from "react";

import type { CtxItem } from "@/components/ContextMenu";
import Icon, { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { invalidate, peek, put } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import { KNOWLEDGE_CACHE, KnowledgeError } from "../lib/api";
import {
  FAVORITES_KEY,
  favoriteIds,
  listFavorites,
  starPage,
  unstarPage,
  type HomeList,
  type HomePage,
} from "../lib/homeApi";
import type { PageSummary } from "../lib/types";

/** The words of a star, so the button and the menu say the same thing. */
export function starLabel(starred: boolean): string {
  return starred ? "Remove from favourites" : "Add to favourites";
}

/** The member's favourites. `enabled` false holds still and reads nothing. */
export function useFavorites(enabled = true) {
  return useCachedResource<HomeList>(enabled ? FAVORITES_KEY : null, listFavorites);
}

type StarPage = Pick<PageSummary, "id" | "title"> & Partial<PageSummary>;

/** The list with *page* starred or not, for the draw before the answer. Pure. */
export function withStar(list: HomeList | undefined, page: StarPage, starred: boolean): HomeList {
  const pages = (list?.pages ?? []).filter((p) => p.id !== page.id);
  if (starred) pages.unshift({ space_name: "", ...(page as PageSummary) } as HomePage);
  return { pages, next_cursor: list?.next_cursor ?? null };
}

/** Is *page* starred, and the act that turns the star over. */
export function useFavoriteToggle(page: StarPage | null, enabled = true) {
  const toast = useToast();
  const favorites = useFavorites(enabled && page !== null);
  const starred = page ? favoriteIds(favorites.data).has(page.id) : false;

  const toggle = useCallback(async () => {
    if (!page) return;
    const next = !starred;
    const before = peek<HomeList>(FAVORITES_KEY)?.data;
    put(FAVORITES_KEY, withStar(before, page, next));
    try {
      if (next) await starPage(page.id);
      else await unstarPage(page.id);
      toast.show({
        key: `kb-star:${page.id}`,
        title: next ? `Added ${page.title} to favourites` : `Removed ${page.title} from favourites`,
      });
    } catch (err) {
      if (before) put(FAVORITES_KEY, before);
      const reason =
        err instanceof KnowledgeError && err.status === 404
          ? "The page is not there for you any more."
          : err instanceof Error
            ? err.message
            : String(err);
      toast.show({
        key: `kb-star:${page.id}`,
        variant: "error",
        title: next ? "Could not add the page to favourites" : "Could not remove the page from favourites",
        description: reason,
      });
    } finally {
      invalidate(KNOWLEDGE_CACHE);
    }
  }, [page, starred, toast]);

  return { starred, toggle };
}

/** The star button of a page's title row. */
export function FavoriteStar({ page }: { page: StarPage }) {
  const { starred, toggle } = useFavoriteToggle(page);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={`${starLabel(starred)}: ${page.title}`}
      aria-pressed={starred}
      title={starLabel(starred)}
      onClick={() => void toggle()}
    >
      <Icon name="Star" size={16} className={starred ? "fill-current text-warning" : "text-muted-foreground"} />
    </Button>
  );
}

/** The star item of a tree row's menu. */
export function favoriteMenuItem(starred: boolean, toggle: () => void): CtxItem {
  return {
    kind: "item",
    label: starLabel(starred),
    icon: themedIcon(starred ? "StarOff" : "Star"),
    onSelect: toggle,
  };
}
