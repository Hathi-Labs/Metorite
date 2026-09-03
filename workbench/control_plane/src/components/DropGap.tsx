"use client";

/**
 * The drop target between two cards — shared by /projects and /tasks (WS-27ad).
 *
 * The visual half of the drop-gap reorder /tasks had and /projects did not (see
 * `@/lib/boardDrop` for the arithmetic and the reason it won). A thin strip with
 * a hit area more than twice its own height, because an empty or short column is
 * otherwise a 2px target somebody has to aim at.
 *
 * **The geometry does not change while a drag is in flight, and that is the
 * point.** This component used to grow from `h-1.5` to `h-3` on a `dragging`
 * prop. One gap sits above every card plus one at the end, and the prop was
 * board-level state, so picking up a single card grew EVERY gap in EVERY column
 * at once — a 12-card column gained 13 x 6px = 78px and the whole board
 * stretched downward under the cursor. The hit area is now won with padding
 * that a negative margin cancels: the target is 14px tall, the layout
 * contribution is the 2px it was at rest, and nothing reflows.
 *
 * The outer box is the drop target and owns the geometry. The inner strip is
 * the paint, so the highlight stays a thin line rather than a 14px slab.
 *
 * `stopPropagation` on both handlers is load-bearing: the gap sits inside a
 * column that is itself a drop target, and without it the column's own handler
 * fires too and appends the card — turning every precise drop back into the
 * append this component exists to replace.
 */

export function DropGap({
  active,
  onOver,
  onDrop,
  className = "",
}: {
  /** This is the gap the card would land in. */
  active: boolean;
  onOver: () => void;
  onDrop: () => void;
  className?: string;
}) {
  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onOver();
      }}
      onDrop={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onDrop();
      }}
      // 6px strip + 8px padding - 12px margin = the 2px this occupied at rest,
      // with a 14px target. Change these three together or not at all.
      className={["-my-1.5 py-1", className].join(" ")}
    >
      <div
        className={[
          "h-1.5 rounded transition-colors",
          active ? "bg-primary/50" : "bg-transparent",
        ].join(" ")}
      />
    </div>
  );
}
