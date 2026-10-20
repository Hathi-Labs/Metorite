"use client";

/**
 * The breadcrumbs of a page (WS-61 KB-2d, `knowledge_base.md` §21.7).
 *
 * The space, each parent and the page. A trail of more than three crumbs
 * folds its middle into a `…` that opens the hidden crumbs. On a phone, the
 * row is one button, and every crumb is in its list (`lib/breadcrumbs.ts`).
 *
 * The parents come from the same read as the rail's tree (`pagesKey`), so
 * the trail costs no request of its own. Each crumb goes through the leave
 * guard of the layout, as every other way out of a page does.
 */

import { useId, useState } from "react";
import Link from "next/link";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import { useCachedResource } from "@/lib/useCachedResource";

import { listPages, pagesKey } from "../lib/api";
import { foldCrumbs, parentChain, type Crumb } from "../lib/breadcrumbs";
import { pageHref, spaceHref } from "../lib/links";
import type { PageDetail, PageSummary, Space } from "../lib/types";
import ChromePopover from "./ChromePopover";

export default function PageBreadcrumbs({
  page,
  space,
  onGo,
}: {
  page: Pick<PageDetail, "id" | "title" | "parent_id" | "space_id">;
  space: Space | null;
  /** Go to an address through the leave guard. Return true to stop the link. */
  onGo: (href: string) => boolean;
}) {
  const { isMobile } = useViewMode();
  const pages = useCachedResource<PageSummary[]>(space ? pagesKey(space.id) : null, () =>
    listPages(page.space_id),
  );
  const [open, setOpen] = useState(false);
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const panelId = useId();

  const trail: Crumb[] = [
    ...(space ? [{ id: space.id, title: space.name, href: spaceHref(space.id), kind: "space" as const }] : []),
    ...parentChain(pages.data ?? [], page.parent_id).map((p) => ({
      id: p.id,
      title: p.title,
      href: pageHref(p.id),
      kind: "page" as const,
    })),
    { id: page.id, title: page.title, href: pageHref(page.id), kind: "page" },
  ];
  const { head, folded, tail } = foldCrumbs(trail, isMobile);

  const crumb = (c: Crumb, last: boolean) =>
    last ? (
      <span aria-current="page" className="min-w-0 truncate text-foreground">
        {c.title}
      </span>
    ) : (
      <Link
        href={c.href}
        onClick={(e) => {
          if (onGo(c.href)) e.preventDefault();
        }}
        className="cc-link min-w-0 truncate text-muted-foreground hover:text-foreground"
      >
        {c.title}
      </Link>
    );

  const separator = <Icon name="ChevronRight" size={12} className="shrink-0 text-muted-foreground/70" />;
  const lastId = trail[trail.length - 1]?.id;

  return (
    <nav aria-label="Breadcrumbs" className="min-w-0 flex-1">
      <ol className="flex min-w-0 items-center gap-1 text-xs">
        {head.map((c, i) => (
          <li key={c.id} className="flex min-w-0 max-w-48 items-center gap-1">
            {i > 0 ? separator : null}
            {crumb(c, c.id === lastId && !folded.length && !tail.length)}
          </li>
        ))}
        {folded.length ? (
          <li className="flex shrink-0 items-center gap-1">
            {head.length ? separator : null}
            <Button
              variant="ghost"
              size="icon-xs"
              icon={isMobile ? "ListTree" : "MoreHorizontal"}
              aria-label={isMobile ? "Show the path to this page" : `Show ${folded.length} hidden pages of the path`}
              aria-haspopup="dialog"
              aria-expanded={open}
              aria-controls={open ? panelId : undefined}
              onClick={(e) => {
                setTrigger(e.currentTarget);
                setOpen((v) => !v);
              }}
            />
            <ChromePopover
              trigger={trigger}
              open={open}
              onClose={() => setOpen(false)}
              label="The path to this page"
              id={panelId}
            >
              <ol className="flex flex-col">
                {folded.map((c) => (
                  <li key={c.id}>
                    <Link
                      href={c.href}
                      onClick={(e) => {
                        setOpen(false);
                        if (onGo(c.href)) e.preventDefault();
                      }}
                      aria-current={c.id === lastId ? "page" : undefined}
                      className="cc-link flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground tech-transition hover:bg-muted"
                    >
                      <Icon
                        name={c.kind === "space" ? "LayoutGrid" : "FileText"}
                        size={14}
                        className="shrink-0 text-muted-foreground"
                      />
                      <span className="min-w-0 truncate">{c.title}</span>
                    </Link>
                  </li>
                ))}
              </ol>
            </ChromePopover>
          </li>
        ) : null}
        {tail.map((c) => (
          <li key={c.id} className="flex min-w-0 max-w-48 items-center gap-1">
            {separator}
            {crumb(c, c.id === lastId)}
          </li>
        ))}
      </ol>
    </nav>
  );
}
