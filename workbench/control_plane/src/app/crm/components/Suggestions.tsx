"use client";

/**
 * The suggestions of the mapping, on screen (WS-53 CRM-Q20-2, spec
 * crm_platform.md section 18.9.6).
 *
 * The mapping pipeline suggests a Zoho module for an empty core list, for
 * example "Commercials" for Deals. The admin's path (section 18.9 v4):
 *
 * 1. The empty list says where its records are: "Preview Commercials as
 *    Deals" (`SlotSuggestion`). A member sees why the list is empty, and no
 *    suggestion (`memberEmptyLine`, through `RecordList`).
 * 2. The panel (`SuggestionPanel`) shows three facts, real records of the
 *    module, at most three checks, and what changes. One action uses it.
 * 3. A toast says it is done, with Undo. Settings → Suggestions
 *    (`SuggestionsCard`) also holds Undo for each list in use.
 *
 * No record moves (option A): the rail row of the list opens the module's
 * list. `lib/suggestions.ts` holds every word and rule, and this file only
 * draws them. No engine word and no confidence number reach the screen.
 */

import Button from "@/components/ui/Button";
import { EmptyState } from "@/components/EmptyState";
import Modal from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";
import { useState } from "react";

import * as api from "../lib/api";
import {
  PREVIEW_SHOWN,
  PREVIEW_UNREAD,
  SLOT_SLUG,
  SUGGESTIONS_KEY,
  suggestionError,
  cellText,
  checks,
  columnName,
  dismissedLine,
  emptyLine,
  facts,
  previewLabel,
  undoneLine,
  useLabel,
  usedLine,
  whatChanges,
  type OverlayInUse,
  type OverlayPreview,
  type Suggestion,
  type SuggestionList,
} from "../lib/suggestions";
import type { EntitySlug } from "../lib/types";
import { useSourceLabels } from "../lib/useCrmMode";
import { viewLabel } from "../lib/views";
import { SuggestionsCardView } from "./SettingsCards";


/** The open suggestions, for an admin in Mirror mode. A failure reads as none. */
export function useSuggestions(enabled: boolean): {
  list: SuggestionList | null;
  failed: boolean;
} {
  const res = useCachedResource<SuggestionList | null>(enabled ? SUGGESTIONS_KEY : null, () =>
    api.listSuggestions(),
  );
  return { list: enabled ? (res.data ?? null) : null, failed: enabled && res.error !== null };
}

/** After a write, every CRM read wakes: the mode (the rail), the list, the field map. */
function rereadAll(): void {
  invalidate("crm:");
}

function message(err: unknown, fallback: string): string {
  return suggestionError(err, fallback);
}

/**
 * The writes of a suggestion, with their toasts. A use shows Undo in its
 * toast (item 7). Each write reads every CRM key again.
 */
export function useSuggestionActions(listLabel: (slug: EntitySlug) => string) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  async function undo(o: { slot: OverlayInUse["slot"] }) {
    const label = listLabel(SLOT_SLUG[o.slot]);
    setBusy(o.slot);
    try {
      await api.undoSuggestion(o.slot);
      toast.show({ key: `crm-suggest:${o.slot}`, variant: "success", title: undoneLine(label) });
    } catch (err) {
      toast.show({ key: `crm-suggest:${o.slot}`, variant: "error", title: message(err, "Undo did not save.") });
    } finally {
      setBusy(null);
      rereadAll();
    }
  }

  async function apply(s: Suggestion): Promise<boolean> {
    const label = listLabel(SLOT_SLUG[s.slot]);
    setBusy(s.slot);
    try {
      await api.approveSuggestion(s.slot, s.version);
      toast.show({
        key: `crm-suggest:${s.slot}`,
        variant: "success",
        title: usedLine(s, label),
        action: { label: "Undo", onClick: () => void undo(s) },
      });
      return true;
    } catch (err) {
      toast.show({ key: `crm-suggest:${s.slot}`, variant: "error", title: message(err, "That did not save.") });
      return false;
    } finally {
      setBusy(null);
      rereadAll();
    }
  }

  async function dismiss(s: Suggestion) {
    const label = listLabel(SLOT_SLUG[s.slot]);
    setBusy(s.slot);
    try {
      await api.dismissSuggestion(s.slot, s.version);
      toast.show({ key: `crm-suggest:${s.slot}`, variant: "success", title: dismissedLine(s, label) });
    } catch (err) {
      toast.show({ key: `crm-suggest:${s.slot}`, variant: "error", title: message(err, "That did not save.") });
    } finally {
      setBusy(null);
      rereadAll();
    }
  }

  return { busy, apply, undo, dismiss };
}

/**
 * The records of the module, as the list would read them. It writes nothing.
 *
 * A table from `sm` up. Below it, at phone width, each record is a short
 * block of label and value, so the panel never scrolls sideways (Batch M
 * review, 375 px). The two draw the same rows and the same words.
 */
function PreviewTable({ preview }: { preview: OverlayPreview }) {
  const rows = preview.rows.slice(0, PREVIEW_SHOWN);
  const [first, ...rest] = preview.columns;
  return (
    <>
      <ul className="space-y-2 sm:hidden" aria-label="Records">
        {rows.map((row) => (
          <li key={row.id} className="min-w-0 rounded-lg border border-border px-3 py-2">
            {first ? (
              <p className="truncate text-xs font-medium text-foreground">
                {cellText(first, row.values[first])}
              </p>
            ) : null}
            <dl className="mt-1 space-y-0.5">
              {rest.map((column) => {
                const text = cellText(column, row.values[column]);
                return (
                  <div key={column} className="flex min-w-0 gap-2 text-xs">
                    <dt className="w-24 shrink-0 truncate text-muted-foreground">{columnName(column)}</dt>
                    <dd className="min-w-0 flex-1 truncate text-foreground" title={text}>
                      {text}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto rounded-lg border border-border sm:block">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-border">
              {preview.columns.map((column) => (
                <th
                  key={column}
                  className="whitespace-nowrap px-3 py-1.5 text-left font-medium text-muted-foreground"
                >
                  {columnName(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b border-border/60 last:border-b-0">
                {preview.columns.map((column) => {
                  const text = cellText(column, row.values[column]);
                  return (
                    <td key={column} className="whitespace-nowrap px-3 py-1.5 text-foreground">
                      <span className="block max-w-48 truncate" title={text}>
                        {text}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** A list of short lines under a small heading. */
function Lines({ title, lines }: { title: string; lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <section className="space-y-1">
      <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">{title}</h3>
      <ul className="space-y-0.5">
        {lines.map((line) => (
          <li key={line} className="text-xs text-foreground">
            {line}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The body of the panel: three facts, real records, the checks and what
 * changes. No hook, so a test reads its markup.
 */
export function SuggestionPanelBody({
  suggestion,
  listLabel,
  preview,
  previewFailed = false,
}: {
  suggestion: Suggestion;
  listLabel: string;
  preview: OverlayPreview | null;
  previewFailed?: boolean;
}) {
  const shown = preview ? Math.min(preview.rows.length, PREVIEW_SHOWN) : 0;
  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      <Lines title="What Metorite found" lines={facts(suggestion, listLabel)} />
      <section className="space-y-1.5">
        <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {preview
            ? `${shown} of ${preview.total.toLocaleString("en-IN")} records, as ${listLabel} would show them`
            : `Records, as ${listLabel} would show them`}
        </h3>
        {preview ? (
          <PreviewTable preview={preview} />
        ) : previewFailed ? (
          <p className="text-xs text-muted-foreground">{PREVIEW_UNREAD}</p>
        ) : (
          <Skeleton className="h-24 w-full" />
        )}
      </section>
      <Lines title="Check" lines={checks(suggestion, preview)} />
      <Lines title="What changes" lines={whatChanges(suggestion, listLabel)} />
    </div>
  );
}

/** The panel of one suggestion: the records first, then one action. */
export function SuggestionPanel({
  suggestion,
  listLabel,
  busy,
  onUse,
  onClose,
}: {
  suggestion: Suggestion;
  listLabel: string;
  busy: boolean;
  onUse: () => void;
  onClose: () => void;
}) {
  const res = useCachedResource<OverlayPreview>(
    `crm:suggestion-preview:${suggestion.version}:${suggestion.slot}`,
    () => api.previewSuggestion(suggestion.version, suggestion.slot),
  );
  return (
    <Modal
      open
      onClose={onClose}
      title={previewLabel(suggestion, listLabel)}
      icon="Eye"
      size="2xl"
      closeLabel="Close"
    >
      <SuggestionPanelBody
        suggestion={suggestion}
        listLabel={listLabel}
        preview={res.data ?? null}
        previewFailed={!res.data && res.error !== null}
      />
      <div className="flex flex-wrap justify-end gap-2 border-t border-border p-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
          Close
        </Button>
        <Button variant="primary" size="sm" icon="Check" loading={busy} disabled={busy} onClick={onUse}>
          {useLabel(suggestion, listLabel)}
        </Button>
      </div>
    </Modal>
  );
}

/**
 * The empty list of an admin, when Metorite has a suggestion for it: where
 * its records are, and "Preview Commercials as Deals".
 */
export function SlotSuggestion({
  suggestion,
  listLabel,
  onPreview,
}: {
  suggestion: Suggestion;
  listLabel: string;
  onPreview: () => void;
}) {
  return (
    <EmptyState
      icon="Sparkles"
      message={emptyLine(suggestion, listLabel)}
      hint="Look at real records first. Nothing changes until you choose."
      action={{ label: previewLabel(suggestion, listLabel), icon: "Eye", onClick: onPreview }}
    />
  );
}

/**
 * The suggestion of one list, with its panel, for the page. `null` with no
 * suggestion, so the list draws as before.
 */
export function SlotSuggestionArea({
  suggestion,
  listLabel,
  labelOf,
}: {
  suggestion: Suggestion;
  listLabel: string;
  labelOf: (slug: EntitySlug) => string;
}) {
  const [open, setOpen] = useState(false);
  const actions = useSuggestionActions(labelOf);
  return (
    <>
      <SlotSuggestion suggestion={suggestion} listLabel={listLabel} onPreview={() => setOpen(true)} />
      {open && (
        <SuggestionPanel
          suggestion={suggestion}
          listLabel={listLabel}
          busy={actions.busy === suggestion.slot}
          onClose={() => setOpen(false)}
          onUse={() => {
            void actions.apply(suggestion).then((done) => done && setOpen(false));
          }}
        />
      )}
    </>
  );
}

/** Settings → Suggestions, with its data. The page draws it for an admin in Mirror mode. */
export default function SuggestionsCard() {
  // The rail's label of each list: the source's names in Mirror mode.
  const labels = useSourceLabels(true);
  const labelOf = (slug: EntitySlug) => viewLabel(slug, labels);
  const { list, failed } = useSuggestions(true);
  const actions = useSuggestionActions(labelOf);
  // The slot only, never a copy of the suggestion: the panel draws the
  // suggestion of the newest read, so a write elsewhere cannot leave it on
  // an older draft (round 2 review P1).
  const [previewingSlot, setPreviewingSlot] = useState<Suggestion["slot"] | null>(null);
  const previewing = list?.suggestions.find((s) => s.slot === previewingSlot) ?? null;
  return (
    <>
      <SuggestionsCardView
        list={list}
        failed={failed && list === null}
        handlers={{
          listLabel: labelOf,
          busy: actions.busy,
          onPreview: (s) => setPreviewingSlot(s.slot),
          onDismiss: (s) => void actions.dismiss(s),
          onUndo: (o) => void actions.undo(o),
        }}
      />
      {previewing && (
        <SuggestionPanel
          suggestion={previewing}
          listLabel={labelOf(SLOT_SLUG[previewing.slot])}
          busy={actions.busy === previewing.slot}
          onClose={() => setPreviewingSlot(null)}
          onUse={() => {
            const s = previewing;
            void actions.apply(s).then((done) => done && setPreviewingSlot(null));
          }}
        />
      )}
    </>
  );
}
