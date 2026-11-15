"use client";

/**
 * The four shared states of a Studio layout (STU-LY-1): loading, empty, error, no permission,
 * and the filtered-empty that differs from the true empty.
 *
 * Spec: `project-docs/specs/studio_layouts.md` §3. One component for every layout preset. It
 * composes `EmptyState` (the empty box, with its disabled arm) and `Skeleton` (the loading shape),
 * so it adds no colour, no control and no second empty state. Which state to draw, and each
 * sentence, come from `lib/studio/blockState.ts`. A preset never writes its own copy of them.
 * Fence: `StudioBlockState.test.ts`.
 */

import { EmptyState } from "@/components/EmptyState";
import { Skeleton, SkeletonBoard, SkeletonRows } from "@/components/ui/Skeleton";
import {
  blockLoadingLabel,
  blockStateCopy,
  blockStateKind,
  type BlockStateInput,
  type BlockStateWords,
} from "@/lib/studio/blockState";

/** The shape of the block that loads, so nothing moves when the rows land. */
export type BlockShape = "rows" | "board" | "stats" | "tile";

export interface StudioBlockStateProps extends BlockStateInput, BlockStateWords {
  shape?: BlockShape;
  onCreate?: () => void;
  onClearFilters?: () => void;
  onRetry?: () => void;
}

/** Draws the state of a block, or nothing when the block has rows. */
export default function StudioBlockState(props: StudioBlockStateProps) {
  const { shape = "rows", onCreate, onClearFilters, onRetry } = props;
  const kind = blockStateKind(props);
  if (kind === null) return null;

  if (kind === "loading") {
    const label = blockLoadingLabel(props.noun);
    if (shape === "board") return <SkeletonBoard columns={3} />;
    // One tile of a dashboard (STU-LY-7): the block is a single card, so the skeleton is one card.
    if (shape === "tile") return <Skeleton className="h-24 w-full" />;
    if (shape === "stats") {
      return (
        <div className="grid grid-cols-2 gap-3 p-3 sm:grid-cols-4" role="status" aria-busy="true" aria-label={label}>
          {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-20 w-full" />)}
        </div>
      );
    }
    return <SkeletonRows count={6} className="p-3" />;
  }

  const copy = blockStateCopy(kind, props);
  const run = kind === "empty" ? onCreate : kind === "filtered" ? onClearFilters : kind === "error" ? onRetry : undefined;
  return (
    <div role={kind === "error" ? "alert" : "status"} className="flex flex-1 flex-col">
      <EmptyState
        icon={copy.icon}
        message={copy.message}
        tone={props.allClear && kind === "empty" ? "success" : "muted"}
        hint={copy.hint}
        action={copy.action && run ? { label: copy.action, onClick: run } : undefined}
      />
    </div>
  );
}
