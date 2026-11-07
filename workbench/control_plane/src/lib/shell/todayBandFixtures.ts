/**
 * Fixtures for the Do first band tests (PKG-18a): a feed card and a feed answer.
 * Test support only. The product code never imports this file.
 */
import type { FeedAnswer, FeedCard, FeedGroupId } from "./feed";

export const card = (n: number, over: Partial<FeedCard> = {}): FeedCard => ({
  signal_id: `tasks:t${n}`,
  app: "tasks",
  kind: "task_due",
  folded: [],
  group: "now",
  score: 100 - n,
  title: `Card ${n}`,
  why: `Reason ${n}`,
  at: null,
  href: `/projects?task=${n}`,
  acts: [],
  ...over,
});

export const answerOf = (cards: FeedCard[]): FeedAnswer => {
  const groups: { id: FeedGroupId; count: number }[] = [];
  for (const c of cards) {
    const g = groups.find((x) => x.id === c.group);
    if (g) g.count += 1;
    else groups.push({ id: c.group, count: 1 });
  }
  return {
    cards,
    groups,
    handled: [],
    handled_ok: true,
    reminders_ahead: [],
    reminder_subjects: {},
    needs: null,
    sources: {},
    capped: [],
    fetchedAt: 0,
  };
};
