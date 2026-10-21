"use client";

/**
 * The space switcher at the top of the Knowledge rail (WS-61 KB-2f,
 * `knowledge_base.md` §21.7).
 *
 * Docmost opens a space from a searchable list at the top of its sidebar.
 * Ours is the shared `SelectButton` with its filter box, so it has the look
 * and the keys of every other list button in the product. It shows the
 * space on screen, or "All spaces" on the home. A pick goes to the space
 * through the caller's `onNavigate`, so the leave guard of an unsaved page
 * still asks first.
 *
 * ⚠️ Props only, no context, for the reason `KnowledgeRail` gives: on a
 * phone the rail draws in the shell's drawer, outside the Knowledge layout.
 */

import { SelectButton, type SelectOption } from "@/components/ui/SelectButton";

import { pageCount } from "../lib/labels";
import { KNOWLEDGE_HOME, spaceHref } from "../lib/links";
import type { Space } from "../lib/types";

/** The value of the "All spaces" choice: the home. */
export const ALL_SPACES = "";

/** The options of the switcher: the home, then each space. Pure. */
export function switcherOptions(spaces: readonly Space[]): SelectOption[] {
  return [
    { value: ALL_SPACES, label: "All spaces", hint: "Home" },
    ...spaces.map((s) => ({ value: s.id, label: s.name, hint: pageCount(s.page_count) })),
  ];
}

export default function SpaceSwitcher({
  spaces,
  selectedSpaceId,
  onNavigate,
}: {
  spaces: readonly Space[];
  selectedSpaceId: string | null;
  onNavigate: (href: string) => void;
}) {
  return (
    <div className="mb-1 px-1">
      <SelectButton
        label="Switch space"
        value={selectedSpaceId ?? ALL_SPACES}
        defaultValue={ALL_SPACES}
        options={switcherOptions(spaces)}
        filterAbove={0}
        widthClass="w-full"
        panelMaxHeight={320}
        onChange={(value) => onNavigate(value ? spaceHref(value) : KNOWLEDGE_HOME)}
      />
    </div>
  );
}
