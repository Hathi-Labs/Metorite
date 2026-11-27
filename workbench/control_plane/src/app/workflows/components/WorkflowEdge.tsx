"use client";

/**
 * The canvas edge (RFC §5.3) — carries the two things the mockup's edges say
 * that a plain line does not:
 *
 *  1. **Which branch this is.** A condition's `sourceHandle` *is* the routing
 *     key, so it is labelled Yes/No on the wire rather than left to be inferred
 *     from geometry. Colour/dash come from the page, which also owns the live
 *     "hot" highlight while a run flows through.
 *  2. **"+" to insert a block inline** — Copilot Studio's transcript-style
 *     authoring. Clicking arms the palette; the next block picked is spliced in
 *     and rewired, so makers never hand-drag two edges to add a step.
 */

import Icon from "@/components/Icon";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from "@xyflow/react";

export type WorkflowEdgeData = {
  branch?: "true" | "false" | null;
  hot?: boolean;
  onInsert?: (edgeId: string) => void;
  insertArmed?: boolean;
  [key: string]: unknown;
};

export default function WorkflowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  data,
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  });
  const d = (data ?? {}) as WorkflowEdgeData;

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: "all",
          }}
          className="nodrag nopan flex items-center gap-1"
        >
          {d.branch && (
            <span
              className={`font-mono text-[10px] uppercase tracking-[0.1em] px-1.5 py-px rounded-full border bg-background ${
                d.branch === "true"
                  ? "text-success border-success/40"
                  : "text-muted-foreground border-border"
              }`}
            >
              {d.branch === "true" ? "yes" : "no"}
            </span>
          )}
          {d.onInsert && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                d.onInsert?.(id);
              }}
              title={
                d.insertArmed
                  ? "Pick a block in the palette to insert here"
                  : "Insert a block here"
              }
              className={`grid place-items-center w-4 h-4 rounded-full border bg-background tech-transition ${
                d.insertArmed
                  ? "border-primary text-primary ring-2 ring-primary/30"
                  : "border-border text-muted-foreground opacity-50 hover:opacity-100 hover:border-primary hover:text-primary"
              }`}
            >
              <Icon name="Plus" className="w-2.5 h-2.5" />
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
