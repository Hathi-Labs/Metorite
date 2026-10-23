"use client";

/**
 * The Gaps view (WS-61 KB-9a, `knowledge_base.md` §8.4 door 3 and §11.1):
 * `/knowledge/gaps`.
 *
 * The questions that the assistant asked Knowledge for members and found
 * no page for, the most asked first. A manager of Knowledge sees each one
 * of the org, and a member sees the ones the member asked. Each row has
 * "Write a page", which opens the one New page form with the question as
 * the title. The layout marks the gap answered with the new page. A manager
 * also has Dismiss.
 *
 * `?write=<id>` opens the form for that gap on arrival. The My Day card
 * `kb_gap` links here so (`gateway/routes/knowledge/gaps.write_href`). The
 * parameter leaves the address once the form opens, so a reload does not
 * open it again.
 *
 * A 404 of the read means gaps are off for the org (`KNOWLEDGE_GAPS`), and
 * `listGaps` gives it as `off`.
 */

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { KNOWLEDGE_CACHE, KnowledgeError } from "../lib/api";
import {
  GAPS_KEY,
  KNOWLEDGE_GAPS,
  WRITE_PARAM,
  askersLabel,
  dismissGap,
  listGaps,
  titleFromQuestion,
  type Gap,
  type GapList,
} from "../lib/gaps";
import { KNOWLEDGE_HOME } from "../lib/links";
import { useKnowledge } from "./KnowledgeContext";

export default function GapsView() {
  const { openNewPage, requestLeave } = useKnowledge();
  const router = useRouter();
  const pathname = usePathname() ?? KNOWLEDGE_GAPS;
  const params = useSearchParams();
  const toast = useToast();
  const read = useCachedResource<GapList>(GAPS_KEY, listGaps);
  const [dismissing, setDismissing] = useState<string | null>(null);

  const write = (gap: Gap) =>
    requestLeave(() =>
      openNewPage({ title: titleFromQuestion(gap.question), gap: { id: gap.id, question: gap.question } }),
    );

  // `?write=<id>`: open the form once, when the list holds the gap.
  const wanted = params?.get(WRITE_PARAM) ?? null;
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!wanted || handled.current === wanted || !read.data) return;
    handled.current = wanted;
    router.replace(pathname);
    const gap = read.data.gaps.find((g) => g.id === wanted);
    if (gap) {
      openNewPage({ title: titleFromQuestion(gap.question), gap: { id: gap.id, question: gap.question } });
    } else {
      toast.show({
        key: `kb-gap:${wanted}`,
        title: "That question is closed",
        description: "Somebody answered or dismissed it, or it is not yours to see.",
      });
    }
  }, [wanted, read.data, router, pathname, openNewPage, toast]);

  async function dismiss(gap: Gap) {
    setDismissing(gap.id);
    try {
      await dismissGap(gap.id);
      invalidate(KNOWLEDGE_CACHE);
      toast.show({ key: `kb-gap:${gap.id}`, title: "Dismissed the question" });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: `kb-gap:${gap.id}`, variant: "error", title: "Could not dismiss the question", description: reason });
    } finally {
      setDismissing(null);
    }
  }

  const list = read.data;
  const manager = list?.scope === "org";
  const rows = list?.gaps ?? [];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Icon name="MessageCircleQuestion" size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-foreground">Gaps</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {manager || !list || list.off
                ? "Questions the assistant could not answer from Knowledge, the most asked first."
                : "Questions you asked that Knowledge could not answer."}
            </p>
          </div>
        </header>

        <section className="mt-5" aria-label="Open questions">
          {read.loading ? (
            <SkeletonRows count={3} />
          ) : list?.off ? (
            <EmptyState
              icon="PowerOff"
              message="Gaps are not on for your organization yet."
              hint="When they are on, a question that Knowledge cannot answer shows here."
              action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => router.push(KNOWLEDGE_HOME) }}
            />
          ) : read.error && !list ? (
            <EmptyState
              icon="CloudOff"
              message="The questions did not load."
              hint="The server did not answer. Try again in a moment."
              action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              icon="CircleCheck"
              message="No open questions."
              hint="When the assistant finds nothing in Knowledge for a question, the question shows here."
            />
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border bg-card">
              {rows.map((gap) => (
                <li
                  key={gap.id}
                  className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3"
                  data-gap-id={gap.id}
                >
                  <span className="flex min-w-0 flex-1 items-start gap-3">
                    <Icon name="MessageCircleQuestion" size={16} className="mt-0.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <span className="block break-words text-sm text-foreground">{gap.question}</span>
                      <span className="block text-xs text-muted-foreground">
                        {askersLabel(gap.ask_count)}
                        {gap.last_asked_at ? ` · last asked ${relativeTime(gap.last_asked_at) || "now"}` : ""}
                        {gap.asked_by_me && manager ? " · you asked it too" : ""}
                      </span>
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2 pl-7 sm:pl-0">
                    <Button
                      size="sm"
                      variant="secondary"
                      icon="FilePlus"
                      aria-label={`Write a page for: ${gap.question}`}
                      onClick={() => write(gap)}
                    >
                      Write a page
                    </Button>
                    {manager ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon="X"
                        loading={dismissing === gap.id}
                        disabled={dismissing !== null}
                        aria-label={`Dismiss: ${gap.question}`}
                        onClick={() => void dismiss(gap)}
                      >
                        Dismiss
                      </Button>
                    ) : null}
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
