"use client";

/**
 * Page history: the versions of a page, one old version read-only, a compare
 * of two versions, and Restore (WS-61 KB-2g, `knowledge_base.md` §21.7).
 *
 * The layout follows the history of Docmost, drawn in our one look. A list
 * of versions sits on the left. The chosen version reads on the right,
 * marked against the version before it. A bar at the foot of the view holds
 * "Highlight changes", the count of changes and the arrows that move between
 * them. Compare asks for two versions and marks the newer against the older.
 * Restore asks first, and then writes the chosen version again as a new one
 * through the revert route, so nothing in the history is lost.
 *
 * On a phone the list gives way to two fields: the version, and the version
 * it compares with.
 *
 * ⚠️ `canRestore` comes from the page's `can_write`, and Restore hides
 * without it. Hiding is a courtesy, never the boundary: the gateway refuses a
 * revert from a reader with 403, and answers 404 under the history floor.
 */

import { useEffect, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import Modal from "@/components/ui/Modal";
import { SelectButton } from "@/components/ui/SelectButton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";

import { KNOWLEDGE_CACHE, KnowledgeError, revertPage } from "../../lib/api";
import {
  PREVIOUS_VERSION as PREVIOUS,
  authorName,
  comparePair,
  phoneFields,
  previousVersion,
  togglePick,
  versionDate,
  type ComparePair,
} from "../../lib/history";
import { changeCount, wordCounts, type Compare } from "../../lib/historyDiff";
import type { PageDetail } from "../../lib/types";
import HistoryList from "./HistoryList";
import HistoryView from "./HistoryView";

export default function PageHistory({
  open,
  onClose,
  page,
  canRestore,
  initialVersion = null,
  onNavigate,
}: {
  open: boolean;
  onClose: () => void;
  page: Pick<PageDetail, "id" | "title" | "versions">;
  /** True when the member may write the page now, and no edit is open. */
  canRestore: boolean;
  /** The version to show first. The newest when absent. */
  initialVersion?: number | null;
  /** A click on a link to a page of this app, in an old version. The modal closes first. */
  onNavigate: (href: string) => void;
}) {
  const toast = useToast();
  const versions = page.versions;
  const newest = versions[0]?.version ?? null;

  const [active, setActive] = useState<number | null>(initialVersion ?? newest);
  const [comparing, setComparing] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [pair, setPair] = useState<ComparePair | null>(null);
  // The phone's "Compare with": the version before, or one the member chose.
  const [against, setAgainst] = useState<string>(PREVIOUS);
  const [highlight, setHighlight] = useState(true);
  const [current, setCurrent] = useState(0);
  const [compare, setCompare] = useState<Compare | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const scrollerRef = useRef<HTMLDivElement>(null);

  // Each open starts again from the version asked for, with no compare.
  const [openedFor, setOpenedFor] = useState<boolean>(false);
  if (open !== openedFor) {
    setOpenedFor(open);
    if (open) {
      setActive(initialVersion ?? newest);
      setComparing(false);
      setPicked([]);
      setPair(null);
      setAgainst(PREVIOUS);
      setCurrent(0);
    }
  }

  // What the view draws: the pair of a compare, or the chosen version
  // against the one before it (or the one the phone's field names).
  const shown = pair?.newer ?? active ?? newest;
  const base =
    pair?.older ??
    (shown === null ? null : against === PREVIOUS ? previousVersion(versions, shown) : Number(against) || null);

  // A new compare starts at its first change.
  const total = highlight ? (compare?.changes.length ?? 0) : 0;
  const [countedFor, setCountedFor] = useState<Compare | null>(null);
  if (compare !== countedFor) {
    setCountedFor(compare);
    setCurrent(compare && compare.changes.length ? 1 : 0);
  }

  // The arrows bring the current change into view.
  useEffect(() => {
    if (!current) return;
    const el = scrollerRef.current?.querySelector(`[data-diff-index="${current}"]`);
    el?.scrollIntoView({ block: "center" });
  }, [current, shown, base, highlight]);

  function step(by: number) {
    if (!total) return;
    setCurrent((c) => ((((c || 1) - 1 + by) % total) + total) % total + 1);
  }

  // The list on the desktop shows no "Compare with", so a pick there reads
  // the version against the one before it.
  function select(n: number) {
    setPair(null);
    setActive(n);
    setAgainst(PREVIOUS);
  }

  // The phone's two fields. The newer version is on show, as on the desktop.
  function pickPhone(version: number, compareWith: string) {
    const next = phoneFields(version, compareWith);
    setPair(null);
    setActive(next.active);
    setAgainst(next.against);
  }

  function startCompare(n: number) {
    setPair(null);
    setComparing(true);
    setPicked([n]);
  }

  function confirmCompare() {
    const next = comparePair(picked);
    if (!next) return;
    setPair(next);
    setComparing(false);
    setPicked([]);
  }

  async function restore(n: number) {
    setBusy(true);
    try {
      await revertPage(page.id, n);
      toast.show({ key: "kb-revert", variant: "success", title: `Restored version ${n}` });
      setRestoring(null);
      onClose();
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: "kb-revert", variant: "error", title: "Could not restore the version", description: reason });
      setRestoring(null);
    } finally {
      setBusy(false);
      invalidate(KNOWLEDGE_CACHE);
    }
  }

  const label = (n: number | null) => {
    const v = versions.find((x) => x.version === n);
    return v ? `version ${v.version} (${versionDate(v.created_at)})` : `version ${n}`;
  };
  const restoreTarget = shown !== null && shown !== newest ? shown : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Page history"
      description={page.title}
      icon="History"
      size="6xl"
      className="h-[min(90dvh,48rem)]"
    >
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Page versions"
          className="hidden w-64 shrink-0 flex-col border-r border-border md:flex lg:w-72"
        >
          <div className="min-h-0 flex-1 overflow-y-auto">
            {versions.length ? (
              <HistoryList
                versions={versions}
                active={shown}
                comparing={comparing}
                picked={picked}
                canRestore={canRestore}
                onSelect={select}
                onTogglePick={(n) => setPicked((p) => togglePick(p, n))}
                onStartCompare={startCompare}
                onRestore={(n) => {
                  select(n);
                  setRestoring(n);
                }}
              />
            ) : (
              <p className="p-3 text-xs text-muted-foreground">No versions saved yet.</p>
            )}
          </div>
          {comparing ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-border p-2">
              <span className="w-full text-xs text-muted-foreground">
                Pick two versions to compare. {picked.length} of 2 picked.
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setComparing(false);
                  setPicked([]);
                }}
              >
                Cancel
              </Button>
              <Button size="sm" icon="GitCompareArrows" disabled={picked.length !== 2} onClick={confirmCompare}>
                Compare
              </Button>
            </div>
          ) : canRestore ? (
            <div className="flex items-center gap-2 border-t border-border p-2">
              <Button variant="secondary" size="sm" onClick={onClose}>
                Close
              </Button>
              <Button
                size="sm"
                icon="RotateCcw"
                disabled={restoreTarget === null}
                onClick={() => restoreTarget !== null && setRestoring(restoreTarget)}
              >
                {restoreTarget === null ? "Restore" : `Restore version ${restoreTarget}`}
              </Button>
            </div>
          ) : null}
        </nav>

        <section aria-label="The chosen version" className="flex min-w-0 flex-1 flex-col">
          {/* The phone: two fields in place of the list. */}
          <div className="flex flex-wrap items-end gap-2 border-b border-border p-2 md:hidden">
            <div className="min-w-0 flex-1 basis-40">
              <span className="mb-1 block text-xs text-muted-foreground">Version</span>
              <SelectButton
                label="Version"
                widthClass="w-full"
                value={shown === null ? "" : String(shown)}
                options={versions.map((v) => ({
                  value: String(v.version),
                  label: `v${v.version} · ${versionDate(v.created_at)}`,
                  hint: authorName(v.author_email),
                }))}
                onChange={(value) => pickPhone(Number(value), pair ? String(pair.older) : against)}
              />
            </div>
            <div className="min-w-0 flex-1 basis-40">
              <span className="mb-1 block text-xs text-muted-foreground">Compare with</span>
              <SelectButton
                label="Compare with"
                widthClass="w-full"
                value={pair ? String(pair.older) : against}
                defaultValue={PREVIOUS}
                options={[
                  { value: PREVIOUS, label: "The version before" },
                  ...versions
                    .filter((v) => v.version !== shown)
                    .map((v) => ({ value: String(v.version), label: `v${v.version} · ${versionDate(v.created_at)}` })),
                ]}
                onChange={(value) => shown !== null && pickPhone(shown, value)}
              />
            </div>
          </div>

          {pair ? (
            <div className="flex items-center gap-2 border-b border-border px-4 py-1.5">
              <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                Comparing {label(pair.newer)} with {label(pair.older)}
              </p>
              <Button variant="ghost" size="icon-sm" icon="X" aria-label="Stop the compare" onClick={() => setPair(null)} />
            </div>
          ) : null}
          {compare?.coarse && highlight ? (
            <p className="border-b border-border bg-warning/10 px-4 py-1.5 text-xs text-foreground">
              These versions differ too much to mark each word. The whole changed part shows as one change.
            </p>
          ) : null}

          {/* The bar floats over the foot of the reader, never over Restore. */}
          <div className="relative flex min-h-0 flex-1 flex-col">
            <div ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto px-4 pb-24 pt-4 sm:px-8 lg:px-10">
              {shown === null ? (
                <p className="text-sm text-muted-foreground">No versions saved yet.</p>
              ) : (
                <HistoryView
                  pageId={page.id}
                  version={shown}
                  against={base}
                  highlight={highlight}
                  current={current}
                  onCompare={setCompare}
                  onNavigate={(href) => {
                    onClose();
                    onNavigate(href);
                  }}
                />
              )}
            </div>

            {base !== null && shown !== null ? (
              <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-3 md:bottom-4">
                <div className="pointer-events-auto flex max-w-full flex-wrap items-center justify-center gap-x-3 gap-y-1 rounded-full border border-border bg-popover px-4 py-1.5 shadow-lg">
                  <label className="flex cursor-pointer select-none items-center gap-2 whitespace-nowrap text-sm text-foreground">
                    <Checkbox checked={highlight} onChange={(e) => setHighlight(e.target.checked)} />
                    Highlight changes
                  </label>
                  {highlight && compare ? (
                    total ? (
                      <span className="flex items-center gap-1">
                        <span
                          className="whitespace-nowrap text-sm tabular-nums text-muted-foreground"
                          aria-live="polite"
                          title={wordCounts(compare)}
                        >
                          {current} of {changeCount(total)}
                        </span>
                        <Button variant="ghost" size="icon-xs" icon="ChevronUp" aria-label="The change before" onClick={() => step(-1)} />
                        <Button variant="ghost" size="icon-xs" icon="ChevronDown" aria-label="The next change" onClick={() => step(1)} />
                      </span>
                    ) : (
                      <span className="whitespace-nowrap text-sm text-muted-foreground">No changes</span>
                    )
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>

          {canRestore ? (
            <div className="flex items-center justify-end gap-2 border-t border-border p-2 md:hidden">
              <Button variant="secondary" size="sm" onClick={onClose}>
                Close
              </Button>
              <Button
                size="sm"
                icon="RotateCcw"
                disabled={restoreTarget === null}
                onClick={() => restoreTarget !== null && setRestoring(restoreTarget)}
              >
                {restoreTarget === null ? "Restore" : `Restore version ${restoreTarget}`}
              </Button>
            </div>
          ) : null}
        </section>
      </div>
      {/* Inside the dialog, so the confirm nests in it and keeps the focus. */}
      <ConfirmDialog
        open={restoring !== null}
        title={restoring !== null ? `Restore version ${restoring}?` : "Restore"}
        subject={versions.find((v) => v.version === restoring)?.title ?? null}
        body={
          restoring !== null
            ? `This writes the title and the body of version ${restoring} as a new version. The versions after it stay in the history.`
            : ""
        }
        confirmLabel={restoring !== null ? `Restore version ${restoring}` : "Restore"}
        confirmVariant="primary"
        icon="RotateCcw"
        busy={busy}
        onConfirm={() => restoring !== null && void restore(restoring)}
        onCancel={() => setRestoring(null)}
      />
    </Modal>
  );
}
