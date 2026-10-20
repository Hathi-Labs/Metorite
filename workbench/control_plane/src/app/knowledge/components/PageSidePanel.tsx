"use client";

/**
 * The right panel of a page (WS-61 KB-2d, `knowledge_base.md` §21.4 and
 * §21.7): the tabs Outline and Details.
 *
 * Outline lists the headings of the body, indented by level, and marks the
 * heading in view as the page scrolls. A click scrolls to the heading. The
 * tab is absent on a page with fewer than two headings, and Details shows
 * alone. Details shows the author, the last editor, the word and character
 * counts, the review date and the state.
 */

import { useState } from "react";

import Tabs from "@/components/Tabs";
import { PersonName } from "@/components/PersonName";
import Badge from "@/components/ui/Badge";

import { bylineOf } from "../lib/byline";
import { dayLabel, stateLabel, stateTone, STATE_LABELS } from "../lib/labels";
import { countLabel, showsOutline, type OutlineHeading, type TextStats } from "../lib/outline";
import type { PageDetail } from "../lib/types";

type PanelTab = "outline" | "details";

export default function PageSidePanel({
  page,
  headings,
  active,
  stats,
  onJump,
}: {
  page: Pick<PageDetail, "author_email" | "review_at" | "state" | "versions">;
  headings: readonly OutlineHeading[];
  active: number;
  stats: TextStats;
  onJump: (index: number) => void;
}) {
  const hasOutline = showsOutline(headings);
  // The member's pick. With no pick, the outline shows when there is one.
  const [picked, setPicked] = useState<PanelTab | null>(null);
  const tab: PanelTab = hasOutline ? (picked ?? "outline") : "details";
  const lastEditor = bylineOf(page.versions)?.email ?? null;

  return (
    <section aria-label="Page outline and details" className="space-y-3">
      {hasOutline ? (
        <Tabs
          chrome="inline"
          tabs={[
            { id: "outline", label: "Outline", icon: "ListTree" },
            { id: "details", label: "Details", icon: "Info" },
          ]}
          activeTab={tab}
          onTabChange={(id) => setPicked(id as PanelTab)}
        />
      ) : (
        <h3 className="text-xs font-medium text-foreground">Details</h3>
      )}

      {tab === "outline" ? (
        <nav aria-label="Outline">
          <ol className="space-y-0.5">
            {headings.map((h, i) => (
              <li key={h.slug}>
                <button
                  type="button"
                  onClick={() => onJump(i)}
                  aria-current={i === active ? "location" : undefined}
                  style={{ paddingLeft: `${0.5 + (Math.min(h.level, 4) - 1) * 0.75}rem` }}
                  className={`block w-full truncate rounded-md border-l-2 py-1 pr-2 text-left text-xs tech-transition ${
                    i === active
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
                  }`}
                >
                  {h.text}
                </button>
              </li>
            ))}
          </ol>
        </nav>
      ) : (
        <dl className="space-y-2 text-xs">
          <Fact label="Author">
            <PersonName email={page.author_email} className="block" />
          </Fact>
          <Fact label="Last edited by">
            {lastEditor ? <PersonName email={lastEditor} className="block" /> : "Nobody yet"}
          </Fact>
          <Fact label="Words">{countLabel(stats.words, "word", "words")}</Fact>
          <Fact label="Characters">{countLabel(stats.characters, "character", "characters")}</Fact>
          <Fact label="Review date">{page.review_at ? dayLabel(page.review_at) : "Set when the page is published"}</Fact>
          <Fact label="State">
            <Badge tone={stateTone(page.state)} icon={STATE_LABELS[page.state]?.icon}>
              {stateLabel(page.state)}
            </Badge>
          </Fact>
        </dl>
      )}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right text-foreground">{children}</dd>
    </div>
  );
}
