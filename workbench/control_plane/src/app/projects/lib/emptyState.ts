/**
 * Projects · what an empty canvas says (S4).
 *
 * Two states, never one. The board used to draw *"Nothing to show. Clear a
 * filter, or this project has no statuses yet."* — a sentence that names both
 * possible causes and leaves the reader to work out which applies to them, and
 * the list drew a bare *"No tasks here yet."* even when a filter was hiding
 * everything. `/tasks` had this right: `NoMatchState` says the filters did it
 * and offers a way out, `EmptyState` says there is genuinely nothing.
 *
 * The predicate is `grouping.isFiltered` — the same one the toolbar's Clear
 * button and the `filtered` badge already read. A second "is anything
 * filtering?" rule here would be the third answer to one question, and the two
 * would disagree the first time a filter was added.
 *
 * Pure, and tested (`emptyState.test.ts`), because the copy IS the feature: a
 * component test could not run here (vitest is node-env, `.test.ts` only) and
 * "does this screen explain itself" is otherwise fenced by nothing.
 *
 * ## Three states since WS-27am
 *
 * The triad closes with **no-permission**, whose CTA is *disabled rather than
 * hidden*. It is a distinct answer to the same question the other two answer —
 * *is this empty, or did I hide it?* — because "you are not allowed to put
 * anything here" is neither.
 *
 * ⚠️ **No caller passes `canCreate` yet.** The gateway's per-project write
 * grant is not on the client's `TaskRow`/`ProjectRow` shapes, and wiring it is
 * a call-site edit in `TaskBoard`/`TaskList`/`TableView` — files other slices
 * hold open. The capability lands first and unreachable, on purpose: the
 * default is `true`, so every existing surface renders exactly what it did.
 */

import type { EmptyStateAction } from "@/components/EmptyState";
import { MIN_QUERY, isSearchableQuery } from "./search";

/** Which surface is empty. They differ only where they honestly differ. */
export type EmptyCanvas = "board" | "list";

export interface EmptyStateCopy {
  /**
   * Lucide name for `<EmptyState icon=…>`.
   *
   * ⚠️ **Must be a name `icon-registry.json` maps in every pack.** An unmapped
   * name silently falls back to the Lucide glyph, and one Lucide outline in a
   * screen of Material Symbols reads as a bug, not as a style. `FilterX` is
   * exactly that trap — mapped nowhere — which is why the filtered state wears
   * `SearchX` (`search-off` / `search-info`). `emptyState.test.ts` checks it.
   */
  icon: string;
  message: string;
  hint?: string;
  /**
   * True when filters are the cause — the caller offers **Clear filters**.
   * Carried rather than re-derived so the copy and the control cannot drift
   * apart: the state that blames the filters is exactly the state that undoes
   * them.
   */
  filtered: boolean;
  /**
   * The CTA this state itself decides on — set **only** by the no-permission
   * arm (WS-27am), and always `disabled: true` there.
   *
   * The other two arms deliberately leave it absent: *Clear filters* and
   * *Create* are live controls the caller wires to its own handlers, and a copy
   * module that carried their `onClick` would be deciding behaviour. What this
   * arm carries is not behaviour — it is the statement *this action exists and
   * is not yours*, which is copy, and which is exactly the part a caller cannot
   * be trusted to remember. Typed as the component's own `EmptyStateAction`
   * (a **type-only** import — no runtime edge from this pure module into React)
   * so there is one vocabulary for an action, not two.
   */
  action?: EmptyStateAction;
}

export function emptyStateCopy(input: {
  canvas: EmptyCanvas;
  filtered: boolean;
  /**
   * Board only: the columns come from the project's statuses, so an unfiltered
   * board with no columns means the project has no statuses — a fact worth
   * saying, and one the list can never be in.
   */
  onStatusAxis?: boolean;
  /**
   * Whether the reader may add a task here. **Defaults to true** — every
   * existing caller predates this arm and none of them passes it, so the
   * default has to be the behaviour they already have.
   */
  canCreate?: boolean;
  /**
   * The filter box's text, `filters.q`. D-PM-31: the list endpoint matches
   * NOTHING for text under `MIN_QUERY` characters. Without this arm the
   * board said "No tasks match your filters" for "qa", which is false.
   */
  query?: string;
}): EmptyStateCopy {
  const query = (input.query ?? "").trim();
  if (input.filtered && query && !isSearchableQuery(query)) {
    return {
      icon: "SearchX",
      message: `Type at least ${MIN_QUERY} characters to search.`,
      hint: "A task number works at any length: #7.",
      filtered: true,
    };
  }
  if (input.filtered) {
    return {
      icon: "SearchX",
      message: "No tasks match your filters.",
      hint: "Clear them to see everything here again.",
      filtered: true,
    };
  }
  /**
   * The third arm (WS-27am), and it outranks the status-axis one below.
   *
   * Order matters and this is the argued half: a viewer on a board with no
   * columns would otherwise be told *"add a status and tasks have somewhere to
   * sit"* — an instruction they cannot follow. A hint that asks for an action
   * the reader is not allowed to take is worse than a less specific truth,
   * because it reads as the app being broken rather than as access being
   * limited. Filters still outrank this one: *Clear filters* is a way out that
   * a read-only reader genuinely has.
   */
  if (input.canCreate === false) {
    return {
      icon: "Lock",
      message: "No tasks here yet.",
      hint: "You have view-only access to this project.",
      filtered: false,
      action: {
        label: "New task",
        icon: "Plus",
        // Disabled, never omitted: the reader learns the action exists AND
        // that it is not theirs. Hidden, they learn neither and go looking.
        disabled: true,
        disabledReason: "Ask a project admin for edit access to add tasks.",
      },
    };
  }
  if (input.canvas === "board" && input.onStatusAxis) {
    return {
      icon: "Columns3",
      message: "This project has no statuses yet.",
      hint: "Statuses are the board's columns — add one and tasks have somewhere to sit.",
      filtered: false,
    };
  }
  return {
    icon: "ClipboardList",
    message: "No tasks here yet.",
    filtered: false,
  };
}
