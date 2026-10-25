"use client";

/**
 * The right panel of a page (WS-61 KB-2d, `knowledge_base.md` §21.4 and
 * §21.7): the tabs Outline and Details.
 *
 * Outline lists the headings of the body, indented by level, and marks the
 * heading in view as the page scrolls. A click scrolls to the heading. The
 * tab is absent on a page with fewer than two headings, and Details shows
 * alone. Details shows the author, the last editor, the word and character
 * counts, the owner, the review date and the state. An overdue review
 * wears an "Overdue" badge. A writer of the space sees "Verify" on a
 * published page (KB-AI-4), which moves the review date forward.
 */

import { useState } from "react";

import { invalidate } from "@/lib/dataCache";
import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";

import Tabs from "@/components/Tabs";
import { PersonName } from "@/components/PersonName";
import Badge from "@/components/ui/Badge";

import { bylineOf } from "../lib/byline";
import { dayLabel, stateLabel, stateTone, STATE_LABELS } from "../lib/labels";
import { formatCount, showsOutline, type OutlineHeading, type TextStats } from "../lib/outline";
import { KNOWLEDGE_CACHE, KnowledgeError, verifyPage } from "../lib/api";
import { isOverdue } from "../lib/maintenance";
import type { PageDetail } from "../lib/types";

type PanelTab = "outline" | "details";

export default function PageSidePanel({
  page,
  headings,
  active,
  stats,
  onJump,
}: {
  page: Pick<
    PageDetail,
    "id" | "author_email" | "review_at" | "state" | "versions" | "owner_email" | "can_write"
  >;
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
  const toast = useToast();
  const [verifying, setVerifying] = useState(false);
  const overdue = isOverdue(page.review_at);
  const canVerify = Boolean(page.can_write) && page.state === "published";

  async function verify() {
    setVerifying(true);
    try {
      await verifyPage(page.id);
      toast.show({ key: `kb-verify:${page.id}`, variant: "success", title: "Page verified" });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: `kb-verify:${page.id}`, variant: "error", title: "Could not verify the page", description: reason });
    } finally {
      setVerifying(false);
      invalidate(KNOWLEDGE_CACHE);
    }
  }

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
          <Fact label="Words">{formatCount(stats.words)}</Fact>
          <Fact label="Characters">{formatCount(stats.characters)}</Fact>
          {page.state === "published" ? (
            <Fact label="Owner">
              {page.owner_email ? <PersonName email={page.owner_email} className="block" /> : "No owner"}
            </Fact>
          ) : null}
          <Fact label="Review date">
            <span className="inline-flex items-center justify-end gap-1.5">
              {overdue ? (
                <Badge tone="warning" icon="CalendarClock">
                  Overdue
                </Badge>
              ) : null}
              {page.review_at ? dayLabel(page.review_at) : "Set when the page is published"}
            </span>
          </Fact>
          <Fact label="State">
            <Badge tone={stateTone(page.state)} icon={STATE_LABELS[page.state]?.icon}>
              {stateLabel(page.state)}
            </Badge>
          </Fact>
        </dl>
      )}

      {tab === "details" && canVerify ? (
        <Button
          size="sm"
          variant="secondary"
          icon="ShieldCheck"
          className="w-full"
          loading={verifying}
          disabled={verifying}
          onClick={() => void verify()}
        >
          Verify
        </Button>
      ) : null}
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
