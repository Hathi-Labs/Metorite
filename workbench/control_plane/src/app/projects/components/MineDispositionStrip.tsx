"use client";

/**
 * Projects · My planning on a task opened from My tasks (WS-64 OP-11).
 *
 * `MyWorkStrip` writes through the task store, and My tasks does not hydrate
 * the store (§9 OP-11, server note 11). So a task opened from My tasks would
 * lose the one control that moves it between the chips. This strip draws the
 * same row with the same parts (`SectionLabel`, `MetaEdit`, `ChipMenu`) and
 * writes the disposition through the lens to the server, as `MyFocusRow`
 * does. The page reads the task again after each write.
 *
 * Context, energy and defer stay in My task inbox's strip until OP-5.
 */

import { themedIcon } from "@/components/Icon";
import { ChipMenu, MetaEdit, SectionLabel } from "@/app/tasks/components/ItemDetail";
import type { Disposition } from "@/app/tasks/lib/types";

import { STRIP_DISPOSITIONS } from "./MyWorkStrip";

export function MineDispositionStrip({
  disposition,
  onPick,
}: {
  disposition: Disposition;
  onPick: (next: Disposition) => void;
}) {
  return (
    <section
      data-testid="my-work-strip"
      data-strip="server"
      aria-label="My planning"
      className="border-b border-border px-3 py-3"
    >
      <SectionLabel icon={themedIcon("Tag")}>My planning</SectionLabel>
      <div className="grid grid-cols-1 gap-y-1">
        <MetaEdit
          label="Disposition"
          icon={themedIcon("CircleDot")}
          display={<span className="capitalize">{disposition.toLowerCase()}</span>}
        >
          {(close) => (
            <ChipMenu
              options={[...STRIP_DISPOSITIONS]}
              active={disposition}
              format={(o) => o.charAt(0) + o.slice(1).toLowerCase()}
              onPick={(v) => {
                if (v) onPick(v as Disposition);
                close();
              }}
            />
          )}
        </MetaEdit>
      </div>
    </section>
  );
}

export default MineDispositionStrip;
