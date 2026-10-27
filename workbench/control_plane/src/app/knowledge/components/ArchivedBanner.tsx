"use client";

/**
 * The banner on an archived page (WS-61 KB-2d, `knowledge_base.md` §21.7).
 *
 * It says that the page is out of the tree and that nobody can edit it. A
 * manager of the space gets Restore here, and a link to the other archived
 * pages of the space. Another member reads who can restore it.
 */

import Link from "next/link";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { relativeTime } from "@/lib/taskCard";

import { archivedHref } from "../lib/links";
import type { PageDetail } from "../lib/types";
import { useRestorePage } from "./useRestorePage";

export default function ArchivedBanner({
  page,
}: {
  page: Pick<PageDetail, "id" | "title" | "archived_at" | "level" | "space_id">;
}) {
  const { restoring, restore } = useRestorePage();
  const manager = page.level === "manage";
  const when = relativeTime(page.archived_at);
  return (
    <div
      role="status"
      data-archived-banner=""
      className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-foreground"
    >
      <Icon name="Archive" size={14} className="shrink-0 text-warning" />
      <span className="min-w-0 flex-1 basis-full sm:basis-0">
        {when && when !== "now" ? `Archived ${when}. ` : "Archived. "}This page is out of the tree, and nobody can edit it.
        {manager ? "" : " A manager of the space can restore it."}
      </span>
      {manager ? (
        <>
          <Link href={archivedHref(page.space_id)} className="cc-link shrink-0 text-muted-foreground hover:text-foreground">
            All archived pages
          </Link>
          <Button
            size="sm"
            variant="secondary"
            icon="ArchiveRestore"
            loading={restoring === page.id}
            onClick={() => void restore(page)}
          >
            Restore page
          </Button>
        </>
      ) : null}
    </div>
  );
}
