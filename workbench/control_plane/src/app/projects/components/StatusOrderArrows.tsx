"use client";

import Button from "@/components/ui/Button";

/**
 * Task 1792. The two order arrows of a status row, in plain sight.
 *
 * `index` is the row's place inside its STAGE group and `count` is the size of
 * that group, so a lane alone in its stage has both arrows disabled and a
 * middle lane has both enabled. The parent calls `reorder()`, which never
 * moves a lane out of its stage.
 */
export function StatusOrderArrows({
  name,
  index,
  count,
  busy,
  onMove,
}: {
  name: string;
  index: number;
  count: number;
  busy: boolean;
  onMove: (direction: "up" | "down") => void;
}) {
  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="ChevronUp"
        aria-label={`Move ${name} up`}
        disabled={busy || index === 0}
        onClick={() => onMove("up")}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        icon="ChevronDown"
        aria-label={`Move ${name} down`}
        disabled={busy || index === count - 1}
        onClick={() => onMove("down")}
      />
    </>
  );
}
