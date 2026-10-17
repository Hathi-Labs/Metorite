"use client";

/**
 * The small cards of the CRM settings page (WS-53 CRM-Q3, spec section 18.4).
 *
 * The page is one column of cards in a fixed order: Setup (with the Zoho
 * connection, CRM-X0), Access, Deal stages, Lead statuses, Lost reasons, Fields,
 * Suggestions (CRM-Q20-2), Sync health.
 * `PipelineSettings` composes them. The editable grids stay in that file, and
 * these are the cards that only read or only wait.
 */

import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { stagesOf } from "../lib/pipelines";
import type { DealPipeline, EntitySlug, Status } from "../lib/types";
import { zohoLink } from "../lib/connect";
import {
  DISMISS_LABEL,
  NO_SUGGESTIONS,
  SLOT_SLUG,
  SUGGESTIONS_NOTE,
  SUGGESTIONS_TITLE,
  UNDO_LABEL,
  emptyLine,
  inUseLine,
  previewLabel,
  type OverlayInUse,
  type Suggestion,
  type SuggestionList,
} from "../lib/suggestions";

/** The frame every card shares, so the page has one look. */
export function Card({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-card/40" aria-label={title}>
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
      </header>
      {children}
    </section>
  );
}

/** A list of names to read: no input, no handle and no delete. */
export function NameList({ names, empty = "None yet." }: { names: string[]; empty?: string }) {
  if (names.length === 0) {
    return <p className="px-4 py-3 text-xs text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="space-y-0.5 px-4 py-3">
      {names.map((name, i) => (
        <li key={`${name}-${i}`} className="text-xs text-foreground">
          {name}
        </li>
      ))}
    </ul>
  );
}

/** A card for work that a later slice fills. It says so and does nothing else. */
export function ComingSoonCard({ title, note }: { title: string; note: string }) {
  return (
    <Card title={title} note={note}>
      <p className="px-4 py-3 text-xs text-muted-foreground">Coming soon.</p>
    </Card>
  );
}

/**
 * The stages of each pipeline, to read.
 *
 * Used when the org has two pipelines or more, and in Mirror mode. There is
 * one section for each pipeline. The stage routes of CRM-Q2 do not exist in
 * this build, so no stage here is editable and the note says why.
 */
export function PipelineSections({
  pipelines,
  fallback,
  note,
  title = "Deal stages",
}: {
  pipelines: DealPipeline[] | null;
  /** The one implicit pipeline: the deal statuses, when there is no pipeline list. */
  fallback: Status[];
  note: string;
  /** The card heading. It is the label of the rail row (CRM-C1). */
  title?: string;
}) {
  const sections =
    pipelines && pipelines.length > 0
      ? [...pipelines].sort((a, b) => a.position - b.position)
      : null;
  return (
    <Card title={title} note={note}>
      {sections ? (
        <div className="divide-y divide-border/60">
          {sections.map((pipeline) => {
            const link = zohoLink({ ext_url: pipeline.ext_url });
            return (
              <div key={pipeline.id} aria-label={pipeline.name}>
                <div className="flex items-center gap-2 px-4 pt-3">
                  <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    {pipeline.name}
                  </h3>
                  {pipeline.is_default && (
                    <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">
                      default
                    </span>
                  )}
                  {link && (
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="ml-auto text-[10px] text-primary underline underline-offset-2"
                    >
                      Open in Zoho
                    </a>
                  )}
                </div>
                <NameList
                  names={stagesOf(pipeline).map((stage) => stage.name)}
                  empty="No stages yet."
                />
              </div>
            );
          })}
        </div>
      ) : (
        <NameList names={fallback.map((s) => s.name)} />
      )}
    </Card>
  );
}

/** What the Suggestions card can do (CRM-Q20-2). The caller owns the requests. */
export type SuggestionsHandlers = {
  /** The label of a core list, as the rail draws it ("Deals"). */
  listLabel: (slug: EntitySlug) => string;
  onPreview: (s: Suggestion) => void;
  onDismiss: (s: Suggestion) => void;
  onUndo: (o: OverlayInUse) => void;
  /** The slot whose request runs, or `null`. */
  busy: string | null;
};

/**
 * Settings → Suggestions (WS-53 CRM-Q20-2): each open suggestion, with
 * Preview and "Not now", and each list that a Zoho module shows now, with
 * Undo. It holds no state and no hook, so a test reads its element tree.
 */
export function SuggestionsCardView({
  list,
  failed = false,
  handlers,
}: {
  /** `GET /crm/mapping/suggestions`, or `null` while it loads. */
  list: SuggestionList | null;
  failed?: boolean;
  handlers: SuggestionsHandlers;
}) {
  const label = (slot: Suggestion["slot"]) => handlers.listLabel(SLOT_SLUG[slot]);
  return (
    <Card title={SUGGESTIONS_TITLE} note={SUGGESTIONS_NOTE}>
      {list === null ? (
        <div className="px-4 py-3">
          {failed ? (
            <p className="text-xs text-muted-foreground">The suggestions did not load.</p>
          ) : (
            <Skeleton className="h-4 w-56" />
          )}
        </div>
      ) : (
        <ul className="divide-y divide-border/60">
          {list.overlays.map((o) => (
            <li key={`use:${o.slot}`} className="flex flex-wrap items-center gap-2 px-4 py-3">
              <span className="w-full min-w-0 text-xs text-foreground sm:w-auto sm:flex-1">
                {inUseLine(o, label(o.slot))}
              </span>
              <Button
                size="sm"
                variant="ghost"
                icon="Undo2"
                loading={handlers.busy === o.slot}
                disabled={handlers.busy !== null}
                onClick={() => handlers.onUndo(o)}
              >
                {UNDO_LABEL}
              </Button>
            </li>
          ))}
          {list.suggestions.map((s) => (
            <li key={`suggest:${s.slot}`} className="flex flex-wrap items-center gap-2 px-4 py-3">
              <span className="w-full min-w-0 text-xs text-foreground sm:w-auto sm:flex-1">
                {emptyLine(s, label(s.slot))}
              </span>
              <Button
                size="sm"
                variant="ghost"
                loading={handlers.busy === s.slot}
                disabled={handlers.busy !== null}
                onClick={() => handlers.onDismiss(s)}
              >
                {DISMISS_LABEL}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                icon="Eye"
                disabled={handlers.busy !== null}
                onClick={() => handlers.onPreview(s)}
              >
                {previewLabel(s, label(s.slot))}
              </Button>
            </li>
          ))}
          {list.overlays.length === 0 && list.suggestions.length === 0 && (
            <li className="px-4 py-3 text-xs text-muted-foreground">{NO_SUGGESTIONS}</li>
          )}
        </ul>
      )}
    </Card>
  );
}
