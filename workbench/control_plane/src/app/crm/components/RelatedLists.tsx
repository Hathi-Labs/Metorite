"use client";

/**
 * The related lists of a record page (WS-53 CRM-Q3, spec section 18.4).
 *
 * A company shows its contacts and its deals. A contact shows its deals. A
 * deal shows its contacts with their roles, and that section stays in
 * `FieldsPanel` because it also holds the primary star and the detach verb.
 *
 * Each row opens that record. The rows come from the existing list routes
 * with a filter (`?company_id=`, `?contact_id=`), so a related row is the same
 * row the list shows.
 */

import { relatedSections } from "../lib/related";
import type { RelatedLists as Related } from "../lib/related";
import type { EntitySlug } from "../lib/types";

type Row = Record<string, unknown>;

export default function RelatedLists({
  entity,
  related,
  onOpen,
}: {
  entity: EntitySlug;
  related: Related;
  onOpen: (entity: EntitySlug, id: string) => void;
}) {
  const sections = relatedSections(entity, related);
  if (sections.length === 0) return null;
  return (
    <div className="border-t border-border" aria-label="Related lists">
      {sections.map((section) => (
        <section key={section.title} className="border-b border-border px-4 py-3 last:border-b-0">
          <h3 className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">
            {section.title}
            <span className="ml-1.5 normal-case text-muted-foreground/80">
              {section.rows.length}
            </span>
          </h3>
          {section.rows.length === 0 ? (
            <p className="text-xs text-muted-foreground">{section.empty}</p>
          ) : (
            <ul className="space-y-1">
              {section.rows.map((row: Row) => (
                <li key={String(row.id)}>
                  <button
                    type="button"
                    onClick={() => onOpen(section.entity, String(row.id))}
                    className="flex w-full min-w-0 items-baseline gap-2 rounded-md px-1 py-1 text-left tech-transition hover:bg-secondary/50"
                  >
                    <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                      {section.name(row)}
                    </span>
                    <span className="max-w-[45%] shrink-0 truncate text-[10px] text-muted-foreground">
                      {section.detail(row)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
