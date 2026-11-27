/* The conversation, as data. Each line is a string or a function of the context.
   All people and companies are fictional. */
window.SCRIPT = {
  defaults: { name: "", business: "your business", city: "", people: 42, buyers: 300 },

  opening: {
    chips: ["I'm Rajesh. I run Kaveri Textiles in Surat. We make fabric for garment makers."],
  },

  reflect: (c) => [
    c.name ? `Good to meet you, ${c.name}.` : "Good to meet you.",
    c.sell
      ? `So ${c.business}${c.city ? ", " + c.city : ""}. You ${c.sell}.`
      : `So ${c.business}${c.city ? ", " + c.city : ""}. I'll learn the rest as we go.`,
    "Three quick questions, so I know what to look for.",
  ],

  people: {
    ask: "How many people work with you?",
    chips: ["42", "About 40", "Skip"],
  },
  buyers: {
    ask: "And about how many buyers order from you in a year?",
    chips: ["About 300", "Skip"],
  },
  stuck: {
    ask: "Where do things get stuck, and wait on you?",
    chips: ["Payments come late", "Decisions wait in WhatsApp", "Both"],
  },
  stuckReply: (c) => {
    const a = [];
    if (c.stuck.pay && c.stuck.wa) a.push("Late money and decisions in WhatsApp. I hear that from most owners I work with.");
    else if (c.stuck.pay) a.push("Late money. Then I start where the money is.");
    else if (c.stuck.wa) a.push("Decisions that wait on you. I'll find them, so they stop waiting.");
    else a.push("Noted. I'll watch for it.");
    a.push(`${c.peopleLine}. That is a lot to follow up on for one owner.`);
    return a;
  },

  email: {
    intro: [
      "First, your email.",
      "That's where buyers ask, complain and promise to pay. I read it so I can tell you what waits on you.",
    ],
    card: {
      mark: "M", title: "Gmail", sub: "Read only · last 30 days",
      why: "I read threads to find what waits on you. I never send, delete or reply without asking you first.",
      yes: ["Read your mail and its attachments", "See the names of the people you write to"],
      no: ["Send mail as you", "Delete or move anything"],
      steps: 214, unit: "threads",
    },
    insight: () => ({
      kicker: "First thing I noticed",
      big: "I read <strong>214 threads</strong> from the last 30 days. <strong>Three wait for your decision.</strong>",
      rows: [
        ["Nandini Apparels asks for a 5% discount", "2 days"],
        ["Surat Dye Chem wants a dye order approved", "1 day"],
        ["Shree Laxmi Garments promised payment by Friday", "6 days"],
      ],
    }),
    laterReply: "Later is fine. I'll work from your books and what you told me.",
  },

  books: {
    intro: [
      "Now your books. Then I can see who owes you, and how much.",
    ],
    card: {
      mark: "₹", title: "Your accounts", sub: "Pick where your books live",
      why: "I read ledgers and open bills. I never post an entry or change a voucher.",
      options: [
        { id: "tally", title: "Tally Prime", sub: "On the accounts PC" },
        { id: "zoho", title: "Zoho", sub: "Books or CRM, online" },
      ],
    },
    tally: {
      title: "Tally Prime", mark: "T", sub: "Read only · via the Aroh bridge",
      yes: ["Read ledgers, bills and outstanding", "Read stock items and sales orders"],
      no: ["Post or change a voucher", "See your bank passwords"],
      account: (c) => ["KAVERI-ACCTS", "Found on your office network"],
      steps: 1286, unit: "vouchers",
    },
    zoho: {
      title: "Zoho", mark: "Z", sub: "Read only · Books and CRM",
      yes: ["Read invoices, payments and deals", "Read your buyer list"],
      no: ["Create or edit an invoice", "Email your buyers"],
      steps: 940, unit: "records",
    },
    insight: (c) => ({
      kicker: "Money I found",
      big: "<strong>₹18,40,000 is overdue</strong> from 7 buyers. <strong>Two of them also have open orders.</strong>",
      rows: [
        ["Shree Laxmi Garments <span class='tag'>Open order</span>", "₹6,20,000 · 64 days"],
        ["Nandini Apparels <span class='tag'>Open order</span>", "₹4,80,000 · 41 days"],
        ["Om Sai Creations", "₹2,95,000 · 33 days"],
      ],
      after: c.email ? "Nandini is the buyer who asked you for a discount in email. I'll keep that in mind." : "",
    }),
    laterReply: "Later works. You can connect your books from this chat at any time.",
  },

  whatsapp: {
    intro: (c) => [
      "One more, and it's optional.",
      "You said decisions wait in WhatsApp. I can read only the groups you pick. Later is a full answer.",
    ],
    introPlain: [
      "One more, and it's optional.",
      "Your WhatsApp groups. I read only the groups you pick. Later is a full answer.",
    ],
    card: {
      mark: "W", title: "WhatsApp groups", sub: "Eleven groups found · pick any",
      why: "I read the groups you tick. I never post in a group, and I never read a chat you leave unticked.",
      groups: [
        ["Sales Team Surat", true], ["Dispatch Kaveri", true], ["Dyeing Unit", true],
        ["Accounts", false], ["Weaving Floor", false], ["Buyers: Mumbai", false],
        ["Buyers: Tiruppur", false], ["Transport", false], ["Samples", false],
        ["Mehta Family", false, true], ["School Friends", false, true],
      ],
      steps: 3, unit: "groups",
    },
    insight: (n) => ({
      kicker: "Waiting on you",
      big: `In ${n === 1 ? "that group" : `those ${n} groups`}, <strong>4 messages since Monday wait on you.</strong> The oldest is a price question from Nandini Apparels.`,
      rows: [],
    }),
    laterReply: "Later. Nothing breaks without it, and nothing gets worse.",
  },

  style: {
    ask: "Last question. When something needs you, do you want it short, or with the detail?",
    chips: ["Short", "With the detail"],
    short: "Short it is. One line each, and the source if you want it.",
    detail: "With the detail, then. The line, the numbers and the source.",
  },

  build: {
    ask: (c) => `Give me a moment${c.name ? ", " + c.name : ""}. I'm putting together your first brief.`,
    steps: (c) => [
      c.email ? "Reading 214 email threads" : "Using what you told me",
      c.books ? "Matching 7 overdue buyers to open orders" : "Looking for money that waits",
      c.whatsapp ? `Reading ${c.whatsappN} WhatsApp groups` : "Leaving WhatsApp for later",
      "Ranking what needs you today",
    ],
  },

  /* The three decisions. Each picks the best source that is connected. */
  brief: (c) => {
    const items = [];
    items.push({
      title: "Approve Nandini Apparels' 5% discount?",
      detail: c.books
        ? "They owe ₹4,80,000, 41 days late, and want 3,000 metres more. I suggest: approve the order once ₹2,00,000 comes in."
        : "They want 3,000 metres more at 5% less. Ask for the old dues first.",
      source: c.email ? ["Gmail", "Re: Revised rates for December"] : c.whatsapp ? ["WhatsApp", "Sales Team Surat"] : ["You", "Payments come late"],
      actions: [["Hold until paid", "I'll tell Nandini the order ships when ₹2,00,000 arrives."], ["Approve 5%", "Approved. I'll draft the reply for you to send."]],
    });
    items.push({
      title: c.books ? "Call Shree Laxmi Garments about ₹6,20,000" : "Chase the payment Shree Laxmi Garments promised",
      detail: c.books
        ? "64 days overdue, and they have a new order open. They promised Friday in email, and Friday is today."
        : "They promised payment by Friday. Friday is today.",
      source: c.books ? [c.booksName, "Outstanding, 64 days"] : c.email ? ["Gmail", "Payment by Friday"] : ["You", "Payments come late"],
      actions: [["Call now", "I put the call on your calendar for 10:30, with the ledger beside it."], ["Remind them", "I'll send a polite reminder from Accounts at 11:00."]],
    });
    items.push({
      title: "Approve a ₹1,35,000 dye order before Monday?",
      detail: "The Dyeing Unit runs out of reactive blue on Tuesday. Last month's price was 4% lower.",
      source: c.whatsapp ? ["WhatsApp", "Dyeing Unit"] : c.email ? ["Gmail", "Surat Dye Chem: PO 2291"] : c.books ? [c.booksName, "Purchase order pending"] : ["You", "Decisions wait on you"],
      actions: [["Approve", "Approved. I'll tell the Dyeing Unit."], ["Ask for 4% off", "I'll ask Surat Dye Chem to match last month's rate."]],
    });
    if (!c.email && !c.books && !c.whatsapp) items.forEach((it) => { it.source = ["a sample", "Connect a source to see your own"]; });
    return items;
  },
  lede: (c) => (c.email || c.books || c.whatsapp)
    ? "Three decisions need you today."
    : "Nothing is connected yet, so this is a sample from a business like yours.",

  close: (c) => [
    (c.email || c.books || c.whatsapp) ? "That's today. Three things, all from your own work." : "That's the shape of it. Connect one source, and tomorrow's brief is yours.",
    "Tomorrow's brief comes at 7:30, before your first call. Ask me anything in the meantime.",
  ],
  after: "In the full product I'd answer that from your data. This demo ends here.",
};
