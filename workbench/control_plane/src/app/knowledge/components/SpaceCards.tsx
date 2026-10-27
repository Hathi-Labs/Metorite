"use client";

/**
 * The spaces as cards (WS-61 KB-2a, `knowledge_base.md` §11.1): the first
 * screen of Knowledge. Each card names the space, its purpose, its count of
 * pages and what the member may do there. A card opens the space. Under the
 * cards, the home lists of KB-2f (`KnowledgeHome`).
 */

import Link from "next/link";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";

import { LEVEL_LABELS, pageCount, spaceIcon } from "../lib/labels";
import { spaceHref } from "../lib/links";
import { useKnowledge } from "./KnowledgeContext";
import KnowledgeHome from "./KnowledgeHome";

export default function SpaceCards() {
  const { spaces, openNewPage } = useKnowledge();
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <h2 className="text-base font-semibold text-foreground">Spaces</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          What the company knows, written down. Open a space to read its pages.
        </p>
        <ul className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {spaces.map((space) => (
            <li key={space.id}>
              <Link
                href={spaceHref(space.id)}
                className="cc-link flex h-full flex-col gap-2 rounded-lg border border-border bg-card p-4 tech-transition hover:border-primary/40 hover:bg-muted/40"
              >
                <span className="flex items-center gap-2">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    <Icon name={spaceIcon(space)} size={16} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                    {space.name}
                  </span>
                </span>
                {space.purpose ? (
                  <span className="line-clamp-2 text-xs text-muted-foreground">{space.purpose}</span>
                ) : null}
                <span className="mt-auto flex flex-wrap items-center gap-2 pt-1 text-xs text-muted-foreground">
                  <span>{pageCount(space.page_count)}</span>
                  <Badge>{LEVEL_LABELS[space.level] ?? space.level}</Badge>
                </span>
              </Link>
            </li>
          ))}
          {/* The member's own space exists only after the first write
              (§4.1). Until then this card makes the first page there. */}
          {spaces.some((s) => s.kind === "me") ? null : (
            <li>
              <button
                type="button"
                onClick={() => openNewPage({ spaceId: null })}
                className="cc-link flex h-full w-full flex-col gap-2 rounded-lg border border-dashed border-border p-4 text-left tech-transition hover:border-primary/40 hover:bg-muted/40"
              >
                <span className="flex items-center gap-2">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    <Icon name="Lock" size={16} />
                  </span>
                  <span className="text-sm font-medium text-foreground">My notes</span>
                </span>
                <span className="text-xs text-muted-foreground">
                  Your private drafts. Only you can see them. Write your first page here.
                </span>
              </button>
            </li>
          )}
        </ul>
        {/* KB-2f: Recently updated, Favourites and Created by me. */}
        <KnowledgeHome />
      </div>
    </div>
  );
}
