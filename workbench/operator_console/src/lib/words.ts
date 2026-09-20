// The Models page's button vocabulary — ONE name per act, in one file.
//
// 🔴 **Why this exists.** The page grew four doors to "put a vendor's numbers
// into our profile" and called them four things: "Fill all N from the feed",
// "Fill from the vendor feed", "Copy the vendor's facts into the boxes", and
// a fourth on the feed table. It grew four "Add"s, and one of them
// ("+ Add details") added no model — it opened an editor. An operator cannot
// build a mental model of a page whose controls rename the same act every
// time they move down it. Owner report, 2026-09-21.
//
// **The rule, and it is the whole file.** One act, one verb, everywhere:
//
//   · ADD    — put a model INTO the catalog. Nothing else may say "add".
//   · REMOVE — take a model OUT of the catalog. Nothing else may say "remove".
//   · EDIT   — open the facts editor. It is never "add", because it adds no
//              model, and an empty editor still reads as editing nothing.
//   · USE THE VENDOR'S PRICES — copy upstream's numbers into our profile,
//              whether that is one model, one card, or every blind one.
//   · SAVE / DONE — commit, and close. Never "Close" beside a Save: the two
//              read as a pair of ways to leave, and one of them loses work.
//
// ⚠️ **R7 — the fence is `words.test.ts`.** It pins every string here AND
// scans `src/app/models/*.tsx` for the retired vocabulary, so the old names
// cannot come back one component at a time, which is how they arrived.

/** Put a model into the catalog. */
export const ADD = {
  /** One row of the vendor feed. */
  one: "Add",
  /** One row whose job the Router has no verb for yet. */
  unservable: "Add anyway",
  /** The bulk act, over the rows you ticked. */
  selected: (n: number) => `Add ${n} selected`,
  /** The by-hand form's disclosure and its submit. */
  byHandSummary: "Add a model by hand",
  byHandSubmit: "Add",
  /** Present participle, for the disabled state. One spelling, everywhere. */
  busy: "Adding…",
} as const;

/** Take a model out of the catalog. */
export const REMOVE = {
  /** The control on a model card. */
  one: "Remove",
  /** The confirm step's commit. It repeats the verb on purpose — a confirm
   *  labelled "OK" makes the reader re-read the question to learn what OK
   *  does. */
  confirm: "Remove it",
  /** Backing out of the confirm. Never "Cancel": the operator is cancelling
   *  nothing, they are keeping what they have. */
  keep: "Keep it",
  busy: "Removing…",
} as const;

/** Open the facts editor. */
export const EDIT = {
  open: "Edit details",
  /** Leaving the editor. Beside a Save, "Close" reads as a second way out and
   *  the reader has to guess which one discards. "Done" does not. */
  close: "Done",
  save: "Save",
  busy: "Saving…",
} as const;

/** Copy the vendor's published numbers into our own profile. */
export const VENDOR_PRICES = {
  /** One card, one click, written straight to the profile. */
  one: "Use the vendor's prices",
  /** Every costs-blind model the feed can price. */
  all: (n: number) => `Use the vendor's prices for all ${n}`,
  /** Inside the open editor this only FILLS THE BOXES — the operator still
   *  presses Save. Same verb, and the sentence around it carries the
   *  difference, because a different verb would read as a different act. */
  intoBoxes: "Use the vendor's prices",
  busy: "Filling…",
} as const;

/** The list controls. */
export const LIST = {
  fetchFeed: "Fetch the latest",
  fetchBusy: "Fetching…",
  readyToAdd: (n: number) => `${n} ready to add →`,
  clear: "Clear filters",
  showMore: (n: number) => `Show ${n} more`,
} as const;
