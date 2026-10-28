"use client";

/**
 * "Move to Metorite" — the preview of the cutover (WS-53 CRM-Z10a, spec
 * `crm_platform.md` §3.4, D133).
 *
 * Self-contained on purpose: it reads its own data and takes no prop, so
 * CRM-U13a can move its one mount line into Connection > Advanced. It has NO
 * switch button. The switch comes in a later release (CRM-Z10b). The card
 * leads with one sentence, and the counts sit behind a fold.
 *
 * `CutoverCardView` only draws, so a test can render it.
 */

import Badge from "@/components/ui/Badge";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  SHOW_COUNTS,
  STATE_WORDS,
  SWITCH_LATER,
  ageWords,
  attention,
  formatNumber,
  getCutoverPreview,
  leadSentence,
  total,
  zohoCell,
  type CutoverPreview,
  type Split,
} from "../lib/cutover";

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

export function CutoverCardView({ preview }: { preview: CutoverPreview }) {
  const lines = attention(preview);
  const asOf = preview.sync.last_good_at
    ? `Last good sync ${ageWords(preview.sync.age_seconds)}. The Zoho counts are as of then.`
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
                    <td className={CELL}>{zohoCell(row)}</td>
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

      <footer className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
        {SWITCH_LATER}
      </footer>
    </section>
  );
}

/** The card with its read. It draws nothing until Zoho counts exist, or when the read is refused. */
export default function CutoverCard() {
  const { data } = useCachedResource<CutoverPreview>("crm:cutover-preview", getCutoverPreview);
  return data ? <CutoverCardView preview={data} /> : null;
}
