/**
 * The shared states of a Studio layout (STU-LY-1). Pure: no React, no I/O.
 *
 * Spec: `project-docs/specs/studio_layouts.md` §3 (States). Every layout preset draws its empty,
 * loading, error and no-permission states through `components/studio/StudioBlockState.tsx`, and this
 * file holds each rule and each sentence, so the node-env vitest holds them:
 *
 * 1. **One order.** No permission beats an error, an error beats loading, loading beats empty. A page
 *    is never blank: a block with rows draws no state (`null`).
 * 2. **Filtered differs from true empty.** Rows that a filter hides give "Clear filters". A set with no
 *    rows at all gives the one next action of the app (Create), or none when the viewer cannot write.
 * 3. **An error says what failed, says the data is safe, and has Retry.** It never shows the server text.
 * 4. **No permission says what the member lacks and who to ask.**
 *
 * Fence: `blockState.test.ts` and `StudioBlockState.test.ts`.
 */

export type BlockStateKind = "loading" | "empty" | "filtered" | "error" | "no-permission" | "not-found";

export interface BlockStateInput {
  /** The read runs now and has no rows yet. */
  loading: boolean;
  /** The read failed. The text is never shown. */
  error: string | null;
  /** The viewer may not read the block (a 403 of the door). */
  forbidden: boolean;
  /** A second try may work. False for a refusal that would fail the same way (a 422, a gone record). Default true. */
  retryable?: boolean;
  /** The one record is gone, or the viewer may not read it. The door says one text for both (STU-LY-3). */
  missing?: boolean;
  /** The rows that the block draws now. */
  rows: number;
  /** True when a filter or a search narrows the rows. */
  filtersActive: boolean;
}

/** Which state to draw, or `null` when the block has rows and draws itself. */
export function blockStateKind(input: BlockStateInput): BlockStateKind | null {
  if (input.forbidden) return "no-permission";
  if (input.missing) return "not-found";
  if (input.error) return "error";
  if (input.loading) return "loading";
  if (input.rows > 0) return null;
  return input.filtersActive ? "filtered" : "empty";
}

export interface BlockStateCopy {
  /** A name of Lucide. */
  icon: string;
  message: string;
  hint?: string;
  /** The label of the one action. */
  action?: string;
}

export interface BlockStateWords {
  /** What the block holds, in the plural and in lower case: "visits". */
  noun: string;
  /** The singular of the create act: "visit". Omit it when the viewer cannot create. */
  create?: string;
  /** Who to ask for access: "an admin of the app". */
  askWho?: string;
  /** A queue that is empty is a success, not a gap (STU-LY-6): "All clear." and no action. */
  allClear?: boolean;
  /** A second try may work (see `BlockStateInput.retryable`). */
  retryable?: boolean;
}

/** The sentence of one state. Loading has no copy, and the skeleton draws it. */
export function blockStateCopy(
  kind: Exclude<BlockStateKind, "loading">,
  words: BlockStateWords,
): BlockStateCopy {
  const { noun, create, askWho, allClear } = words;
  switch (kind) {
    case "empty":
      if (allClear) return { icon: "CircleCheck", message: "All clear.", hint: `No ${noun} wait here.` };
      return create
        ? {
            icon: "Inbox",
            message: `No ${noun} yet.`,
            hint: `Add the first ${create} to start.`,
            action: `New ${create}`,
          }
        : { icon: "Inbox", message: `No ${noun} yet.` };
    case "filtered":
      return {
        icon: "SearchX",
        message: `No ${noun} match these filters.`,
        hint: "Clear the filters to see all of them.",
        action: "Clear filters",
      };
    case "error":
      // A refusal that a second try cannot change has no Retry and no "try again" (capture finding, task 3320).
      if (words.retryable === false) {
        return {
          icon: "TriangleAlert",
          message: `The ${noun} cannot be shown.`,
          hint: "Your data is safe. The app asks for something that it cannot read. Tell the owner of the app.",
        };
      }
      return {
        icon: "TriangleAlert",
        message: `The ${noun} did not load.`,
        hint: "Your data is safe. Try again.",
        action: "Retry",
      };
    case "not-found":
      // One sentence for a record that is gone and for one the viewer may not read, so it leaks nothing.
      return { icon: "SearchX", message: `This ${noun} was not found, or you cannot open it.` };
    case "no-permission":
      return {
        icon: "Lock",
        message: `You cannot open the ${noun}.`,
        hint: askWho ? `Ask ${askWho} for access.` : "Ask the owner of the app for access.",
      };
  }
}

/** The plural of an object label, in lower case: "Visit" gives "visits", "Company" gives "companies". */
export function pluralOf(label: string): string {
  const w = label.trim().toLowerCase();
  if (!w) return "records";
  if (/[^aeiou]y$/.test(w)) return `${w.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/.test(w)) return `${w}es`;
  return `${w}s`;
}

/** The label that a screen reader hears for a loading block. */
export function blockLoadingLabel(noun: string): string {
  return `Loading ${noun}`;
}
