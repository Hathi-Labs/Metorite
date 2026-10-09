// What each money word MEANS — one entry per word, one meaning per entry.
// WS-50 slice 2, decision D94.
//
// 🔴 **Why this exists.** Six labels named the vendor cost and "Margin" named
// four different numbers. The owner could not tell "Our cost" from what we
// charge. Every `Explain` tooltip reads this file, so a word cannot mean one
// thing on one page and another thing on the next.
//
// Each entry carries:
//   - `title`: the label, exactly as a page prints it.
//   - `means`: one or two plain sentences.
//   - `formula`: how the number is reached, in words. `glossary.test.ts`
//     refuses an entry without one.
//
// ⚠️ **No internal code in this file.** No H-number, D-number, migration
// number or env var name. The test refuses them. The owner reads these words.

export type GlossaryEntry = {
  title: string;
  means: string;
  formula: string;
};

export const GLOSSARY = {
  weCharged: {
    title: "We charged",
    means:
      "The money this customer owes us for the period: their seats, plus the AI credits " +
      "they used that they had BOUGHT. Free credits are not counted.",
    formula: "Seats + bought credits used (at the price each lot was sold at)",
  },
  seats: {
    title: "Seats",
    means: "The monthly subscription: each seat the customer bought, at the plan price.",
    formula: "Seats bought × plan price a month. Zero until the paid plan is active.",
  },
  paidCredits: {
    title: "Bought credits used",
    means:
      "AI credits the customer paid for and spent in the period. A credit they bought " +
      "but did not use is not counted yet.",
    formula: "Credits spent from bought lots × the price each lot was sold at",
  },
  aiCost: {
    title: "AI cost",
    means:
      "What the AI vendors (OpenRouter and others) billed US for this customer's calls. " +
      "This is our cost, not the customer's price.",
    formula: "Vendor bill in US dollars × the planning rate saved on the Pricing page",
  },
  givenAway: {
    title: "Given away",
    means:
      "The AI cost of calls paid with FREE credits (trial, promotion or grant). We paid " +
      "the vendor and earned nothing for these calls. It is part of the AI cost.",
    formula: "AI cost × (free credits used ÷ all credits used)",
  },
  profit: {
    title: "Profit",
    means: "What is left after we pay the AI vendors. Other costs are not included.",
    formula: "We charged − AI cost",
  },
  margin: {
    title: "Margin",
    means: "The share of what we charged that we keep after the AI cost.",
    formula: "Profit ÷ We charged, as a percent",
  },
  aiRevenue: {
    title: "Charged for AI",
    means:
      "The value of the bought credits this app or person used. Seats are not split by app " +
      "or person, so they are not in this column.",
    formula: "Credits used × the customer's average earned per credit in the period",
  },
  aiMargin: {
    title: "AI margin",
    means:
      "The share of what we charged for AI that we keep after the AI cost. It leaves out " +
      "seats, so it can be lower than the customer's overall margin.",
    formula: "(Charged for AI − AI cost) ÷ Charged for AI, as a percent",
  },
  creditsLeft: {
    title: "Credits left",
    means:
      "The customer's credit balance. Each AI call spends credits. Below zero means we " +
      "kept serving after they ran out.",
    formula: "Every credit added − every credit spent",
  },
  daysLeft: {
    title: "Days left",
    means: "How long the credits last if the customer keeps using AI at the same rate.",
    formula: "Credits left ÷ average credits a day over the last 7 days",
  },
  owed: {
    title: "Owed",
    means:
      "Credits spent after the balance reached zero. The customer has not paid for them, " +
      "so they are not in We charged.",
    formula: "Credits below zero × the credit price",
  },
  creditsUsed: {
    title: "Credits used",
    means: "AI credits spent in the period, bought and free together.",
    formula: "The sum of the credits charged for each AI call",
  },
  calls: {
    title: "AI calls",
    means: "How many AI requests we answered for this customer.",
    formula: "Answered requests in the period. Refused requests are not counted.",
  },
  refused: {
    title: "Refused",
    means:
      "AI requests we turned down, for example at a spending limit. They cost us nothing, " +
      "and the customer got no answer.",
    formula: "Refused requests in the period",
  },
  servedNotBilled: {
    title: "Served, not billed",
    means:
      "AI requests we answered but could not charge, because the meter failed to read the " +
      "vendor's reply. We paid the vendor and charged nothing.",
    formula: "Answered requests with a metering fault",
  },
  notAttributed: {
    title: "Not attributed",
    means:
      "Calls that did not say which app, agent or person made them. The money is real. " +
      "Only the label is missing.",
    formula: "Calls with no app, agent or person recorded",
  },
  estimated: {
    title: "Estimated",
    means:
      "Part of this figure is an estimate, because the record that would make it exact " +
      "does not exist for that part. Open the explanation to see which part.",
    formula: "See the notes under the figures",
  },
} as const satisfies Record<string, GlossaryEntry>;

export type TermKey = keyof typeof GLOSSARY;
