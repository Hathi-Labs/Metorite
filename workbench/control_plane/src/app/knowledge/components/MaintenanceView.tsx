"use client";

/**
 * The Maintenance view (WS-61 KB-AI-4, `knowledge_base.md` §22.3):
 * `/knowledge/maintenance`.
 *
 * The published pages that need care, for a manager of Knowledge. A page is
 * on the list when its review is overdue, when it has no owner, or when
 * nobody edited it for a year. The gateway builds ONE row for each page, so
 * the reasons show as chips on that one row. A row opens the page, where a
 * writer of the space verifies it, sets the owner or changes the interval.
 * The list re-reads when any Knowledge write invalidates the cache.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import { PersonName } from "@/components/PersonName";
import Badge from "@/components/ui/Badge";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { dayLabel } from "../lib/labels";
import { KNOWLEDGE_HOME, pageHref } from "../lib/links";
import {
  MAINTENANCE_KEY,
  REASON_CHIPS,
  listMaintenance,
  chipLabel,
  type MaintenanceList,
} from "../lib/maintenance";

export default function MaintenanceView() {
  const router = useRouter();
  const read = useCachedResource<MaintenanceList>(MAINTENANCE_KEY, listMaintenance);
  const list = read.data;
  const rows = list?.pages ?? [];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Icon name="ShieldCheck" size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-foreground">Maintenance</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Pages that need care: the review is overdue, nobody owns them, or nobody edited them for a year.
            </p>
          </div>
        </header>

        <section className="mt-5" aria-label="Pages that need care">
          {read.loading ? (
            <SkeletonRows count={3} />
          ) : read.error && !list ? (
            <EmptyState
              icon="CloudOff"
              message="The list did not load."
              hint="The server did not answer, or you may not see this list. Try again in a moment."
              action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              icon="CircleCheck"
              message="Every page is in good care."
              hint="When a page is overdue, has no owner or has gone stale, it shows here."
              action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => router.push(KNOWLEDGE_HOME) }}
            />
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border bg-card">
              {rows.map((p) => (
                <li key={p.id} className="flex flex-col gap-1.5 px-3 py-2.5" data-maintenance-id={p.id}>
                  <span className="flex min-w-0 items-start gap-3">
                    <Icon name="FileText" size={16} className="mt-0.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <Link
                        href={pageHref(p.id)}
                        className="block truncate text-sm font-medium text-foreground hover:underline"
                      >
                        {p.title}
                      </Link>
                      <span className="flex min-w-0 items-baseline gap-1 text-xs text-muted-foreground">
                        <span className="shrink-0">{p.space_name}</span>
                        <span aria-hidden="true">·</span>
                        {p.owner_email ? (
                          <PersonName email={p.owner_email} className="min-w-0" />
                        ) : (
                          <span>No owner</span>
                        )}
                      </span>
                    </span>
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5 pl-7">
                    {p.reasons.map((r) => (
                      <Badge key={r} tone={REASON_CHIPS[r].tone} icon={REASON_CHIPS[r].icon}>
                        {chipLabel(r, p.review_at, dayLabel)}
                      </Badge>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
