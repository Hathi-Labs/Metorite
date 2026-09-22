// The operator console's button vocabulary — ONE name per act, in one file.
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
//
// 📌 **It grew past the Models page on 2026-09-22.** The owner reported the
// same complaint one page over — Tiers and Pricing named their commit and
// their back-out whatever each component felt like, and Pricing put a
// "Close" beside a Save, which the SAVE / DONE rule above already forbids by
// name. `FORM` below is that rule made reusable.

/** Put a model into the catalog. */
export const ADD = {
  /** One row of the vendor feed. */
  one: "Add",
  /** One row the vendor feed carries NO price for. It still declares fine
   *  and lands costs blind, so the button works and says it is a concession.
   *  ⚠️ A row the Router has no verb for draws no button at all — that case
   *  is the "not servable yet" note, not a label here. */
  unpriced: "Add anyway",
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


/** The controls every editing surface shares — commit, back out, revert.
 *
 * 🔴 **Why this is not per-page.** The Models page took the vocabulary rule
 * above and the other three pages did not, so one console spelled the same
 * two acts four ways. The owner read it as "the button names are all over
 * the place", twice, five weeks apart.
 *
 * ⚠️ **`cancel` and `undo` are DIFFERENT ACTS, and the split is the point.**
 * `cancel` abandons an editor that was never committed — nothing existed to
 * go back to. `undo` throws away edits to something that IS saved, and puts
 * the stored version back. Labelling both "Cancel" tells the operator the
 * second one is harmless, and it is not.
 */
export const FORM = {
  /** Commit an editor. Plain, because the surrounding heading says what of. */
  save: "Save",
  /** Commit a re-ordered failover chain. Says "order" because the act is the
   *  ORDER, not the membership — an operator who added a model and expects
   *  "Save" to add it is reading the right word for the wrong act. */
  saveOrder: "Save this order",
  /** Put a thing into a list that is being built. */
  add: "Add",
  /** Abandon an editor that has committed NOTHING yet.
   *  ⚠️ Never "Close" beside a Save. Two ways out, and one loses work. */
  cancel: "Cancel",
  /** Throw away edits and restore what is stored. */
  undo: "Undo",
  /** Leave an editor whose work is already committed. */
  done: "Done",
  /** Present participle for the disabled state. One spelling, everywhere. */
  busy: "Saving…",
} as const;
