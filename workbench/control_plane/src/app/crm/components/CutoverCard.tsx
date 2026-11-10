"use client";

/**
 * "Move to Metorite" — the preview of the cutover (WS-53 CRM-Z10a) and the
 * switch (CRM-Z10b), spec `crm_platform.md` §3.4, D133.
 *
 * Self-contained on purpose: it reads its own data and takes the mode only,
 * so CRM-U13a can move its one mount line into Connection > Advanced. The
 * card leads with one sentence, and the counts sit behind a fold.
 *
 * - **Mirror mode.** The preview. When the org is on the cutover list
 *   (`can_switch`), the last part holds the two switches of D133 (as
 *   amended 2026-10-29), each with one line about the records under it. The
 *   one that keeps the records is the primary button, and it opens
 *   `CutoverDialog`. The one that starts empty has the danger tone, and it
 *   opens the start-over dialog of the hub (`onStartEmpty`). The hub then
 *   leaves its own copy out (`switchesInCard`). Both labels come from
 *   `lib/setup.ts`.
 * - **Native mode.** Nothing, unless the switch is done (`cutover_done` of
 *   `GET /crm/mode`) and Zoho is still connected (phase 3 did not finish).
 *   Then "Finish closing Zoho" reads the status, and posts the switch again
 *   only when it says `done`. The gateway then closes Zoho only. A Native
 *   CRM that never switched reads no status.
 *
 * The dialog lives in the card itself, so a mode that changes under it does
 * not close it. `CutoverCardView` and `FinishClosingView` only draw, so a test
 * can render them.
 */

import { Component, useState, type ReactNode } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import type { CrmMode } from "../lib/connect";
import {
  SHOW_COUNTS,
  STATE_WORDS,
  SWITCH_OFF,
  SWITCH_READY,
  ageWords,
  attention,
  formatNumber,
  getCutoverPreview,
  isPreview,
  leadSentence,
  total,
  zohoCell,
  closingPending,
  getCutoverStatus,
  switchToMetorite,
  switchesInCard,
  type CutoverPreview,
  type CutoverStatus,
  type Split,
} from "../lib/cutover";
import { SWITCH_LABEL, finishClosing } from "../lib/cutoverSwitch";
import { ACTIONS, KEEP_RECORDS_SWITCH } from "../lib/setup";
import { useCutoverDone } from "../lib/useCrmMode";
import CutoverDialog from "./CutoverDialog";

/** The switch of the hub that removes the records (CRM-X0). */
const START_EMPTY = ACTIONS["switch-to-metorite"];

const CELL = "px-4 py-1.5 text-right tabular-nums";

function Counts({ split }: { split: Split }) {
  return (
    <>
      <td className={CELL}>{formatNumber(split.from_zoho)}</td>
      <td className={CELL}>{formatNumber(split.native)}</td>
    </>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{children}</h3>;
}

export function CutoverCardView({
  preview,
  onSwitch,
  onStartEmpty,
}: {
  preview: CutoverPreview;
  /** Opens the dialog of the switch that keeps the records. Shown only when the org may switch. */
  onSwitch?: () => void;
  /** Opens the start-over dialog of the hub, which removes the records. */
  onStartEmpty?: () => void;
}) {
  const lines = attention(preview);
  const asOf = preview.sync.last_good_at
    ? `Last good sync ${ageWords(preview.sync.age_seconds)}. Each Zoho count shows when it was taken.`
    : null;

  return (
    <section
      id="crm-cutover"
      className="rounded-xl border border-border bg-card/40"
      aria-label="Move to Metorite"
    >
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Move to Metorite</h2>
        <p className="mt-0.5 text-xs text-foreground">{leadSentence(preview)}</p>
        {asOf && <p className="mt-0.5 text-[11px] text-muted-foreground">{asOf}</p>}
      </header>

      {lines.length > 0 && (
        <ul className="divide-y divide-border/60 border-b border-border">
          {lines.map((line) => (
            <li key={line} className="flex items-start gap-2 px-4 py-2 text-xs text-foreground">
              <Badge tone="warning">Check</Badge>
              <span className="min-w-0 flex-1">{line}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-3 px-4 py-3">
        <CollapsibleSection label={SHOW_COUNTS} defaultOpen={false}>
          <div className="overflow-x-auto pt-2">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  <th className="px-4 py-1 text-left font-medium">What</th>
                  <th className={`${CELL} font-medium`}>In Zoho</th>
                  <th className={`${CELL} font-medium`}>From Zoho, in Metorite</th>
                  <th className={`${CELL} font-medium`}>Made in Metorite</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {preview.records.map((row) => (
                  <tr key={row.key}>
                    <th scope="row" className="px-4 py-1.5 text-left font-normal text-foreground">
                      {row.label}
                      {row.state !== "complete" && (
                        <span className="ml-2">
                          <Badge tone="warning">{STATE_WORDS[row.state]}</Badge>
                        </span>
                      )}
                    </th>
                    <td className={CELL}>
                      {zohoCell(row)}
                      {row.zoho !== null && row.zoho_age_seconds !== null && (
                        <span className="block text-[10px] text-muted-foreground">
                          as of {ageWords(row.zoho_age_seconds)}
                        </span>
                      )}
                    </td>
                    <Counts split={row.metorite} />
                  </tr>
                ))}
                {preview.other.map((row) => (
                  <tr key={row.key}>
                    <th scope="row" className="px-4 py-1.5 text-left font-normal text-foreground">
                      {row.label}
                    </th>
                    <td className={CELL}>—</td>
                    <Counts split={row.metorite} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="px-4 pt-1 text-[11px] text-muted-foreground">
            Activities in Metorite:{" "}
            {preview.records
              .filter((row) => row.parts.length > 0)
              .flatMap((row) => row.parts)
              .map((part) => `${formatNumber(total(part.metorite))} ${part.label.toLowerCase()}`)
              .join(" · ") || "none"}
          </p>
        </CollapsibleSection>

        {preview.stays_in_zoho.length > 0 && (
          <CollapsibleSection label="Stays in Zoho" defaultOpen={false}>
            <p className="px-4 pt-2 text-xs text-foreground">{preview.stays_in_zoho.join(", ")}.</p>
          </CollapsibleSection>
        )}

        {preview.labels_kept.length > 0 && (
          <div>
            <Heading>Names you keep</Heading>
            <p className="mt-1 text-xs text-foreground">
              {preview.labels_kept.map((label) => label.label).join(" · ")}. You can rename them later.
            </p>
          </div>
        )}

        {preview.unmatched_owners.length > 0 && (
          <div>
            <Heading>Zoho owners with no Metorite member</Heading>
            <ul className="mt-1 text-xs text-foreground">
              {preview.unmatched_owners.map((owner) => (
                <li key={owner.name} className="flex justify-between gap-3 py-0.5">
                  <span className="min-w-0 break-words">{owner.name}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {formatNumber(owner.records)} records
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {switchesInCard(preview) && onSwitch ? (
        <footer className="border-t border-border px-4 py-3">
          <p className="text-[11px] text-muted-foreground">{SWITCH_READY}</p>
          <ul className="mt-2 flex flex-col gap-3" aria-label="Choose how to switch">
            <li className="flex flex-col items-start gap-1">
              <Button variant="primary" size="sm" icon="ArrowRightLeft" onClick={onSwitch}>
                {SWITCH_LABEL}
              </Button>
              <p className="text-[11px] text-muted-foreground">{KEEP_RECORDS_SWITCH.explain}</p>
            </li>
            {onStartEmpty && (
              <li className="flex flex-col items-start gap-1">
                <Button variant="destructive" size="sm" onClick={onStartEmpty}>
                  {START_EMPTY.label}
                </Button>
                <p className="text-[11px] text-muted-foreground">{START_EMPTY.explain}</p>
              </li>
            )}
          </ul>
        </footer>
      ) : (
        <footer className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
          {SWITCH_OFF}
        </footer>
      )}
    </section>
  );
}

/** "Finish closing Zoho": the switch is done and Zoho is still connected. */
export function FinishClosingView({
  busy,
  error,
  done,
  onFinish,
}: {
  busy: boolean;
  error: string | null;
  /** The lines of the result, after the call. */
  done: string[] | null;
  onFinish: () => void;
}) {
  return (
    <section
      id="crm-cutover"
      className="rounded-xl border border-border bg-card/40"
      aria-label="Finish closing Zoho"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">Zoho is still connected</h2>
          <p className="mt-0.5 text-xs text-foreground">
            Metorite is your CRM now. Finish closing Zoho, so that Metorite holds no Zoho access.
          </p>
        </div>
        {!done && (
          <Button variant="primary" size="sm" loading={busy} disabled={busy} onClick={onFinish}>
            Finish closing Zoho
          </Button>
        )}
      </div>
      {(error || done) && (
        <div role={error ? "alert" : "status"} className="border-t border-border px-4 py-2">
          {error && <p className="text-xs text-destructive">{error}</p>}
          {done?.map((line) => (
            <p key={line} className="text-xs text-foreground">
              {line}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Nothing in the card may take Setup down. A throw draws `fallback`, which is
 * nothing unless the card held the hub's start-empty switch.
 */
export class CutoverBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? (this.props.fallback ?? null) : this.props.children;
  }
}

/**
 * The hub's start-empty switch, when the card failed to draw (verifier P3-4).
 * The hub leaves that switch to the card while the card shows both switches.
 * A card that throws must not take it off the screen until a reload.
 */
export function StartEmptyFallback({ onStartEmpty }: { onStartEmpty: () => void }) {
  return (
    <section
      id="crm-cutover"
      className="rounded-xl border border-border bg-card/40"
      aria-label="Move to Metorite"
    >
      <div className="flex flex-wrap items-center gap-3 px-4 py-2">
        <p className="min-w-0 flex-1 basis-60 text-[11px] text-muted-foreground">
          {START_EMPTY.explain}
        </p>
        <Button variant="secondary" size="sm" onClick={onStartEmpty}>
          {START_EMPTY.label}
        </Button>
      </div>
    </section>
  );
}

/** The cache key of the preview. `SetupHub` reads the same key (`switchesInCard`). */
export const PREVIEW_KEY = "crm:cutover-preview";

function CutoverRead({
  onSwitch,
  onStartEmpty,
}: {
  onSwitch: (preview: CutoverPreview) => void;
  onStartEmpty: () => void;
}) {
  const { data } = useCachedResource<CutoverPreview>(PREVIEW_KEY, getCutoverPreview);
  return isPreview(data) ? (
    <CutoverCardView preview={data} onSwitch={() => onSwitch(data)} onStartEmpty={onStartEmpty} />
  ) : null;
}

/** Native mode, after the switch: the card shows only while phase 3 waits. A 404 draws nothing. */
function FinishClosingRead() {
  const { data } = useCachedResource<CutoverStatus>("crm:cutover-status", getCutoverStatus);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string[] | null>(null);
  if (!done && !closingPending(data)) return null;

  async function finish() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await finishClosing({ status: getCutoverStatus, commit: switchToMetorite });
      if ("lines" in out) setDone(out.lines);
      else setError(out.error);
    } finally {
      setBusy(false);
      invalidate("crm:cutover-status");
    }
  }

  return <FinishClosingView busy={busy} error={error} done={done} onFinish={() => void finish()} />;
}

/**
 * The card with its reads. In Mirror mode it draws nothing until Zoho counts
 * exist, when the read is refused, or when the body is not a preview. In
 * Native mode it reads the status only after the switch (F3).
 */
export default function CutoverCard({
  mode,
  onStartEmpty,
  holdsStartEmpty = false,
}: {
  mode: CrmMode;
  /** Opens the start-over dialog of the hub with `ACTIONS["switch-to-metorite"]`. */
  onStartEmpty: () => void;
  /**
   * The hub left its start-empty switch to this card (`switchesInCard`). A
   * card that throws then draws that switch alone (`StartEmptyFallback`).
   */
  holdsStartEmpty?: boolean;
}) {
  // The preview the dialog opened with. The dialog keeps it as a snapshot.
  const [opened, setOpened] = useState<CutoverPreview | null>(null);
  const switched = useCutoverDone(mode === "native");
  return (
    <CutoverBoundary
      fallback={holdsStartEmpty ? <StartEmptyFallback onStartEmpty={onStartEmpty} /> : null}
    >
      {mode === "native"
        ? switched && <FinishClosingRead />
        : <CutoverRead onSwitch={setOpened} onStartEmpty={onStartEmpty} />}
      {opened && <CutoverDialog open preview={opened} onClose={() => setOpened(null)} />}
    </CutoverBoundary>
  );
}
