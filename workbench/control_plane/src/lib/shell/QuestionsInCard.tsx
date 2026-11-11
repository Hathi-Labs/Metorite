"use client";

/**
 * EA-18e (task 2964, D100 and D101). The agent questions, answered inside the
 * My Day card. The list is `QuestionsView` in its `embedded` form, the same
 * one the Approvals tab draws: "For me" only, the same `AgentQuestionCard`
 * (so the same `retryToken` rule), and the same two writes (`/activities`, or
 * `archive-approval` for a typed archive request). This file adds no rule and
 * no write. `may_answer_run` still decides: the server sends `can_answer`.
 *
 * "Open in Projects" stays as a link, for a member who wants the whole list.
 */

import { useRouter } from "next/navigation";

import { QuestionsView } from "@/app/projects/components/QuestionsView";
import { AGENT_QUESTIONS_HREF } from "./feed";

export default function QuestionsInCard({ onChanged }: { onChanged?: () => void }) {
  const router = useRouter();
  return (
    <div
      data-feed-expanded="questions"
      className="mt-2 flex basis-full flex-col rounded-lg border border-border bg-card/50"
    >
      <QuestionsView
        embedded
        projects={[]}
        onOpenTask={(id) => router.push(`/projects?task=${encodeURIComponent(id)}`)}
        onOpenProject={(id) => router.push(`/projects?project=${encodeURIComponent(id)}`)}
        onChanged={onChanged}
      />
      <a
        href={AGENT_QUESTIONS_HREF}
        className="px-3 pb-2 text-xs text-primary hover:underline"
        data-feed-expanded-full=""
      >
        Open in Projects
      </a>
    </div>
  );
}
