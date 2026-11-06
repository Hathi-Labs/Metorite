"use client";

/**
 * The tab bar of a record card (D181, `specs/one_record_card.md` §3.3).
 *
 * ONE bar for each card that shows one record: the CRM record (Overview,
 * Timeline, Related, Fields) and the task card (Overview, Related, Files,
 * Discussion). ORC-3 lifted it from `crm/components/RecordShell.tsx`, so the
 * two cards cannot drift into two looks. It draws the shared `Tabs`, inline
 * and underlined. A card that wants tabs draws this, and never its own bar.
 *
 * - `count` shows beside the word. The caller passes it only when the tab
 *   holds something, so an empty tab reads as a word alone.
 * - `pinned` keeps the bar at the top of a card that scrolls as one region
 *   (the task card), so the tabs stay in reach under a long Overview. The CRM
 *   card puts the bar outside its scroll region and does not need it.
 *
 * Fence: `app/projects/lib/taskTabs.test.ts` ("one tab bar").
 */

import Tabs from "@/components/Tabs";

export interface RecordTabDef<T extends string> {
  id: T;
  label: string;
  /** The number of things on the tab. Leave it out for none. */
  count?: number;
  /** The CRM's Timeline tab is a guide anchor (WS-OB GD-10b). */
  guideAnchor?: boolean;
}

export default function RecordTabBar<T extends string>({
  tabs,
  active,
  onTab,
  pinned = false,
}: {
  tabs: readonly RecordTabDef<T>[];
  active: T;
  onTab: (tab: T) => void;
  pinned?: boolean;
}) {
  return (
    <div
      className={`shrink-0 border-b border-border px-2 ${
        pinned ? "sticky top-0 z-10 bg-card" : ""
      }`}
      data-record-tab-bar=""
    >
      <Tabs
        chrome="inline"
        variant="underline"
        tabs={tabs.map((t) => ({
          id: t.id,
          label: t.label,
          count: t.count,
          guideAnchor: t.guideAnchor,
        }))}
        activeTab={active}
        onTabChange={(t) => onTab(t as T)}
      />
    </div>
  );
}
