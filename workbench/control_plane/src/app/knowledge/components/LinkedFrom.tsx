"use client";

/**
 * "Linked from": the pages that link to this page (WS-61 KB-2h,
 * `knowledge_base.md` §21.5). The gateway lists published pages the member
 * can see, so a link the member cannot open is never named here. It reads
 * through `KNOWLEDGE_CACHE`, so a page write re-reads it. With no links it
 * shows one short muted line.
 */

import Link from "next/link";

import { useCachedResource } from "@/lib/useCachedResource";

import { backlinksKey, getBacklinks, type Backlink } from "../lib/api";
import { pageHref } from "../lib/links";

export default function LinkedFrom({ pageId }: { pageId: string }) {
  const read = useCachedResource<Backlink[]>(backlinksKey(pageId), () => getBacklinks(pageId));
  const links = read.data ?? [];
  return (
    <section aria-label="Linked from">
      <h3 className="mb-1 text-xs font-medium text-foreground">Linked from</h3>
      {links.length === 0 ? (
        <p className="text-xs text-muted-foreground">No page links here yet.</p>
      ) : (
        <ul className="space-y-0.5">
          {links.map((l) => (
            <li key={l.id}>
              <Link
                href={pageHref(l.id)}
                className="block rounded-md px-1.5 py-1 text-xs tech-transition hover:bg-muted"
              >
                <span className="block truncate text-foreground">
                  {l.icon ? `${l.icon} ` : ""}
                  {l.title}
                </span>
                <span className="block truncate text-[11px] text-muted-foreground">{l.space_name}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
