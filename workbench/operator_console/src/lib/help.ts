// What every control on the Models section is FOR — one dictionary.
//
// 🔴 **Why these live here and not inline.** The owner's complaint was that
// labels like "Can", "From" and "State" told them nothing. Two of those rows
// are gone; the rest of the page still asks a reader to know what "Reads at
// most" or "costs blind" means before they can act. A hover that explains is
// the cheapest fix, and a dictionary is what makes it reviewable: the whole
// vocabulary of a surface, readable in one file, instead of forty strings
// scattered through JSX.
//
// ⚠️ **A tooltip EXPLAINS, it never restates.** "Reads at most: the most it
// reads" is worse than nothing — it spends a hover and teaches nothing, and it
// teaches the reader that hovering is not worth doing. `help.test.ts` refuses
// an entry that merely repeats its own label.
//
// ⚠️ **Say the CONSEQUENCE where there is one.** "What the vendor charges us,
// not what a customer pays" stops the single most expensive confusion on this
// page — reading a cost as a price inverts a margin.

import type { ModelStatus } from "./modelSearch";

/** The feed strip, above everything. */
export const HELP_FEED = {
  freshness:
    "When we last pulled vendor facts — prices, context windows and " +
    "retirement dates — from litellm's maintained price map. Nothing here " +
    "is billing truth: what a customer pays lives on Pricing.",
  fetch:
    "Pull today's vendor facts now. This updates what the vendor charges US " +
    "and changes no price a customer pays.",
  readyToAdd:
    "Jump to the models your connected vendors offer that nobody has " +
    "declared here yet. Adding one from there needs no typing.",
} as const;

/** The costs-blind banner. */
export const HELP_FILL = {
  fillAll:
    "Copies the vendor's own recorded price onto every model that has none. " +
    "It only ADDS a price — a model you have already costed is untouched.",
} as const;

/** The toolbar and the filter row. */
export const HELP_TOOLBAR = {
  search:
    "Matches the model id, its name, the vendor, the description and what " +
    "it can do. Every word you type must match somewhere — try " +
    "“deepseek reads images”.",
  sort:
    "Changes the order of the cards below. “Cheapest” sorts on what " +
    "the VENDOR charges us, and a model with no recorded price sorts last " +
    "rather than first.",
  kindChip:
    "Show only models that can do this job. Pick several and a model must " +
    "do ALL of them — that is how you find a chat model that also reads " +
    "images.",
  attention:
    "Models that cannot serve, or cannot be costed: no vendor key " +
    "installed, no price recorded, or not declared to the Router at all.",
  clear: "Drop every filter and the search, and show the whole catalog again.",
  showMore:
    "Draw the rest of the matching models. They are held back because every " +
    "card builds a feed lookup and a price comparison, and a long catalog " +
    "makes the page slow before the first one is readable.",
} as const;

/** Tiers and their backups — where the word a customer picks meets the model
 *  we actually call. */
export const HELP_TIERS = {
  outage:
    "Switch a vendor off to see which tiers would stop answering. It changes " +
    "nothing — it is a question, not a switch.",
  outageChip:
    "Pretend this vendor is down. Tiers whose whole chain runs on it stop; " +
    "tiers with a step elsewhere fail over to it.",
  outageMore:
    "The vendors listed serve the most jobs. The rest each serve fewer, so " +
    "switching one off would lose less than any chip shown here.",
  section:
    "Open or close this group. The count is how many tiers are in it.",
  job:
    "The one job this tier serves. The Router refuses a model that was not " +
    "declared for it, so audio can never reach a chat model.",
  moveUp:
    "Try this model earlier. The step at position 1 serves every call; the " +
    "rest only run when the step above them fails.",
  moveDown: "Try this model later, after the step now above it.",
  remove:
    "Take this model out of the chain. Nothing is saved until you press " +
    "Save, and removing the last step leaves the tier unable to serve.",
  addBackup:
    "Add a second model to try when the first stops answering. A tier with " +
    "one model has no failover: when that model is down or rate limited, " +
    "the job stops for every customer on the tier.",
  noBackup:
    "One model only. When it is down or rate limited this job stops for " +
    "every customer on this tier, and there is nowhere for the Router to go.",
  noPrice:
    "No rate card for this tier and job, so calls through it bill nothing. " +
    "Price it on the Pricing page.",
  sameProvider:
    "Every step runs on one vendor. The VENDOR is the thing that goes down, " +
    "so this is one point of failure written several times. Add a step from " +
    "a different provider.",
  notRegistered:
    "A binding names this tier but `tier_catalog` does not. It serves, and " +
    "it cannot be priced until somebody registers it.",
  rank:
    "Where this model sits in the chain. 1 serves every call; 2 runs only " +
    "when 1 fails, and so on.",
} as const;

/** Declaring a model by hand — the escape hatch when the feed has never heard
 *  of it. Most models should come from "available from your vendors" instead. */
export const HELP_DECLARE = {
  open:
    "Add a model the vendor feed does not carry. Anything litellm can reach " +
    "works, written up or not — but if the feed knows it, adding it above " +
    "needs no typing at all.",
  modelId:
    "The id the Router will call, exactly as the vendor spells it — usually " +
    "vendor/model. Getting this wrong means every call to it fails.",
  task:
    "Which job this model does. The Router refuses to route a model to a job " +
    "it was not declared for, so audio can never reach a chat model.",
  verb:
    "Which provider call the Router makes for it. This must match the job — " +
    "a chat model answers a completion, not a transcription.",
  streams:
    "The model can send its answer back a piece at a time. Chat and speech " +
    "do; a transcription arrives whole.",
  submit:
    "Declares the model to the Router. It records no price, so the model " +
    "arrives costs blind until you add one.",
} as const;

/** The three facts on every card. */
export const HELP_FACTS = {
  contextWindow:
    "The largest prompt this model accepts, counted in tokens. The vendor's " +
    "number, not ours. A dash means the vendor has not told us.",
  maxOutput:
    "The most this model will write in one reply, in tokens. A long answer " +
    "is cut off here however big the prompt was.",
  vendorPrice:
    "What the VENDOR charges US per million tokens. This is not what a " +
    "customer pays — that is the rate card on Pricing, keyed to the tier " +
    "they picked. Reading one as the other inverts a margin.",
  tierUse:
    "The tiers that serve from this model. A 1st choice takes every call; a " +
    "backup runs only when the choice above it fails. Changing a 1st choice " +
    "moves customer traffic immediately.",
  unused:
    "No tier points at this model, so nothing calls it. That is the normal " +
    "middle of setup, not a fault — bind it on Tiers & backups when ready.",
} as const;

/** The supply state chip, which is the word an operator acts on. */
export const HELP_STATUS: Record<ModelStatus, string> = {
  costed:
    "Declared, callable, and we know what the vendor charges — so every " +
    "call through it can be costed and its margin is real.",
  costblind:
    "Declared and callable, but no vendor price is recorded. Calls SERVE " +
    "normally; what they cost us reads as unknown, so any margin touching " +
    "this model is unknown too.",
  nokey:
    "We hold no live platform key for this vendor, so EVERY call to this " +
    "model fails. A price would not fix it — install the key on Providers.",
  undeclared:
    "The Router has no capability row for this model, so it will refuse to " +
    "route to it. Nothing can call it until it is declared.",
};

/** "Available from your vendors". */
export const HELP_AVAILABLE = {
  search:
    "Narrow the list within every vendor at once. Typing also opens the " +
    "vendors that matched.",
  vendor:
    "Open to see what this vendor offers that nobody has declared yet. The " +
    "count is what is on offer, not what we use.",
  colModel: "The id the Router calls, spelled exactly as the vendor spells it.",
  colJob: "What this model is for. The Router serves chat, transcribe, image and speech.",
  colContext:
    "The largest prompt this model accepts, counted in tokens. The vendor's " +
    "own figure — a dash means it has not published one.",
  colPrice:
    "What the vendor would charge US, in whatever unit it sells by — per " +
    "million tokens, per minute of audio, per character, or per image.",
  add:
    "Declares the model to the Router AND saves the vendor's facts, in one " +
    "act. No form, and nothing to type.",
  addUnpriced:
    "Declares the model, but the feed carries no price for it — so it will " +
    "arrive costs blind and its margin will read as unknown.",
  notServable:
    "litellm offers this model in a mode the Router has no endpoint for " +
    "yet, so declaring it would create something nothing can call.",
} as const;

/** The details editor — the form that made the owner ask for all of this. */
export const HELP_DETAILS = {
  open: "Record or correct what we know about this model.",
  fillFromFeed:
    "Copy the vendor's own window, limits and prices onto this model and " +
    "save, in one click. Nothing to type.",
  label: "A friendlier name for the lists on this page. The Router always uses the id.",
  description:
    "What this model is good at, in your words. The search box matches it, " +
    "so “cheap” or “long documents” makes it findable.",
  contextWindow: HELP_FACTS.contextWindow,
  maxOutput: HELP_FACTS.maxOutput,
  vendorIn: "What the vendor charges us per million INPUT tokens, at the peak rate.",
  vendorOut: "What the vendor charges us per million OUTPUT tokens, at the peak rate.",
  vendorCached:
    "The vendor's discounted rate for input it served from its own cache. " +
    "Without it, a cache-hitting call cannot be costed at all.",
  offpeak:
    "Only for a vendor that charges less at certain hours. Leave every box " +
    "empty and this model is priced the same all day, which is how almost " +
    "every vendor works.",
  longContext:
    "Only for a vendor that charges more above a context size. Leave empty " +
    "for one rate at every prompt length.",
  perUnit:
    "For a model that is not sold by the token. Fill in the one line its " +
    "job uses — a minute of audio, a character spoken, an image — and leave " +
    "the rest empty.",
  readsImages: "The model accepts images in a prompt. The Vision tier needs this.",
  thinksFirst: "The model reasons before answering, which costs more output tokens.",
  blank: "Leave a box empty for “we do not know”. It shows as a dash, which is true — a zero would read as a broken model.",
  copyFeed:
    "Fill every box below from the vendor's own figures. Nothing is saved " +
    "until you press Save, so you can read the numbers first.",
  save:
    "Write these facts to the model. Calls made after this are costed with " +
    "them — what a customer pays is unchanged, and lives on Pricing.",
  close: "Shut the editor. Anything you typed and did not save is discarded.",
} as const;
